import unittest

from route_task import config, quota

WEEK = 7 * 86400
T0 = 1_000_000_000.0


def rl(h, d, h_reset=T0 + 3600, d_reset=T0 + 3 * 86400):
    return {"five_hour": {"utilization": h, "resetsAt": h_reset}, "seven_day": {"utilization": d, "resetsAt": d_reset}}


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Probe:
    def __init__(self, *readings):
        self.readings, self.calls = list(readings), 0

    def __call__(self):
        self.calls += 1
        return self.readings.pop(0) if self.readings else None


def gate(probe, now=None, **q):
    c = config.load(None)
    for k, v in q.items():
        setattr(c.quota, k, v)
    return quota.QuotaGate(c, probe, now=now or Clock())


class CeilingTest(unittest.TestCase):
    def test_low_usage_allows_both_roles(self):
        g = gate(Probe(rl(0.10, 0.10), rl(0.10, 0.10)))
        self.assertTrue(g.check("evaluator").allowed)
        self.assertTrue(g.check("premium").allowed)

    def test_five_hour_over_evaluator_ceiling_blocks_until_that_window_resets(self):
        d = gate(Probe(rl(0.80, 0.10))).check("evaluator")
        self.assertFalse(d.allowed)
        self.assertIn("five_hour", d.reason)
        self.assertEqual(d.resume_after, T0 + 3600)

    def test_premium_ceiling_is_stricter_than_evaluator(self):
        reading = rl(0.65, 0.10)
        self.assertTrue(gate(Probe(reading)).check("evaluator").allowed)
        self.assertFalse(gate(Probe(reading)).check("premium").allowed)

    def test_seven_day_over_ceiling_blocks_until_weekly_reset(self):
        # mid-week so pacing is not what blocks
        now = Clock(T0 + 3 * 86400)
        d = gate(Probe(rl(0.10, 0.85, d_reset=T0 + 4 * 86400)), now=now).check("premium")
        self.assertFalse(d.allowed)
        self.assertIn("seven_day", d.reason)
        self.assertEqual(d.resume_after, T0 + 4 * 86400)

    def test_exactly_at_ceiling_is_blocked(self):
        self.assertFalse(gate(Probe(rl(0.75, 0.10))).check("evaluator").allowed)


class PacingTest(unittest.TestCase):
    def setUp(self):
        self.reset = T0 + WEEK  # window just started at T0
        self.now = Clock(T0 + 0.2 * WEEK)

    def test_ahead_of_pace_is_blocked_with_resume_when_pace_catches_up(self):
        g = gate(Probe(rl(0.10, 0.50, d_reset=self.reset)), now=self.now)
        d = g.check("evaluator")
        self.assertFalse(d.allowed)
        self.assertIn("pacing", d.reason)
        self.assertAlmostEqual(d.resume_after, T0 + (0.50 - 0.15) * WEEK, places=3)

    def test_on_pace_is_allowed(self):
        g = gate(Probe(rl(0.10, 0.30, d_reset=self.reset)), now=self.now)
        self.assertTrue(g.check("evaluator").allowed)

    def test_pacing_can_be_disabled(self):
        g = gate(Probe(rl(0.10, 0.50, d_reset=self.reset)), now=self.now, pacing=False)
        self.assertTrue(g.check("evaluator").allowed)

    def test_slack_is_configurable(self):
        g = gate(Probe(rl(0.10, 0.50, d_reset=self.reset)), now=self.now, pacing_slack=0.40)
        self.assertTrue(g.check("evaluator").allowed)


class ReadingTest(unittest.TestCase):
    def test_no_reading_fails_closed_and_retries_later(self):
        now = Clock()
        d = gate(Probe(None), now=now).check("evaluator")
        self.assertFalse(d.allowed)
        self.assertIn("no quota reading", d.reason)
        self.assertEqual(d.resume_after, T0 + 300)

    def test_malformed_reading_fails_closed(self):
        d = gate(Probe({"five_hour": {"utilization": "lots"}})).check("evaluator")
        self.assertFalse(d.allowed)

    def test_cache_is_used_within_ttl_and_refreshed_after(self):
        now, probe = Clock(), Probe(rl(0.1, 0.1), rl(0.1, 0.1))
        g = gate(probe, now=now)
        g.check("evaluator")
        now.t += 30
        g.check("evaluator")
        self.assertEqual(probe.calls, 1)
        now.t += 61
        g.check("evaluator")
        self.assertEqual(probe.calls, 2)

    def test_update_from_a_real_call_refreshes_cache_without_probing(self):
        now, probe = Clock(), Probe()
        g = gate(probe, now=now)
        g.update(rl(0.2, 0.2))
        self.assertTrue(g.check("evaluator").allowed)
        self.assertEqual(probe.calls, 0)

    def test_update_with_none_keeps_previous_reading(self):
        g = gate(Probe(), now=Clock())
        g.update(rl(0.2, 0.2))
        g.update(None)
        self.assertEqual(g.last["five_hour"]["utilization"], 0.2)

    def test_disabled_never_probes_and_always_allows(self):
        probe = Probe()
        g = gate(probe, enabled=False)
        self.assertTrue(g.check("premium").allowed)
        self.assertEqual(probe.calls, 0)

    def test_last_reading_is_exposed_for_state(self):
        g = gate(Probe(rl(0.1, 0.2)))
        g.check("evaluator")
        self.assertEqual(g.last["seven_day"]["utilization"], 0.2)


if __name__ == "__main__":
    unittest.main()
