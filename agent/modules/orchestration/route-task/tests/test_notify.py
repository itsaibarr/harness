import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from route_task import config, notify, run
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_quota import Clock, Probe, rl
from tests.test_run import BAD, OK, make_task


class SendTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "osascript is macOS only")
    def test_builds_an_osascript_call_with_escaped_text(self):
        with mock.patch("route_task.notify.subprocess.run") as m:
            notify.send('Run "x"', 'done: \\ and "quotes"')
        argv = m.call_args.args[0]
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertIn('display notification "done: \\\\ and \\"quotes\\""', argv[2])
        self.assertIn('with title "Run \\"x\\""', argv[2])

    def test_never_raises_when_osascript_is_missing_or_slow(self):
        for exc in (OSError("no osascript"), __import__("subprocess").TimeoutExpired("osascript", 10)):
            with mock.patch("route_task.notify.subprocess.run", side_effect=exc):
                notify.send("t", "m")  # must not raise

    def test_noop_off_macos(self):
        with mock.patch("route_task.notify.sys.platform", "linux"), mock.patch("route_task.notify.subprocess.run") as m:
            notify.send("t", "m")
        m.assert_not_called()


class RunNotifyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False
        self.cfg.evaluator.enabled = False
        self.cfg.quota.enabled = False
        self.sent = []

    def tearDown(self):
        self._tmp.cleanup()

    def go(self, oc, cl, **kw):
        return run.run_task(make_task(), self.cfg, Agents(oc, cl), repo=self.repo,
                            notify=lambda title, msg: self.sent.append((title, msg)), **kw)

    def test_done_notifies_once_with_title_and_status(self):
        self.go(ScriptedAgent(OK), ScriptedAgent())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("done", self.sent[0][1])
        self.assertIn("Make out.txt good", self.sent[0][0] + self.sent[0][1])

    def test_needs_human_notifies(self):
        self.go(ScriptedAgent(BAD, BAD), ScriptedAgent(BAD))
        self.assertIn("needs_human", self.sent[0][1])

    def test_parked_run_notifies_with_resume_info(self):
        self.cfg.quota.enabled = True
        self.cfg.evaluator.enabled = True
        g = __import__("route_task.quota", fromlist=["QuotaGate"]).QuotaGate(self.cfg, Probe(rl(0.9, 0.1)), now=Clock())
        self.go(ScriptedAgent(OK), ScriptedAgent(), quota=g)
        self.assertIn("waiting_quota", self.sent[0][1])
        self.assertIn("resume", self.sent[0][1].lower())

    def test_stopped_run_does_not_notify(self):
        self.go(ScriptedAgent(BAD, OK), ScriptedAgent(), should_stop=lambda: True)
        self.assertEqual(self.sent, [])

    def test_a_failing_notifier_never_breaks_the_run(self):
        def boom(t, m):
            raise RuntimeError("notifier down")
        r = run.run_task(make_task(), self.cfg, Agents(ScriptedAgent(OK), ScriptedAgent()), repo=self.repo, notify=boom)
        self.assertEqual(r.state["status"], "done")

    def test_no_notifier_by_default(self):
        r = run.run_task(make_task(), self.cfg, Agents(ScriptedAgent(OK), ScriptedAgent()), repo=self.repo)
        self.assertEqual(r.state["status"], "done")


if __name__ == "__main__":
    unittest.main()
