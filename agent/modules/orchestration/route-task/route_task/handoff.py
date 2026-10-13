"""handoff.md: everything the next agent needs, so it reads the worktree instead of re-analysing the repo."""
from pathlib import Path


def _bullets(items, fmt):
    return "\n".join(f"- {fmt(i)}" for i in items) or "(none yet)"


def render(task, attempts, diff_stat, gate_output, issues, files, next_step):
    gate_tail = "\n".join(gate_output.splitlines()[-60:]) or "(none yet)"
    return f"""# Handoff: {task.title}

## Task
{task.body or '(no description)'}

## Acceptance
{_bullets(task.acceptance, str)}

## Gate commands (must exit 0)
{_bullets(task.gate, str)}

## Attempts so far
{_bullets(attempts, lambda a: f"{a['stage']}: {a['agent']} / {a['model']} ({a['duration_s']:.0f}s) -> {a['outcome']}")}

## Diff so far
```
{diff_stat.strip() or '(no changes yet)'}
```

## Gate output (last 60 lines)
```
{gate_tail}
```

## Evaluator issues
{_bullets(issues, lambda i: f"[{i.get('severity', '?')}] {i.get('file', '')}: {i.get('note', '')}")}

## Files touched
{_bullets(files, str)}

## Next
{next_step}
"""


def write(worktree_path, text):
    p = Path(worktree_path) / ".route-task" / "handoff.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    return p
