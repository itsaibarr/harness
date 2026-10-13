"""Config: defaults + optional TOML override. Unknown keys are errors (typos must not silently disable a limit)."""
import tomllib
from pathlib import Path
from types import SimpleNamespace

DEFAULTS = {
    "state_dir": "~/.route-task",
    "economy": {
        "model": "opencode/muse-spark-1.3-contributor-free",
        "trains_on_data": True,
        "private_model": "",  # used for privacy: private tasks when `model` trains on data
    },
    "evaluator": {"model": "sonnet", "enabled": True},
    "premium": {"model": "opus"},
    "limits": {
        "worker_attempts": 2,
        "premium_per_task": 1,
        "worker_timeout_s": 1800,
        "claude_timeout_s": 1800,
        "gate_timeout_s": 900,
    },
    "quota": {
        "evaluator_5h": 0.75,
        "evaluator_7d": 0.90,
        "premium_5h": 0.60,
        "premium_7d": 0.80,
        "pacing_slack": 0.15,
        "pacing": True,
        "probe_ttl_s": 60,
    },
    "env": {"extra": []},  # extra env var names to pass through to children
}


class ConfigError(ValueError):
    pass


def _merge(base, over, path=""):
    out = dict(base)
    for k, v in over.items():
        if k not in base:
            raise ConfigError(f"unknown config key: {path}{k}")
        if isinstance(base[k], dict):
            if not isinstance(v, dict):
                raise ConfigError(f"{path}{k} must be a table")
            out[k] = _merge(base[k], v, f"{path}{k}.")
        else:
            out[k] = v
    return out


def _ns(d):
    return SimpleNamespace(**{k: _ns(v) if isinstance(v, dict) else v for k, v in d.items()})


def load(path):
    data = DEFAULTS
    if path:
        with open(path, "rb") as f:
            data = _merge(DEFAULTS, tomllib.load(f))
    c = _ns(data)
    c.state_dir = Path(c.state_dir).expanduser()
    return c
