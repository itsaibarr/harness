"""Resumable state machine. `ctx.next` is persisted after every step, so a run can park (quota), stop, crash and resume.

worker -> gate -> evaluator -> (retry with feedback | escalate) -> premium -> gate -> evaluator once -> done | needs_human
"""
import time
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

from . import evaluator, events, gate as gatemod, guard, handoff, quota as quotamod, report, routing, worktree
from .task import Task

WORKER_PROMPT = (
    "Read .route-task/handoff.md in this directory and complete the task it describes by editing files here. "
    "Stay inside this directory. Do not push, publish, deploy or contact remotes. "
    "Finish with a 3-line summary of what you changed.")
PREMIUM_PROMPT = (
    "Read .route-task/handoff.md in this directory. Cheaper agents already tried and failed. Find the root cause and "
    "fix it with the smallest diff; run the gate commands from the handoff yourself before finishing. "
    "Stay inside this directory. Do not push, publish, deploy or contact remotes. "
    "Finish with a 3-line summary.")
PROBE_PROMPT = "Reply with: ok"
WAIT_CHUNK_S = 30


class Ctx:
    def __init__(self, task, cfg, agents, run, wt, should_stop, economy_model, quota, tier, notify=None):
        self.task, self.cfg, self.agents, self.run, self.wt = task, cfg, agents, run, wt
        self.should_stop, self.economy_model, self.quota, self.notify = should_stop, economy_model, quota, notify
        self.env = guard.child_env(cfg)
        self.history, self.attempts, self.gate_text, self.issues = [], {"worker": 0, "premium": 0}, "", []
        self.next = "premium" if tier == "premium" else "worker"
        self.last_stage, self.error, self.resume_after, self.quota_reason = None, "", None, ""

    def save(self):
        self.run.update(history=self.history, attempts=self.attempts, next=self.next, last_stage=self.last_stage,
                        gate_text=self.gate_text, issues=self.issues, error=self.error, quota=self.quota.last)


def default_quota(cfg, agents):
    def probe():
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        return agents.claude.run(PROBE_PROMPT, cfg.state_dir, model="haiku", mode="readonly", timeout=90).rate_limit
    return quotamod.QuotaGate(cfg, probe)


def _handoff(ctx, next_step):
    handoff.write(ctx.wt.path, handoff.render(
        ctx.task, ctx.history, ctx.wt.diff_stat(), ctx.gate_text, ctx.issues, ctx.wt.files(), next_step))


def _run_stage(ctx, stage, agent_name, model, call):
    """One agent call + checkpoint + gate. Returns 'gate_ok' | 'fail' | 'stopped'."""
    ctx.attempts[stage] += 1
    n = ctx.attempts[stage]
    ctx.run.log("stage_start", stage=stage, attempt=n, agent=agent_name, model=model)
    r = call()
    ctx.wt.checkpoint(f"route-task: {stage} attempt {n}")
    ctx.quota.update(r.rate_limit)
    ctx.last_stage = stage
    gate_res = None
    if r.stopped:
        outcome = "stopped"
        ctx.attempts[stage] -= 1  # an interrupted attempt does not count against the cap
    elif not r.ok:
        outcome = f"agent error: {r.error}"
    else:
        gate_res = gatemod.run(ctx.task.gate, ctx.wt.path, ctx.env, ctx.cfg.limits.gate_timeout_s)
        ctx.gate_text = gate_res.summary()
        outcome = "gate passed" if gate_res.ok else "gate failed"
    ctx.history.append({"stage": stage, "agent": agent_name, "model": model, "duration_s": r.duration_s,
                        "outcome": outcome, "gate": gate_res.summary() if gate_res else "", "session_id": r.session_id})
    ctx.run.log("stage_end", stage=stage, attempt=n, outcome=outcome)
    return "stopped" if r.stopped else ("gate_ok" if outcome == "gate passed" else "fail")


def _after_gate_ok(ctx):
    ctx.next = "eval" if ctx.cfg.evaluator.enabled else "done"


def _park(ctx, decision):
    ctx.resume_after, ctx.quota_reason = decision.resume_after, decision.reason
    ctx.run.log("quota_block", reason=decision.reason, resume_after=decision.resume_after)
    return "waiting_quota"


def _step_worker(ctx):
    if ctx.attempts["worker"] >= ctx.cfg.limits.worker_attempts:
        ctx.next = "premium"
        return None
    _handoff(ctx, "Do the task. If an earlier attempt failed, fix exactly what the gate output / issues say; keep the diff small.")
    res = _run_stage(ctx, "worker", "opencode", ctx.economy_model, lambda: ctx.agents.opencode.run(
        WORKER_PROMPT, ctx.wt.path, model=ctx.economy_model, timeout=ctx.cfg.limits.worker_timeout_s,
        should_stop=ctx.should_stop))
    if res == "stopped":
        return "stopped"
    if res == "gate_ok":
        _after_gate_ok(ctx)
    return None


def _step_premium(ctx):
    if ctx.attempts["premium"] >= ctx.cfg.limits.premium_per_task:
        ctx.next = "needs_human"
        return None
    decision = ctx.quota.check("premium")
    if not decision.allowed:
        return _park(ctx, decision)
    _handoff(ctx, "Earlier attempts failed. Fix the root cause, smallest diff, then re-run the gate commands.")
    model = ctx.cfg.premium.model
    res = _run_stage(ctx, "premium", "claude", model, lambda: ctx.agents.claude.run(
        PREMIUM_PROMPT, ctx.wt.path, model=model, mode="full", timeout=ctx.cfg.limits.claude_timeout_s,
        should_stop=ctx.should_stop))
    if res == "stopped":
        return "stopped"
    if res == "gate_ok":
        _after_gate_ok(ctx)
    else:
        ctx.next = "needs_human"
    return None


def _step_eval(ctx):
    decision = ctx.quota.check("evaluator")
    if not decision.allowed:
        return _park(ctx, decision)
    cfg = ctx.cfg
    ctx.run.log("stage_start", stage="eval", model=cfg.evaluator.model)
    v = evaluator.evaluate(ctx.agents.claude, ctx.task, ctx.wt.path, ctx.wt.diff(), ctx.gate_text,
                           model=cfg.evaluator.model, timeout=cfg.limits.claude_timeout_s, should_stop=ctx.should_stop)
    ctx.quota.update(v.rate_limit)
    if v.call_failed:
        outcome = f"evaluator unavailable: {v.error}"
        ctx.error = outcome
    elif v.passed:
        outcome = "eval passed"
    else:
        ctx.issues = v.issues
        outcome = f"eval failed: {v.reason}"
    ctx.history.append({"stage": "eval", "agent": "claude", "model": cfg.evaluator.model, "duration_s": v.duration_s,
                        "outcome": outcome, "gate": "", "session_id": None})
    ctx.run.log("stage_end", stage="eval", outcome=outcome)
    if v.call_failed:
        if ctx.should_stop():
            return "stopped"
        ctx.next = "needs_human"  # evaluator infrastructure failure must not burn premium
    elif v.passed:
        ctx.next = "done"
    elif ctx.last_stage == "premium":
        ctx.next = "needs_human"
    else:
        ctx.next = "premium" if v.needs_premium and v.high_severity else "worker"
    return None


STEPS = {"worker": _step_worker, "premium": _step_premium, "eval": _step_eval}


def _loop(ctx):
    while True:
        if ctx.should_stop():
            return "stopped"
        if ctx.next in ("done", "needs_human"):
            return ctx.next
        out = STEPS[ctx.next](ctx)
        ctx.save()
        if out:
            return out


def _drive(ctx, wait, sleep, now):
    try:
        status = _loop(ctx)
        while status == "waiting_quota" and wait:
            while now() < ctx.resume_after:
                if ctx.should_stop():
                    status = "stopped"
                    break
                sleep(min(WAIT_CHUNK_S, ctx.resume_after - now()))
            else:
                status = _loop(ctx)
                continue
            break
    except Exception as e:  # a crashing task must be visible and must not take other tasks down
        ctx.error = f"{type(e).__name__}: {e}"
        ctx.run.log("run_error", error=ctx.error)
        status = "failed"
    return _finish(ctx, status)


def _notify(notify, title, status, detail=""):
    if notify and status != "stopped":  # the user caused a stop; no toast needed
        try:
            notify(f"route-task: {title}", f"{status}{detail}")
        except Exception:
            pass  # a broken notifier must never break a run


def _finish(ctx, status):
    parked = status == "waiting_quota"
    extra = {"resume_after": ctx.resume_after, "quota_reason": ctx.quota_reason} if parked else {}
    ctx.save()
    ctx.run.update(status=status, stage=status if parked else "finished", **extra)
    ctx.run.log("run_parked" if parked else "run_finished", status=status)
    report.write(ctx.run)
    detail = f" (resume after {datetime.fromtimestamp(ctx.resume_after):%a %H:%M}: {ctx.quota_reason})" if parked and ctx.resume_after else ""
    _notify(ctx.notify, ctx.task.title, status, detail)
    return ctx.run


def run_task(task, cfg, agents, repo=None, should_stop=None, run_id=None, is_public=None, quota=None,
             wait=False, sleep=time.sleep, now=time.time, notify=None):
    should_stop = should_stop or (lambda: False)
    run_id = run_id or events.new_run_id(task.title)
    repo = Path(task.repo or repo or ".").expanduser().resolve()
    run = events.RunDir.create(cfg.state_dir / "runs", run_id, {
        "status": "running", "stage": "intake", "task": asdict(task), "repo": str(repo),
        "attempts": {"worker": 0, "premium": 0}, "history": []})
    tier, why = routing.static(task, cfg)
    economy_model = cfg.economy.model
    if tier != "premium":
        try:
            economy_model = guard.economy_model(cfg, task, is_public or (lambda: guard.repo_is_public(repo)))
            if cfg.economy.trains_on_data:
                run.log("privacy_guard", decision="allowed", model=economy_model, privacy=task.privacy)
        except guard.PrivacyRefused as e:
            run.update(status="failed", error=f"privacy guard: {e}")
            run.log("privacy_guard", decision="refused", model=cfg.economy.model, privacy=task.privacy, reason=str(e))
            run.log("run_finished", status="failed")
            report.write(run)
            _notify(notify, task.title, "failed", f": {run.state['error']}")
            return run
    if tier is None:
        tier = "economy"
        if cfg.routing.classifier:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            c = agents.opencode.run(routing.classify_prompt(task), cfg.state_dir, model=economy_model, timeout=120)
            picked = routing.parse_classification(c.text) if c.ok else None
            if picked:
                tier, why = picked, "classifier"
    run.update(tier=tier, route_reason=why)
    run.log("route", tier=tier, reason=why)
    try:
        wt = worktree.create(repo, run_id, cfg.state_dir / "worktrees")
    except worktree.WorktreeError as e:
        run.update(status="failed", error=str(e))
        run.log("run_finished", status="failed")
        report.write(run)
        _notify(notify, task.title, "failed", f": {e}")
        return run
    run.update(worktree=str(wt.path), branch=wt.branch, base_sha=wt.base_sha, economy_model=economy_model)
    ctx = Ctx(task, cfg, agents, run, wt, should_stop, economy_model, quota or default_quota(cfg, agents), tier, notify)
    ctx.save()  # persist `next` before the first stage so even a crash during it is resumable
    return _drive(ctx, wait, sleep, now)


def resume_task(run, cfg, agents, should_stop=None, quota=None, wait=False, sleep=time.sleep, now=time.time, notify=None):
    """Continue a parked / stopped / interrupted run from its persisted `next` step (completed stages are not redone)."""
    s = run.state
    if s["status"] in ("done", "needs_human", "failed"):
        raise ValueError(f"run {s['run_id']} is {s['status']}; nothing to resume")
    wt = worktree.open_(s["repo"], s["run_id"], cfg.state_dir / "worktrees", s["base_sha"])
    ctx = Ctx(Task(**s["task"]), cfg, agents, run, wt, should_stop or (lambda: False), s["economy_model"],
              quota or default_quota(cfg, agents), s.get("tier", "economy"), notify)
    ctx.history, ctx.attempts = s["history"], s["attempts"]
    ctx.gate_text, ctx.issues, ctx.error = s.get("gate_text", ""), s.get("issues", []), s.get("error", "")
    ctx.next, ctx.last_stage = s["next"], s.get("last_stage")
    run.update(status="running")
    run.log("run_resumed", next=ctx.next)
    return _drive(ctx, wait, sleep, now)
