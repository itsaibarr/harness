"""report.md: what was completed, which agents ran, what was verified, what is unresolved, whether premium was used."""
from datetime import datetime


def resume_note(ts, reason=""):
    when = f"{datetime.fromtimestamp(ts):%a %H:%M}" if ts else "unknown"
    return f"resume after {when}" + (f": {reason}" if reason else "")


def render(state):
    t = state["task"]
    hist = state.get("history", [])
    premium = any(h["stage"] == "premium" for h in hist)
    status = state["status"]
    lines = [f"# Report: {t['title']}", "", f"Status: **{status}**", ""]
    if state.get("branch"):
        lines += [f"Branch: `{state['branch']}`  Worktree: `{state['worktree']}`",
                  "Nothing was pushed or merged. Review the branch (e.g. in T3 Code) and push it yourself.", ""]
    lines += ["## Agents used", ""]
    lines += [f"- {h['stage']}: {h['agent']} / {h['model']} ({h['duration_s']:.0f}s) -> {h['outcome']}" for h in hist] or ["- none"]
    lines += ["", "## Verification", ""]
    if not t.get("gate"):
        lines.append("- No gate commands were defined; the result was not machine-verified.")
    for h in hist:
        if h.get("gate"):
            lines += [f"- after {h['stage']} attempt: {h['gate'].splitlines()[0] if h['gate'] else ''}"]
    if any(h["stage"] == "eval" for h in hist):
        lines += [f"- evaluator: {h['outcome']}" for h in hist if h["stage"] == "eval"]
    lines += ["", f"Premium used: {'yes' if premium else 'no'}", "", "## Unresolved", ""]
    if status == "done":
        lines.append("- none known")
    elif status == "needs_human":
        lines.append("- This run needs a human: automated attempts were exhausted. See the last gate output in `events.jsonl` / the worktree handoff.")
        if state.get("error"):
            lines.append(f"- {state['error']}")
    elif status == "waiting_quota":
        lines.append(f"- Parked until the Claude quota window allows it ({resume_note(state.get('resume_after'), state.get('quota_reason', ''))}).")
    elif status == "stopped":
        lines.append("- Stopped by the kill switch before completion.")
    else:
        lines.append(f"- Failed: {state.get('error', 'unknown error')}")
    return "\n".join(lines) + "\n"


def write(run):
    (run.path / "report.md").write_text(render(run.state))
