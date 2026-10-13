"""Adapters that run the official `opencode` / `claude` binaries as subprocesses (existing logins, no credentials read)."""
import fcntl
import json
import os
import signal
import subprocess
import time
from collections import namedtuple
from dataclasses import dataclass, field

from . import guard


Agents = namedtuple("Agents", "opencode claude")


@dataclass
class StageResult:
    ok: bool = False
    text: str = ""
    session_id: str | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    duration_s: float = 0.0
    error: str = ""
    timed_out: bool = False
    stopped: bool = False
    returncode: int | None = None
    structured: dict | None = None
    rate_limit: dict | None = None  # {"five_hour": {utilization, resetsAt}, "seven_day": {...}}
    denials: list = field(default_factory=list)


def _kill_group(p):
    for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_process(argv, cwd, env, timeout, should_stop=None, on_start=None):
    """Run in its own process group; kill the whole group on timeout or stop. Returns (rc, out, err, timed_out, stopped, secs)."""
    t0 = time.time()
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    if on_start:
        on_start(p.pid, argv)  # lets the caller record the child so a resumed run can reap it if we get SIGKILLed
    timed_out = stopped = False
    while True:
        try:
            out, err = p.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            timed_out = time.time() - t0 > timeout
            stopped = not timed_out and bool(should_stop and should_stop())
            if timed_out or stopped:
                _kill_group(p)
                out, err = p.communicate()
                break
    return p.returncode, out, err, timed_out, stopped, time.time() - t0


def _events(out):
    for line in out.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


class OpenCodeAgent:
    def __init__(self, env, exe="opencode"):
        self.env, self.exe = env, exe

    def run(self, prompt, cwd, model, session=None, timeout=1800, should_stop=None, on_start=None):
        argv = [self.exe, "run", "-m", model, "--dir", str(cwd), "--format", "json", "--auto"]
        if session:
            argv += ["-s", session]
        argv.append(prompt)
        env = {**self.env, "OPENCODE_PERMISSION": guard.opencode_permission()}
        rc, out, err, timed_out, stopped, secs = run_process(argv, cwd, env, timeout, should_stop, on_start)
        r = StageResult(returncode=rc, duration_s=secs, timed_out=timed_out, stopped=stopped)
        texts, errors = [], []
        for o in _events(out):
            r.session_id = r.session_id or o.get("sessionID")
            part = o.get("part") or {}
            if o.get("type") == "text":
                texts.append(part.get("text", ""))
            elif o.get("type") == "step_finish":
                r.tokens = (r.tokens or 0) + (part.get("tokens") or {}).get("total", 0)
                r.cost_usd = (r.cost_usd or 0) + (part.get("cost") or 0)
            elif o.get("type") == "error":
                errors.append(part.get("message") or str(o.get("error") or o))
        r.text = "\n".join(texts)
        if errors:
            r.error = "; ".join(errors)
        elif timed_out:
            r.error = "timeout"
        elif rc:
            r.error = (err or out).strip()[-300:]
        r.ok = rc == 0 and not errors and not timed_out and not stopped
        return r


class ClaudeAgent:
    def __init__(self, env, exe="claude"):
        self.env, self.exe = env, exe

    def run(self, prompt, cwd, model, mode="full", schema=None, session=None, timeout=1800, should_stop=None, on_start=None):
        # Never --bare: it ignores the subscription login. Prompt right after -p; list flags take one comma-joined value.
        argv = [self.exe, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose"]
        if mode == "full":
            argv += ["--dangerously-skip-permissions", "--disallowedTools", guard.claude_deny_arg()]
        else:
            argv += ["--permission-mode", "dontAsk", "--allowedTools", guard.READONLY_TOOLS]
        if schema:
            argv += ["--json-schema", json.dumps(schema)]
        if session:
            argv += ["--resume", session]
        rc, out, err, timed_out, stopped, secs = run_process(argv, cwd, self.env, timeout, should_stop, on_start)
        r = StageResult(returncode=rc, duration_s=secs, timed_out=timed_out, stopped=stopped)
        result = None
        for o in _events(out):
            if o.get("type") == "rate_limit_event":
                r.rate_limit = (o.get("rate_limit_info") or {}).get("unifiedWindows")
            elif o.get("type") == "result":
                result = o
        if result:
            r.session_id = result.get("session_id")
            r.cost_usd = result.get("total_cost_usd")
            r.structured = result.get("structured_output")
            r.denials = result.get("permission_denials") or []
            r.text = result.get("result") or ""
            if result.get("is_error") or result.get("subtype") != "success":
                r.error = r.text or result.get("subtype", "error")
        if not r.error:
            r.error = "timeout" if timed_out else (err.strip()[-300:] if rc else ("no result event" if not result else ""))
        r.ok = bool(result) and not r.error and rc == 0 and not timed_out and not stopped
        return r


class LockedAgent:
    """Serialises calls through a cross-thread, cross-process file lock (at most one Claude call in flight)."""

    def __init__(self, agent, lock_path, poll=0.2):
        self.agent, self.lock_path, self.poll = agent, lock_path, poll

    def run(self, *args, should_stop=None, **kw):
        with open(self.lock_path, "a+") as f:
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if should_stop and should_stop():
                        return StageResult(stopped=True, error="stopped while waiting for the Claude lock")
                    time.sleep(self.poll)
            try:
                return self.agent.run(*args, should_stop=should_stop, **kw)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
