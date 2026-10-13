"""Config: defaults + optional TOML override. Unknown keys are errors (typos must not silently disable a limit)."""
import tomllib
from pathlib import Path
from types import SimpleNamespace

from .routing import TIERS

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
        "enabled": True,
    },
    "notify": {"enabled": True},  # macOS toast when a run finishes, parks or needs a human
    "routing": {"classifier": False},  # one cheap economy-model call for `tier: auto` tasks no rule matched
    "routes": [  # first match wins; whole-word, case-insensitive; only applies to `tier: auto`
        {"tier": "premium", "keywords": ["architecture", "architectural", "security", "threat model",
                                         "data migration", "migration plan", "auth", "authentication",
                                         "credentials", "payments"]},
    ],
    "env": {"extra": []},  # extra env var names to pass through to children
}


class ConfigError(ValueError):
    pass


def _same_kind(base, v):
    if isinstance(base, bool):
        return isinstance(v, bool)
    if isinstance(base, (int, float)):
        return isinstance(v, (int, float)) and not isinstance(v, bool)  # TOML `1` is fine where 0.75 is expected
    return isinstance(v, type(base))


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
            if not _same_kind(base[k], v):
                raise ConfigError(f"{path}{k}: expected {type(base[k]).__name__}, got {type(v).__name__}")
            out[k] = v
    return out


def _ns(d):
    return SimpleNamespace(**{k: _ns(v) if isinstance(v, dict) else v for k, v in d.items()})


def _check_routes(routes):
    for r in routes:
        if (not isinstance(r, dict) or set(r) != {"keywords", "tier"} or r["tier"] not in TIERS
                or not r["keywords"] or not all(isinstance(k, str) for k in r["keywords"])):
            raise ConfigError(f"bad [[routes]] entry (need keywords=[...] and tier=economy|premium): {r!r}")


def load(path):
    data = DEFAULTS
    if path:
        with open(path, "rb") as f:
            data = _merge(DEFAULTS, tomllib.load(f))
    _check_routes(data["routes"])
    c = _ns(data)
    c.state_dir = Path(c.state_dir).expanduser()
    return c
