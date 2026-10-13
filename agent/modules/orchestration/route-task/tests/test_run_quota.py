"""Quota parking, resume without redoing completed stages, wait mode, fail-closed behaviour."""
import tempfile
import unittest
from pathlib import Path

from route_task import config, quota, run
from tests.fakes import Agents, ScriptedAgent
from tests.gitrepo import make_repo
from tests.test_quota import Clock, Probe, T0, rl
from tests.test_run import BAD, OK, make_task
from tests.test_run_eval import FAIL_LOW, PASS


class QuotaRunBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.cfg = config.load(None)
        self.cfg.state_dir = self.root / "state"
        self.cfg.economy.trains_on_data = False
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, *readings):
        return quota.QuotaGate(self.cfg, Probe(*readings), now=self.clock)

    def go(self, oc, cl, gate, task=None, **kw):
        return run.run_task(task or make_task(), self.cfg, Agents(oc, cl), repo=self.repo, quota=gate, **kw)


class ParkingTest(QuotaRunBase):
    def test_evaluator_blocked_parks_run_after_the_worker_without_calling_claude(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent()
        r = self.go(oc, cl, self.gate(rl(0.90, 0.10)))
        s = r.state
        self.assertEqual(s["status"], "waiting_quota")
        self.assertEqual(s["next"], "eval")
        self.assertEqual(s["resume_after"], T0 + 3600)
        self.assertIn("five_hour", s["quota_reason"])
        self.assertEqual(len(oc.calls), 1)
        self.assertEqual(len(cl.calls), 0)
        self.assertIn("quota", (r.path / "report.md").read_text().lower())

    def test_premium_blocked_parks_without_a_full_access_call(self):
        self.cfg.evaluator.enabled = False
        oc, cl = ScriptedAgent(BAD, BAD), ScriptedAgent(OK)
        r = self.go(oc, cl, self.gate(rl(0.65, 0.10)))  # over the premium ceiling (0.60)
        self.assertEqual(r.state["status"], "waiting_quota")
        self.assertEqual(r.state["next"], "premium")
        self.assertEqual(len(cl.calls), 0)

    def test_no_quota_reading_fails_closed(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent(PASS)
        r = self.go(oc, cl, self.gate(None))
        self.assertEqual(r.state["status"], "waiting_quota")
        self.assertEqual(len(cl.calls), 0)
        self.assertIn("no quota reading", r.state["quota_reason"])

    def test_quota_from_the_evaluator_call_blocks_a_later_premium_escalation(self):
        high = {"rate_limit": rl(0.70, 0.10)}
        oc = ScriptedAgent(OK, OK)
        cl = ScriptedAgent({**FAIL_LOW, **high}, {**FAIL_LOW, **high}, OK)
        r = self.go(oc, cl, self.gate(rl(0.10, 0.10)))
        self.assertEqual(r.state["status"], "waiting_quota")
        self.assertEqual(r.state["next"], "premium")
        self.assertEqual(sum(c["mode"] == "full" for c in cl.calls), 0)

    def test_latest_reading_is_recorded_in_state(self):
        self.cfg.evaluator.enabled = False
        oc, cl = ScriptedAgent(BAD, BAD), ScriptedAgent({**OK, "rate_limit": rl(0.30, 0.20)})
        r = self.go(oc, cl, self.gate(rl(0.10, 0.10)))
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(r.state["quota"]["five_hour"]["utilization"], 0.30)

    def test_economy_only_run_never_touches_quota(self):
        self.cfg.evaluator.enabled = False
        probe = Probe(rl(0.99, 0.99))
        g = quota.QuotaGate(self.cfg, probe, now=self.clock)
        r = self.go(ScriptedAgent(OK), ScriptedAgent(), g)
        self.assertEqual(r.state["status"], "done")
        self.assertEqual(probe.calls, 0)


class ResumeTest(QuotaRunBase):
    def test_resume_continues_at_eval_without_redoing_the_worker(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent(PASS)
        r = self.go(oc, cl, self.gate(rl(0.90, 0.10)))
        self.assertEqual(r.state["status"], "waiting_quota")
        self.clock.t += 7200
        r2 = run.resume_task(r, self.cfg, Agents(oc, cl), quota=self.gate(rl(0.10, 0.10)))
        self.assertEqual(r2.state["status"], "done")
        self.assertEqual(len(oc.calls), 1)  # worker not re-run
        self.assertEqual(len(cl.calls), 1)

    def test_resume_while_still_over_the_ceiling_stays_parked(self):
        oc, cl = ScriptedAgent(OK), ScriptedAgent(PASS)
        r = self.go(oc, cl, self.gate(rl(0.90, 0.10)))
        r2 = run.resume_task(r, self.cfg, Agents(oc, cl), quota=self.gate(rl(0.90, 0.10)))
        self.assertEqual(r2.state["status"], "waiting_quota")
        self.assertEqual(len(cl.calls), 0)

    def test_resume_continues_a_parked_premium_stage_and_keeps_the_cap(self):
        self.cfg.evaluator.enabled = False
        oc, cl = ScriptedAgent(BAD, BAD), ScriptedAgent(OK)
        r = self.go(oc, cl, self.gate(rl(0.65, 0.10)))
        r2 = run.resume_task(r, self.cfg, Agents(oc, cl), quota=self.gate(rl(0.10, 0.10)))
        self.assertEqual(r2.state["status"], "done")
        self.assertEqual(len(oc.calls), 2)
        self.assertEqual(len(cl.calls), 1)
        self.assertEqual(r2.state["attempts"]["premium"], 1)


class WaitModeTest(QuotaRunBase):
    def test_wait_sleeps_until_the_window_resets_then_finishes(self):
        slept = []

        def sleep(s):
            slept.append(s)
            self.clock.t += s

        oc, cl = ScriptedAgent(OK), ScriptedAgent(PASS)
        r = self.go(oc, cl, self.gate(rl(0.90, 0.10), rl(0.10, 0.10)), wait=True, sleep=sleep, now=self.clock)
        self.assertEqual(r.state["status"], "done")
        self.assertGreaterEqual(sum(slept), 3600 - 1)
        self.assertEqual(len(oc.calls), 1)

    def test_kill_switch_interrupts_waiting(self):
        n = {"sleeps": 0}

        def sleep(s):
            n["sleeps"] += 1
            self.clock.t += s

        r = self.go(ScriptedAgent(OK), ScriptedAgent(PASS), self.gate(rl(0.90, 0.10)), wait=True, sleep=sleep,
                    now=self.clock, should_stop=lambda: n["sleeps"] >= 2)
        self.assertEqual(r.state["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
