"""Usage-left gate for Claude calls. Works only on observed utilization fractions (from Claude's `rate_limit_event`);
it never converts utilization into tokens or dollars (not observable) and fails closed when there is no reading."""
import threading
import time
from dataclasses import dataclass

WEEK = 7 * 86400
RETRY_NO_READING_S = 300


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    resume_after: float | None = None


def _parse(rl):
    try:
        h, d = rl["five_hour"], rl["seven_day"]
        return float(h["utilization"]), float(h["resetsAt"]), float(d["utilization"]), float(d["resetsAt"])
    except (KeyError, TypeError, ValueError):
        return None


class QuotaGate:
    def __init__(self, cfg, probe, now=time.time):
        self.cfg, self.probe, self.now = cfg, probe, now
        self.last, self.at = None, 0.0
        self._lock = threading.RLock()

    def update(self, rate_limit):
        with self._lock:
            if rate_limit and _parse(rate_limit):
                self.last, self.at = rate_limit, self.now()

    def invalidate(self):
        """Forget the cached reading (e.g. after sleeping through a window) so the next check re-probes."""
        with self._lock:
            self.at = 0.0

    def _fresh(self):
        return self.last is not None and self.now() - self.at <= self.cfg.quota.probe_ttl_s

    def check(self, role):
        with self._lock:
            return self._check(role)

    def _check(self, role):
        q = self.cfg.quota
        if not q.enabled:
            return Decision(True)
        if not self._fresh():
            self.update(self.probe())
        now = self.now()
        if not self._fresh():
            return Decision(False, "no quota reading available (failing closed)", now + RETRY_NO_READING_S)
        h_util, h_reset, d_util, d_reset = _parse(self.last)
        c5, c7 = (q.evaluator_5h, q.evaluator_7d) if role == "evaluator" else (q.premium_5h, q.premium_7d)
        if h_util >= c5:
            return Decision(False, f"five_hour utilization {h_util:.2f} >= {role} ceiling {c5:.2f}", h_reset)
        if d_util >= c7:
            return Decision(False, f"seven_day utilization {d_util:.2f} >= {role} ceiling {c7:.2f}", d_reset)
        if q.pacing:
            start = d_reset - WEEK
            elapsed = min(max((now - start) / WEEK, 0.0), 1.0)
            if d_util > elapsed + q.pacing_slack:
                catch_up = start + (d_util - q.pacing_slack) * WEEK
                return Decision(False, f"weekly pacing: used {d_util:.2f} > elapsed {elapsed:.2f} + slack {q.pacing_slack:.2f}",
                                min(catch_up, d_reset))
        return Decision(True)
