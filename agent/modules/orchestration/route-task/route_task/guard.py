"""Guardrails for unattended children.

Enforcement = the scrubbed env (git/gh cannot authenticate, whatever the invocation form: spike S1/S2 showed
`bash -c` and python subprocess bypass Bash deny rules). The deny lists below are only an advisory first layer.
"""
import functools
import json
import os
import subprocess
from pathlib import Path

ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL",
         "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
FORBIDDEN = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK")
STARVE = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/usr/bin/false",
    "GIT_SSH_COMMAND": "/usr/bin/false",
}
DENY = (
    "git push*", "git * push*", "git remote*", "git * remote*",
    "gh pr*", "gh release*", "gh repo*", "gh api*", "gh workflow*",
    "vercel*", "railway*", "npm publish*", "pnpm publish*", "yarn publish*",
    "sudo*", "rm -rf /*", "rm -rf ~*",
)
READONLY_TOOLS = "Read,Grep,Glob,Bash(git diff*),Bash(git status*),Bash(git log*),Bash(git show*)"


def child_env(cfg, extra=None):
    env = {k: os.environ[k] for k in ALLOW if k in os.environ}
    env["TERM"] = "dumb"
    env.update({k: os.environ[k] for k in cfg.env.extra if k in os.environ})
    env.update(STARVE)
    gh_dir = Path(cfg.state_dir) / "ghempty"
    gh_dir.mkdir(parents=True, exist_ok=True)
    env["GH_CONFIG_DIR"] = str(gh_dir)
    env.update(extra or {})
    for k in FORBIDDEN:
        env.pop(k, None)
    return env


def claude_deny_arg():
    return ",".join(f"Bash({p})" for p in DENY)


def opencode_permission():
    # OpenCode: last matching rule wins, so the catch-all allow goes first.
    return json.dumps({"bash": {"*": "allow", **{p: "deny" for p in DENY}}, "edit": "allow"})


class PrivacyRefused(RuntimeError):
    pass


@functools.cache  # one `gh` network call per repo per process, not per task
def repo_is_public(repo):
    """True only if `gh` says the repo is public and not a fork. Fails closed (False) on any error."""
    try:
        r = subprocess.run(["gh", "repo", "view", "--json", "isPrivate,isFork", "-q", "(.isPrivate|not) and (.isFork|not)"],
                           cwd=repo, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def economy_model(cfg, task, is_public):
    """Pick the OpenCode model for the task, refusing to send private code to a model that trains on prompts."""
    e = cfg.economy
    if not e.trains_on_data or task.privacy == "open":
        return e.model
    if e.private_model:
        return e.private_model
    if is_public():
        return e.model
    raise PrivacyRefused(
        f"{e.model} trains on prompts and this task is private. Set `privacy: open` in the task file, "
        "or set economy.private_model in the config to a zero-retention model.")
