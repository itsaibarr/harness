"""Evaluator wiring: retry with feedback, escalation rules, premium-once, evaluator infrastructure failures."""
import tempfile
import unittest
from pathlib import Path

from route_task import config, run, task
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_run import BAD, OK, make_task

PASS = {"structured": {"pass": True, "issues": [], "needs_premium": False, "reason": "ok"}}
FAIL_LOW = {"structured": {"pass": False, "issues": [{"severity": "medium", "file": "out.txt", "note": "wording"}],
                           "needs_premium": False, "reason": "meh"}}
FAIL_HARD = {"structured": {"pass": False, "issues": [{"severity": "high", "file": "out.txt", "note": "wrong design"}],
                            "needs_premium": True, "reason": "needs deeper reasoning"}}


class EvalBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False
        self.cfg.quota.enabled = False

    def tearDown(self):
        self._tmp.cleanup()

    def go(self, t, oc, cl):
        return run.run_task(t, self.cfg, Agents(oc, cl), repo=self.repo)


class EvaluatorFlowTest(EvalBase):
    def test_pass_means_zero_premium_and_one_sonnet_call(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent(PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual(len(cl.calls), 1)
        self.assertEqual(cl.calls[0]["model"], "sonnet")
        self.assertEqual(cl.calls[0]["mode"], "readonly")
        self.assertIn("Premium used: no", (r.path / "report.md").read_text())

    def test_gate_failure_skips_the_evaluator_entirely(self):
        oc, cl = ScriptedAgent(BAD, OK), ScriptedAgent(PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(cl.calls), 1)  # only the evaluation after the passing gate

    def test_eval_fail_feeds_issues_back_to_next_worker_attempt(self):
        oc, cl = ScriptedAgent(OK, OK), ScriptedAgent(FAIL_LOW, PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 2)
        self.assertEqual(len(cl.calls), 2)
        handoff = (Path(r.state["worktree"]) / ".route-task" / "handoff.md").read_text()
        self.assertIn("wording", handoff)
        self.assertIn("medium", handoff)

    def test_exhausted_worker_attempts_escalate_to_one_premium_then_one_eval(self):
        oc = ScriptedAgent(OK, OK)
        cl = ScriptedAgent(FAIL_LOW, FAIL_LOW, OK, PASS)  # eval, eval, premium, eval
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual([c["mode"] for c in cl.calls], ["readonly", "readonly", "full", "readonly"])
        self.assertEqual(cl.calls[2]["model"], "opus")
        self.assertIn("Premium used: yes", (r.path / "report.md").read_text())

    def test_high_severity_needs_premium_escalates_immediately_skipping_remaining_worker_attempts(self):
        oc = ScriptedAgent(OK, OK)
        cl = ScriptedAgent(FAIL_HARD, OK, PASS)  # eval, premium, eval
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual([c["mode"] for c in cl.calls], ["readonly", "full", "readonly"])

    def test_needs_premium_without_high_severity_does_not_escalate_early(self):
        soft = {"structured": {"pass": False, "issues": [{"severity": "medium", "file": "x", "note": "n"}],
                               "needs_premium": True, "reason": "r"}}
        oc, cl = ScriptedAgent(OK, OK), ScriptedAgent(soft, PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(len(oc.calls), 2)
        self.assertEqual(r.state["status"], "done")

    def test_premium_result_failing_eval_ends_needs_human_never_a_second_premium(self):
        oc = ScriptedAgent(OK, OK)
        cl = ScriptedAgent(FAIL_LOW, FAIL_LOW, OK, FAIL_LOW, OK, PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "needs_human")
        self.assertEqual(sum(c["mode"] == "full" for c in cl.calls), 1)

    def test_evaluator_infrastructure_failure_parks_for_a_human_without_burning_premium(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent({"ok": False, "error": "rate limited"})
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "needs_human")
        self.assertEqual(sum(c["mode"] == "full" for c in cl.calls), 0)
        self.assertIn("rate limited", r.state["error"])
        self.assertIn("evaluator", (r.path / "report.md").read_text().lower())

    def test_malformed_verdicts_are_an_evaluator_problem_not_a_code_failure(self):
        junk = {"structured": {"nope": 1}}
        oc, cl = ScriptedAgent(OK, OK), ScriptedAgent(junk, junk, PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "needs_human")
        self.assertEqual(len(oc.calls), 1)  # no worker attempt burned, no premium
        self.assertEqual(sum(c["mode"] == "full" for c in cl.calls), 0)
        self.assertIn("malformed", r.state["error"])

    def test_premium_tier_runs_premium_then_a_single_eval(self):
        oc, cl = ScriptedAgent(), ScriptedAgent(OK, PASS)
        r = self.go(make_task(tier="premium"), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(oc.calls), 0)
        self.assertEqual([c["mode"] for c in cl.calls], ["full", "readonly"])

    def test_evaluator_can_be_disabled(self):
        self.cfg.evaluator.enabled = False
        oc, cl = ScriptedAgent(OK), ScriptedAgent()
        r = self.go(make_task(), oc, cl)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(len(cl.calls), 0)

    def test_eval_entries_appear_in_history_and_report(self):
        r = self.go(make_task(), ScriptedAgent(OK), ScriptedAgent(PASS))
        stages = [h["stage"] for h in r.state["history"]]
        self.assertEqual(stages, ["worker", "eval"])
        self.assertIn("evaluator", (r.path / "report.md").read_text().lower())

    def test_single_worker_attempt_config_escalates_after_one(self):
        self.cfg.limits.worker_attempts = 1
        oc, cl = ScriptedAgent(OK), ScriptedAgent(FAIL_LOW, OK, PASS)
        r = self.go(make_task(), oc, cl)
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual(r.state["status"], "done")


if __name__ == "__main__":
    unittest.main()
