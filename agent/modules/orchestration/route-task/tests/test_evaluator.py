import tempfile
import unittest
from pathlib import Path

from route_task import evaluator, task
from tests.fakes import ScriptedAgent

T = task.parse("---\ntitle: Add retry\nacceptance:\n  - retries 3x on 5xx\n  - tests pass\n---\nImplement retry with backoff in fetch().")
GOOD = {"pass": True, "issues": [], "needs_premium": False, "reason": "meets acceptance"}
BAD = {"pass": False, "issues": [{"severity": "high", "file": "a.py", "note": "no backoff"}], "needs_premium": True, "reason": "design flaw"}


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def ev(self, agent, diff="diff --git a/a.py b/a.py\n+retry()", gate="PASS  pytest"):
        return evaluator.evaluate(agent, T, self.wt, diff, gate, model="sonnet", timeout=10)

    def test_prompt_has_task_acceptance_diff_gate_and_ignores_handoff_dir(self):
        agent = ScriptedAgent({"structured": GOOD})
        self.ev(agent)
        p = agent.calls[0]["prompt"]
        for needle in ("Implement retry with backoff", "retries 3x on 5xx", "diff --git a/a.py", "PASS  pytest", ".route-task"):
            self.assertIn(needle, p)

    def test_call_is_readonly_with_schema_model_and_worktree_cwd(self):
        agent = ScriptedAgent({"structured": GOOD})
        self.ev(agent)
        c = agent.calls[0]
        self.assertEqual(c["mode"], "readonly")
        self.assertEqual(c["model"], "sonnet")
        self.assertEqual(c["cwd"], self.wt)
        self.assertEqual(sorted(c["schema"]["required"]), ["issues", "needs_premium", "pass", "reason"])

    def test_pass_verdict(self):
        v = self.ev(ScriptedAgent({"structured": GOOD}))
        self.assertTrue(v.passed)
        self.assertFalse(v.call_failed)
        self.assertFalse(v.needs_premium)

    def test_fail_verdict_carries_issues_and_premium_hint(self):
        v = self.ev(ScriptedAgent({"structured": BAD}))
        self.assertFalse(v.passed)
        self.assertTrue(v.needs_premium)
        self.assertEqual(v.issues[0]["note"], "no backoff")
        self.assertTrue(v.high_severity)

    def test_malformed_verdict_is_retried_once_then_accepted(self):
        agent = ScriptedAgent({"structured": {"pass": "yes"}}, {"structured": GOOD})
        v = self.ev(agent)
        self.assertEqual(len(agent.calls), 2)
        self.assertTrue(v.passed)

    def test_two_malformed_verdicts_fail_safe(self):
        agent = ScriptedAgent({"structured": None}, {"structured": {"pass": True}})  # second is missing fields
        v = self.ev(agent)
        self.assertEqual(len(agent.calls), 2)
        self.assertFalse(v.passed)
        self.assertTrue(v.malformed)

    def test_agent_call_failure_is_not_a_verdict(self):
        agent = ScriptedAgent({"ok": False, "error": "rate limited"})
        v = self.ev(agent)
        self.assertTrue(v.call_failed)
        self.assertFalse(v.passed)
        self.assertEqual(len(agent.calls), 1)  # no retry on infrastructure failure
        self.assertIn("rate limited", v.error)

    def test_huge_diff_is_truncated_with_marker(self):
        agent = ScriptedAgent({"structured": GOOD})
        self.ev(agent, diff="+x\n" * 100000)
        p = agent.calls[0]["prompt"]
        self.assertLess(len(p), 80000)
        self.assertIn("[diff truncated", p)

    def test_rate_limit_from_the_call_is_exposed(self):
        rl = {"five_hour": {"utilization": 0.3, "resetsAt": 1}, "seven_day": {"utilization": 0.1, "resetsAt": 2}}
        v = self.ev(ScriptedAgent({"structured": GOOD, "rate_limit": rl}))
        self.assertEqual(v.rate_limit, rl)


if __name__ == "__main__":
    unittest.main()
