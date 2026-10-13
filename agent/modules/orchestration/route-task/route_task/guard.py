"""Guardrails for unattended children.

Enforcement = the scrubbed env (git/gh cannot authenticate, whatever the invocation form: spike S1/S2 showed
`bash -c` and python subprocess bypass Bash deny rules). The deny lists below are only an advisory first layer.
"""
import json
import os
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
