"""Run directory: atomic state.json + append-only events.jsonl. Disk is the source of truth (resume, status, report)."""
import json
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path


def new_run_id(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}-{slug}"


class RunDir:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def create(cls, root, run_id, state):
        r = cls(Path(root) / run_id)
        r.path.mkdir(parents=True)
        r._write({"run_id": run_id, **state})
        r.log("run_created")
        return r

    @classmethod
    def open(cls, root, run_id):
        r = cls(Path(root) / run_id)
        if not (r.path / "state.json").exists():
            raise FileNotFoundError(f"no such run: {run_id}")
        return r

    @property
    def state(self):
        return json.loads((self.path / "state.json").read_text())

    def _write(self, state):
        tmp = self.path / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, self.path / "state.json")

    def update(self, **kw):
        self._write({**self.state, **kw})

    def log(self, type_, **kw):
        line = json.dumps({"ts": time.time(), "type": type_, **kw})
        with open(self.path / "events.jsonl", "a") as f:
            f.write(line + "\n")
