"""Scripted fake agents for state-machine tests (the model is the only thing faked; git, gate and files are real)."""
from pathlib import Path

from route_task.agents import StageResult


class ScriptedAgent:
    """Each step: dict(writes={relpath: text}, ok=True, error='', structured=None, text='done', rate_limit=None)."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def run(self, prompt, cwd, model, **kw):
        self.calls.append({"prompt": prompt, "cwd": Path(cwd), "model": model, **kw})
        step = self.steps.pop(0) if self.steps else {}
        for rel, text in step.get("writes", {}).items():
            p = Path(cwd) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        ok = step.get("ok", True)
        return StageResult(ok=ok, text=step.get("text", "done"), session_id=step.get("session", "s1"),
                           error=step.get("error", "" if ok else "agent failed"), duration_s=0.1,
                           structured=step.get("structured"), rate_limit=step.get("rate_limit"),
                           stopped=step.get("stopped", False), timed_out=step.get("timed_out", False))


class Agents:
    def __init__(self, opencode=None, claude=None):
        self.opencode = opencode or ScriptedAgent()
        self.claude = claude or ScriptedAgent()
