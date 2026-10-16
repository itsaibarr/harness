"""Credentials: env first, then provider "mimo" in ~/.config/opencode/opencode.json. The key is never printed or logged."""
import json
import os
import re
from pathlib import Path

OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"


def _resolve(value):
    """OpenCode lets option values be '{env:NAME}' references."""
    if isinstance(value, str):
        m = re.fullmatch(r"\{env:(\w+)\}", value.strip())
        if m:
            return os.environ.get(m.group(1))
    return value


def load_credentials(env=None):
    env = os.environ if env is None else env
    key, base = env.get("MIMO_API_KEY"), env.get("MIMO_BASE_URL")
    if not (key and base):
        try:
            opts = json.loads(OPENCODE_CONFIG.read_text())["provider"]["mimo"]["options"]
        except (OSError, ValueError, KeyError, TypeError):
            opts = {}
        key = key or _resolve(opts.get("apiKey"))
        base = base or _resolve(opts.get("baseURL"))
    if not (key and base):
        raise SystemExit("lane-runner: no credentials. Set MIMO_API_KEY and MIMO_BASE_URL, or add provider 'mimo' "
                         f"(options.apiKey, options.baseURL) to {OPENCODE_CONFIG}")
    return key, base.rstrip("/")
