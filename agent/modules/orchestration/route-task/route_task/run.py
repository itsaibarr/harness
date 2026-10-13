"""State machine: worker -> gate -> (retry with feedback) -> premium -> gate -> done | needs_human."""
from dataclasses import asdict
from pathlib import Path

from . import events, gate as gatemod, guard, handoff, report, worktree

WORKER_PROMPT = (
    "Read .route-task/handoff.md in this directory and complete the task it describes by editing files here. "
    "Stay inside this directory. Do not push, publish, deploy or contact remotes. "
    "Finish with a 3-line summary of what you changed.")
PREMIUM_PROMPT = (
    "Read .route-task/handoff.md in this directory. Cheaper agents already tried and failed. Find the root cause and "
    "fix it with the smallest diff; run the gate commands from the handoff yourself before finishing. "
    "Stay inside this directory. Do not push, publish, deploy or contact remotes. "
    "Finish with a 3-line summary.")


class Ctx:
    def __init__(self, task, cfg, agents, run, wt, should_stop):
        self.task, self.cfg, self.agents, self.run, self.wt, self.should_stop = task, cfg, agents, run, wt, should_stop
        self.env = guard.child_env(cfg)
        self.history, self.attempts, self.gate_text, self.issues = [], {"worker": 0, "premium": 0}, "", []

    def save(self):
        self.run.update(history=self.history, attempts=self.attempts)


def _handoff(ctx, next_step):
    handoff.write(ctx.wt.path, handoff.render(
        ctx.task, ctx.history, ctx.wt.diff_stat(), ctx.gate_text, ctx.issues, ctx.wt.files(), next_step))


def _attempt(ctx, stage, agent_name, model, call):
    """Run one agent call, checkpoint, gate. Returns 'pass' | 'fail' | 'stopped'."""
    ctx.attempts[stage] += 1
    n = ctx.attempts[stage]
    ctx.run.log("stage_start", stage=stage, attempt=n, agent=agent_name, model=model)
    r = call()
    ctx.wt.checkpoint(f"route-task: {stage} attempt {n}")
    gate_res = None
    if r.stopped:
        outcome = "stopped"
    elif not r.ok:
        outcome = f"agent error: {r.error}"
    else:
        gate_res = gatemod.run(ctx.task.gate, ctx.wt.path, ctx.env, ctx.cfg.limits.gate_timeout_s)
        ctx.gate_text = gate_res.summary()
        outcome = "gate passed" if gate_res.ok else "gate failed"
    ctx.history.append({"stage": stage, "agent": agent_name, "model": model, "duration_s": r.duration_s,
                        "outcome": outcome, "gate": gate_res.summary() if gate_res else "", "session_id": r.session_id})
    ctx.save()
    ctx.run.log("stage_end", stage=stage, attempt=n, outcome=outcome)
    return "stopped" if r.stopped else ("pass" if outcome == "gate passed" else "fail")


def _worker(ctx):
    _handoff(ctx, "Do the task. If an earlier attempt failed, fix exactly what the gate output says; keep the diff small.")
    return _attempt(ctx, "worker", "opencode", ctx.cfg.economy.model, lambda: ctx.agents.opencode.run(
        WORKER_PROMPT, ctx.wt.path, model=ctx.cfg.economy.model, timeout=ctx.cfg.limits.worker_timeout_s,
        should_stop=ctx.should_stop))


def _premium(ctx):
    _handoff(ctx, "Earlier attempts failed. Fix the root cause, smallest diff, then re-run the gate commands.")
    model = ctx.cfg.premium.model
    return _attempt(ctx, "premium", "claude", model, lambda: ctx.agents.claude.run(
        PREMIUM_PROMPT, ctx.wt.path, model=model, mode="full", timeout=ctx.cfg.limits.claude_timeout_s,
        should_stop=ctx.should_stop))


def _pipeline(ctx):
    if ctx.task.tier != "premium":
        for _ in range(ctx.cfg.limits.worker_attempts):
            if ctx.should_stop():
                return "stopped"
            outcome = _worker(ctx)
            if outcome != "fail":
                return "done" if outcome == "pass" else "stopped"
    if ctx.attempts["premium"] < ctx.cfg.limits.premium_per_task:
        if ctx.should_stop():
            return "stopped"
        outcome = _premium(ctx)
        return {"pass": "done", "stopped": "stopped"}.get(outcome, "needs_human")
    return "needs_human"


def run_task(task, cfg, agents, repo=None, should_stop=None, run_id=None):
    should_stop = should_stop or (lambda: False)
    run_id = run_id or events.new_run_id(task.title)
    repo = Path(task.repo or repo or ".").expanduser().resolve()
    run = events.RunDir.create(cfg.state_dir / "runs", run_id, {
        "status": "running", "stage": "intake", "task": asdict(task), "repo": str(repo),
        "attempts": {"worker": 0, "premium": 0}, "history": []})
    try:
        wt = worktree.create(repo, run_id, cfg.state_dir / "worktrees")
    except worktree.WorktreeError as e:
        run.update(status="failed", error=str(e))
        run.log("run_finished", status="failed")
        report.write(run)
        return run
    run.update(worktree=str(wt.path), branch=wt.branch, base_sha=wt.base_sha)
    ctx = Ctx(task, cfg, agents, run, wt, should_stop)
    status = _pipeline(ctx)
    run.update(status=status, stage="finished")
    run.log("run_finished", status=status)
    report.write(run)
    return run
