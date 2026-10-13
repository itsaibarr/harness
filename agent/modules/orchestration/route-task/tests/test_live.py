"""Opt-in live smoke test: real `opencode` + real `claude` (Haiku stands in for Sonnet/Opus to keep quota use negligible).
Run: RT_LIVE=1 python3 -m unittest tests.test_live -v   (from the module dir). Spends a few tokens; never touches your repos."""
import os
import tempfile
import unittest
from pathlib import Path

from route_task import config, guard, run
from route_task.agents import Agents, ClaudeAgent, OpenCodeAgent
from tests.gitrepo import make_repo
from tests.test_run import make_task


@unittest.skipUnless(os.environ.get("RT_LIVE") == "1", "set RT_LIVE=1 to spend a few real tokens")
class LiveSmokeTest(unittest.TestCase):
    def test_forced_escalation_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = make_repo(root)
            cfg = config.load(None)
            cfg.state_dir = root / "state"
            cfg.economy.model = "opencode/this-model-does-not-exist"  # forces the worker to fail -> premium
            cfg.economy.trains_on_data = False
            cfg.evaluator.model = cfg.premium.model = "haiku"
            cfg.limits.worker_attempts = 1
            env = guard.child_env(cfg)
            t = make_task(gate=('grep -qx "hello from route-task" hello.txt',))
            t.body = "Create hello.txt in the repository root containing exactly one line: hello from route-task"
            r = run.run_task(t, cfg, Agents(OpenCodeAgent(env), ClaudeAgent(env)), repo=repo)
            self.assertEqual(r.state["status"], "done", (r.path / "report.md").read_text())
            report = (r.path / "report.md").read_text()
            self.assertIn("Premium used: yes", report)
            self.assertIn("eval passed", report)


if __name__ == "__main__":
    unittest.main()
