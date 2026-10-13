"""Deterministic gate: the task's own commands (tests/lint) run in the worktree. Cheapest and most independent check."""
from dataclasses import dataclass, field

from .agents import run_process


@dataclass
class Check:
    cmd: str
    ok: bool
    returncode: int | None
    output: str
    timed_out: bool = False
    duration_s: float = 0.0


@dataclass
class GateResult:
    ok: bool
    checks: list = field(default_factory=list)

    def summary(self):
        lines = []
        for c in self.checks:
            lines.append(f"{'PASS' if c.ok else 'FAIL'}  {c.cmd}" + ("" if c.ok else f"  (exit {c.returncode}{', timeout' if c.timed_out else ''})"))
            if not c.ok and c.output:
                lines.append(c.output)
        return "\n".join(lines)


def run(commands, cwd, env, timeout, tail_lines=60, on_start=None):
    result = GateResult(ok=True)
    for cmd in commands:
        rc, out, err, timed_out, _, secs = run_process(["bash", "-c", cmd], cwd, env, timeout, on_start=on_start)
        tail = "\n".join((out + err).splitlines()[-tail_lines:])
        ok = rc == 0 and not timed_out
        result.checks.append(Check(cmd, ok, rc, tail, timed_out, secs))
        if not ok:
            result.ok = False
            break
    return result
