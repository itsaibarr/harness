"""Task file: a tiny YAML subset (scalars + `- ` lists) in front matter, free-form body after."""
import re
from dataclasses import dataclass, field

TIERS = ("economy", "premium", "auto")
PRIVACY = ("open", "private")
LISTS = ("gate", "acceptance")
SCALARS = ("title", "repo", "tier", "privacy")


class TaskError(ValueError):
    pass


@dataclass
class Task:
    title: str
    body: str = ""
    repo: str | None = None
    tier: str = "economy"
    privacy: str = "private"
    gate: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)


def _strip_comment(v):
    return re.sub(r"\s+#.*$", "", v).strip()


def parse(text):
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        raise TaskError("task file must start with --- front matter ---")
    head, body = m.groups()
    fields, key = {}, None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"\s+-\s+(.*)", line)
        if item and key in LISTS:
            fields.setdefault(key, []).append(_strip_comment(item.group(1)))
            continue
        kv = re.match(r"([a-z_]+):\s*(.*)", line)
        if not kv:
            raise TaskError(f"cannot parse front matter line: {line!r}")
        key, val = kv.group(1), _strip_comment(kv.group(2))
        if key not in LISTS + SCALARS:
            raise TaskError(f"unknown task key: {key}")
        if key in SCALARS:
            fields[key] = val
        else:
            fields.setdefault(key, [])
    if not fields.get("title"):
        raise TaskError("task needs a title")
    t = Task(body=body.strip(), **fields)
    if t.tier not in TIERS:
        raise TaskError(f"tier must be one of {TIERS}")
    if t.privacy not in PRIVACY:
        raise TaskError(f"privacy must be one of {PRIVACY}")
    return t
