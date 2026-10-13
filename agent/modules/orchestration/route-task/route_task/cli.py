"""route-task CLI: run | resume | status | report | stop."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import batch, config, events, guard, notify, report, run, task
from .agents import Agents, ClaudeAgent, LockedAgent, OpenCodeAgent


def _load_cfg(path):
    path = path or os.environ.get("ROUTE_TASK_CONFIG")
    if not path:
        default = config.load(None).state_dir / "config.toml"
        path = default if default.exists() else None
    return config.load(path)


def _agents(cfg):
    env = guard.child_env(cfg)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return Agents(OpenCodeAgent(env), LockedAgent(ClaudeAgent(env), cfg.state_dir / "claude.lock"))


def _notifier(cfg):
    return notify.send if cfg.notify.enabled else None


def _line(r):
    s = r.state
    return f"{r.path.name}  {s['status']}  {s.get('branch', '')}  report: {r.path / 'report.md'}"


def _cmd_run(args, cfg):
    tasks, worst = [], 0
    for f in args.tasks:
        try:
            tasks.append(task.parse(Path(f).read_text()))
        except (task.TaskError, OSError) as e:
            print(f"{f}: {e}", file=sys.stderr)
            worst = 1
    wait = args.wait if args.wait is not None else len(tasks) > 1
    runs = batch.run_batch(tasks, cfg, _agents(cfg), jobs=args.jobs, repo=args.repo,
                           should_stop=(cfg.state_dir / "STOP").exists, wait=wait, notify=_notifier(cfg))
    for r in runs:
        print(_line(r))
        worst = worst or (0 if r.state["status"] == "done" else 1)
    return worst


def _cmd_resume(args, cfg):
    try:
        r = events.RunDir.open(cfg.state_dir / "runs", args.run_id)
        r = run.resume_task(r, cfg, _agents(cfg), should_stop=(cfg.state_dir / "STOP").exists, wait=bool(args.wait),
                            notify=_notifier(cfg))
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    print(_line(r))
    return 0 if r.state["status"] == "done" else 1


def _cmd_status(args, cfg):
    runs = sorted((cfg.state_dir / "runs").glob("*/state.json")) if (cfg.state_dir / "runs").exists() else []
    for p in runs:
        s = events.RunDir(p.parent).state
        extra = ""
        if s["status"] == "waiting_quota":
            extra = f"  ({report.resume_note(s.get('resume_after'), s.get('quota_reason', ''))})"
        print(f"{s['run_id']}  {s['status']:<14}  {s.get('stage', ''):<14}  {s['task']['title']}  {s.get('branch', '')}{extra}")
    if not runs:
        print("no runs yet")
    return 0


def _cmd_report(args, cfg):
    try:
        events.RunDir.open(cfg.state_dir / "runs", args.run_id)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    print((cfg.state_dir / "runs" / args.run_id / "report.md").read_text())
    return 0


def _cmd_stop(args, cfg):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "STOP").touch()
    print(f"kill switch set: {cfg.state_dir / 'STOP'} (remove it to run again)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="route-task")
    ap.add_argument("--config", help="TOML config (default: $ROUTE_TASK_CONFIG or ~/.route-task/config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("tasks", nargs="+")
    p.add_argument("--repo")
    p.add_argument("--jobs", type=int, default=1, help="tasks to run concurrently (one worktree each; one Claude call at a time)")
    p.add_argument("--wait", action=argparse.BooleanOptionalAction, default=None,
                   help="sleep through quota windows instead of parking (default: on for batches)")
    p = sub.add_parser("resume")
    p.add_argument("run_id")
    p.add_argument("--wait", action=argparse.BooleanOptionalAction, default=False)
    sub.add_parser("status")
    p = sub.add_parser("report")
    p.add_argument("run_id")
    sub.add_parser("stop")
    # allow --config after the subcommand too
    for sp in sub.choices.values():
        sp.add_argument("--config", dest="config_sub", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        cfg = _load_cfg(args.config_sub or args.config)
    except (config.ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return {"run": _cmd_run, "resume": _cmd_resume, "status": _cmd_status, "report": _cmd_report, "stop": _cmd_stop}[args.cmd](args, cfg)
