"""Run several tasks concurrently: one worktree + one thread per task, bounded by `jobs`."""
from concurrent.futures import ThreadPoolExecutor

from . import run


def run_batch(tasks, cfg, agents, jobs=1, repo=None, should_stop=None, wait=False, quota=None, is_public=None, notify=None):
    quota = quota or run.default_quota(cfg, agents)  # one shared, thread-safe gate for the whole batch
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futures = [ex.submit(run.run_task, t, cfg, agents, repo=repo, should_stop=should_stop, wait=wait,
                             quota=quota, is_public=is_public, notify=notify) for t in tasks]
        return [f.result() for f in futures]
