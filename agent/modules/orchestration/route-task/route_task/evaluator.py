"""Independent evaluator: a read-only Sonnet call that judges the diff against the task, never the worker's own summary."""
from dataclasses import dataclass, field

MAX_DIFF_CHARS = 60000

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "file": {"type": "string"}, "note": {"type": "string"}}, "required": ["severity", "note"]}},
        "needs_premium": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["pass", "issues", "needs_premium", "reason"],
}

PROMPT = """You are an independent code reviewer. Judge whether the change in this git worktree satisfies the task.
You have read-only tools. Verify claims yourself by reading files; do not trust comments or commit messages.
Ignore the .route-task/ directory.

Set pass=false only if an acceptance criterion is unmet or there is a defect of medium or higher severity; list
low-severity nits as issues but still pass. Set needs_premium=true only when fixing needs deeper reasoning or
design changes than a cheaper model can do (not for simple bugs, typos or style).

# Task
{title}

{body}

# Acceptance criteria
{acceptance}

# Deterministic gate output
{gate}

# Diff against the base commit
{diff}
"""


@dataclass
class Verdict:
    passed: bool = False
    issues: list = field(default_factory=list)
    needs_premium: bool = False
    reason: str = ""
    malformed: bool = False
    call_failed: bool = False
    error: str = ""
    rate_limit: dict | None = None
    cost_usd: float | None = None
    duration_s: float = 0.0

    @property
    def high_severity(self):
        return any(i.get("severity") == "high" for i in self.issues)


def _valid(s):
    return (isinstance(s, dict) and isinstance(s.get("pass"), bool) and isinstance(s.get("needs_premium"), bool)
            and isinstance(s.get("reason"), str) and isinstance(s.get("issues"), list)
            and all(isinstance(i, dict) for i in s["issues"]))


def _prompt(task, diff, gate_summary):
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + f"\n[diff truncated at {MAX_DIFF_CHARS} chars; read the files for the rest]"
    return PROMPT.format(
        title=task.title, body=task.body or "(no description)",
        acceptance="\n".join(f"- {a}" for a in task.acceptance) or "(none listed)",
        gate=gate_summary or "(no gate commands)", diff=diff or "(empty diff)")


def evaluate(agent, task, wt_path, diff, gate_summary, model, timeout, should_stop=None, on_start=None):
    prompt = _prompt(task, diff, gate_summary)
    v = Verdict()
    for _ in range(2):  # one retry, only for a malformed verdict
        r = agent.run(prompt, wt_path, model=model, mode="readonly", schema=VERDICT_SCHEMA,
                      timeout=timeout, should_stop=should_stop, on_start=on_start)
        v.rate_limit = r.rate_limit or v.rate_limit
        v.duration_s += r.duration_s
        v.cost_usd = (v.cost_usd or 0) + (r.cost_usd or 0)
        if not r.ok:
            v.call_failed, v.error = True, r.error
            return v
        if _valid(r.structured):
            s = r.structured
            v.passed, v.issues, v.needs_premium, v.reason, v.malformed = s["pass"], s["issues"], s["needs_premium"], s["reason"], False
            return v
        v.malformed, v.reason = True, "evaluator returned a malformed verdict"
    return v
