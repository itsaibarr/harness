import threading
import tempfile
import time
import unittest
from pathlib import Path

from route_task import agents, batch, config, quota, run
from route_task.agents import StageResult
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_quota import Probe, rl
from tests.test_run import OK, make_task


class Tracker:
    """Fake agent recording peak concurrency."""

    def __init__(self, delay=0.3, writes=None):
        self.delay, self.writes = delay, writes or {}
        self.now = self.peak = 0
        self.calls = 0
        self.lock = threading.Lock()

    def run(self, prompt, cwd, model, **kw):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.calls += 1
        time.sleep(self.delay)
        for rel, text in self.writes.items():
            (Path(cwd) / rel).write_text(text)
        with self.lock:
            self.now -= 1
        return StageResult(ok=True, text="done", session_id="s", duration_s=self.delay)


class LockedAgentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self._tmp.name) / "claude.lock"

    def tearDown(self):
        self._tmp.cleanup()

    def test_serialises_concurrent_calls(self):
        inner = Tracker(delay=0.15)
        locked = agents.LockedAgent(inner, self.lock, poll=0.02)
        ts = [threading.Thread(target=lambda: locked.run("p", self._tmp.name, model="m")) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(inner.calls, 4)
        self.assertEqual(inner.peak, 1)

    def test_waiting_for_the_lock_honours_the_kill_switch(self):
        inner = Tracker(delay=1.0)
        locked = agents.LockedAgent(inner, self.lock, poll=0.02)
        holder = threading.Thread(target=lambda: locked.run("p", self._tmp.name, model="m"))
        holder.start()
        time.sleep(0.1)
        t0 = time.time()
        r = locked.run("p", self._tmp.name, model="m", should_stop=lambda: True)
        self.assertTrue(r.stopped)
        self.assertLess(time.time() - t0, 0.5)
        holder.join()
        self.assertEqual(inner.calls, 1)


class QuotaThreadSafetyTest(unittest.TestCase):
    def test_concurrent_checks_probe_once(self):
        calls = []

        def slow_probe():
            calls.append(1)
            time.sleep(0.2)
            return rl(0.1, 0.1)

        g = quota.QuotaGate(config.load(None), slow_probe)
        ts = [threading.Thread(target=lambda: g.check("evaluator")) for _ in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(calls), 1)


class BatchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False
        self.cfg.evaluator.enabled = False
        self.cfg.quota.enabled = False

    def tearDown(self):
        self._tmp.cleanup()

    def tasks(self, n):
        return [make_task() for _ in range(n)]

    def test_jobs_run_tasks_concurrently_in_separate_worktrees_of_the_same_repo(self):
        oc = Tracker(delay=0.3, writes={"out.txt": "good\n"})
        runs = batch.run_batch(self.tasks(4), self.cfg, Agents(oc, ScriptedAgent()), jobs=4, repo=self.repo)
        self.assertEqual([r.state["status"] for r in runs], ["done"] * 4)
        self.assertEqual(oc.peak, 4)
        self.assertEqual(len({r.state["worktree"] for r in runs}), 4)
        self.assertEqual(len({r.state["branch"] for r in runs}), 4)

    def test_jobs_one_is_sequential(self):
        oc = Tracker(delay=0.1, writes={"out.txt": "good\n"})
        runs = batch.run_batch(self.tasks(3), self.cfg, Agents(oc, ScriptedAgent()), jobs=1, repo=self.repo)
        self.assertEqual(oc.peak, 1)
        self.assertEqual([r.state["status"] for r in runs], ["done"] * 3)

    def test_jobs_cap_is_respected(self):
        oc = Tracker(delay=0.2, writes={"out.txt": "good\n"})
        batch.run_batch(self.tasks(6), self.cfg, Agents(oc, ScriptedAgent()), jobs=2, repo=self.repo)
        self.assertEqual(oc.peak, 2)

    def test_results_keep_task_order(self):
        ts = [make_task() for _ in range(3)]
        for i, t in enumerate(ts):
            t.title = f"task {i}"
        oc = Tracker(delay=0.05, writes={"out.txt": "good\n"})
        runs = batch.run_batch(ts, self.cfg, Agents(oc, ScriptedAgent()), jobs=3, repo=self.repo)
        self.assertEqual([r.state["task"]["title"] for r in runs], ["task 0", "task 1", "task 2"])

    def test_one_crashing_task_does_not_take_down_the_others(self):
        class Boom:
            def __init__(self):
                self.n = 0

            def run(self, prompt, cwd, model, **kw):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("provider exploded")
                (Path(cwd) / "out.txt").write_text("good\n")
                return StageResult(ok=True, text="done", duration_s=0.01)

        runs = batch.run_batch(self.tasks(3), self.cfg, Agents(Boom(), ScriptedAgent()), jobs=1, repo=self.repo)
        statuses = sorted(r.state["status"] for r in runs)
        self.assertEqual(statuses, ["done", "done", "failed"])
        failed = next(r for r in runs if r.state["status"] == "failed")
        self.assertIn("provider exploded", failed.state["error"])


if __name__ == "__main__":
    unittest.main()
