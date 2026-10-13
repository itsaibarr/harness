"""Run several tasks concurrently: one worktree + one thread per task, bounded by `jobs`.

Quota-parked tasks release their slot immediately (so queued tasks that need no Claude can still run); with `wait`
the batch then sleeps once until the earliest resume time and resumes all parked runs together."""
import time
from concurrent.futures import ThreadPoolExecutor

from . import run

WAIT_CHUNK_S = 30
MIN_CYCLE_S = 60  # never re-probe quota more often than this, even if a reset time is already in the past


def run_batch(tasks, cfg, agents, jobs=1, repo=None, should_stop=None, wait=False, quota=None, is_public=None,
              notify=None, sleep=time.sleep, now=time.time):
    quota = quota or run.default_quota(cfg, agents)  # one shared, thread-safe gate for the whole batch
    should_stop = should_stop or (lambda: False)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        runs = list(ex.map(lambda t: run.run_task(t, cfg, agents, repo=repo, should_stop=should_stop, quota=quota,
                                                  is_public=is_public, notify=notify), tasks))
        while wait:
            parked = [i for i, r in enumerate(runs) if r.state["status"] == "waiting_quota"]
            if not parked or should_stop():
                break
            until = max(min(runs[i].state["resume_after"] for i in parked), now() + MIN_CYCLE_S)
            while now() < until:
                if should_stop():
                    return runs
                sleep(min(WAIT_CHUNK_S, until - now()))
            quota.invalidate()  # readings predate the wait
            resumed = ex.map(lambda i: run.resume_task(runs[i], cfg, agents, should_stop=should_stop, quota=quota,
                                                       notify=notify), parked)
            for i, r in zip(parked, list(resumed)):
                runs[i] = r
    return runs
