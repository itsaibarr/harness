"""route-task CLI: run | status | report | stop."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import config, events, guard, run, task
from .agents import Agents, ClaudeAgent, OpenCodeAgent


def _load_cfg(path):
    path = path or os.environ.get("ROUTE_TASK_CONFIG")
    if not path:
        default = config.load(None).state_dir / "config.toml"
        path = default if default.exists() else None
    return config.load(path)


def _cmd_run(args, cfg):
    stop_file = cfg.state_dir / "STOP"
    env = guard.child_env(cfg)
    agents = Agents(OpenCodeAgent(env), ClaudeAgent(env))
    worst = 0
    for f in args.tasks:
        try:
            t = task.parse(Path(f).read_text())
        except (task.TaskError, OSError) as e:
            print(f"{f}: {e}", file=sys.stderr)
            worst = 1
            continue
        r = run.run_task(t, cfg, agents, repo=args.repo, should_stop=stop_file.exists)
        s = r.state
        print(f"{r.path.name}  {s['status']}  {s.get('branch', '')}  report: {r.path / 'report.md'}")
        worst = worst or (0 if s["status"] == "done" else 1)
    return worst


def _cmd_status(args, cfg):
    runs = sorted((cfg.state_dir / "runs").glob("*/state.json")) if (cfg.state_dir / "runs").exists() else []
    for p in runs:
        s = json.loads(p.read_text())
        print(f"{s['run_id']}  {s['status']:<14}  {s.get('stage', ''):<10}  {s['task']['title']}  {s.get('branch', '')}")
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
    return {"run": _cmd_run, "status": _cmd_status, "report": _cmd_report, "stop": _cmd_stop}[args.cmd](args, cfg)
