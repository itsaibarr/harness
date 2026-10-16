import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import agent
from .client import ApiError
from .config import load_credentials
from .tools import Tools
from .trace import Trace

EXIT_OK, EXIT_ERROR, EXIT_BUDGET = 0, 1, 3


def build_parser():
    p = argparse.ArgumentParser(prog="lane-runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one task to completion")
    r.add_argument("--task", required=True, help="task markdown file (becomes the user message)")
    r.add_argument("--dir", required=True, help="work dir (git worktree); all paths are confined to it")
    r.add_argument("--allow", action="append", default=[], metavar="GLOB",
                   help="repo-relative glob writable by write_file/edit_file (repeatable; none = read-only)")
    r.add_argument("--model", default="mimo-v2.6-pro")
    r.add_argument("--thinking", choices=["off", "low", "on"], default="off")
    r.add_argument("--max-steps", type=int, default=150)
    r.add_argument("--max-minutes", type=float, default=120)
    r.add_argument("--max-total-tokens", type=int, default=3_000_000)
    r.add_argument("--log", help="JSONL trace path (default <dir>/.lane-runner/<timestamp>.jsonl)")
    return p


def _git_exclude(root, log):
    """Keep the default trace dir out of `git status` (shared info/exclude, like route-task does)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=root, capture_output=True,
                             text=True, check=True).stdout.strip()
        f = (root / out).resolve()
        if ".lane-runner/" not in (f.read_text() if f.exists() else ""):
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a") as fh:
                fh.write(".lane-runner/\n")
    except (OSError, subprocess.CalledProcessError, FileNotFoundError):
        pass


def run(a):
    root = Path(a.dir).resolve()
    if not root.is_dir():
        print(f"lane-runner: --dir is not a directory: {a.dir}", file=sys.stderr)
        return EXIT_ERROR
    task = Path(a.task)
    if not task.is_file():
        print(f"lane-runner: task file not found: {a.task}", file=sys.stderr)
        return EXIT_ERROR
    key, base = load_credentials()
    log = Path(a.log) if a.log else root / ".lane-runner" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    if not a.log:
        _git_exclude(root, log)
    trace = Trace(log, secrets=[key])
    trace.emit("run_start", args={**vars(a), "dir": str(root), "base_url": base}, log=str(log))
    print(f"lane-runner: model={a.model} thinking={a.thinking} dir={root}\n  trace: {log}  (tail -f it)", flush=True)
    r = agent.Runner(a, key, base, trace, Tools(root, a.allow), task.read_text(encoding="utf-8"))
    status, code, note = "error", EXIT_ERROR, ""
    try:
        while True:
            why = r.budget_hit()
            if why:
                status, code, note = "budget", EXIT_BUDGET, why
                break
            r.step()
            if r.report is not None:
                status, code = "finished", EXIT_OK
                break
    except ApiError as e:
        note = str(e)[:500]
    except KeyboardInterrupt:
        status, note = "interrupted", "KeyboardInterrupt"
    except Exception as e:
        note = f"{type(e).__name__}: {e}"
    wall = round(time.time() - r.t0, 1)
    trace.emit("run_end", status=status, note=note, totals=r.totals, wall_s=wall)
    trace.close()
    t = r.totals
    print(f"lane-runner: {status}{' (' + note + ')' if note else ''} steps={t['steps']} prompt={t['prompt']} "
          f"cached={t['cached']} completion={t['completion']} reasoning={t['reasoning']} wall={wall}s", flush=True)
    if r.report is not None:
        Path(str(log) + ".report.md").write_text(r.report, encoding="utf-8")
        print("\n--- report ---\n" + r.report, flush=True)
    return code


def main(argv=None):
    a = build_parser().parse_args(argv)
    return run(a)
