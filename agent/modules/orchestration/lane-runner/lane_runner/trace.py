"""JSONL trace. Every event is flushed on write so `tail -f` is live. Secrets are redacted defensively."""
import json
import time


class Trace:
    def __init__(self, path, secrets=()):
        self.path = path
        self.secrets = [s for s in secrets if s]
        self.t0 = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")

    def emit(self, event, **fields):
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "t": round(time.time() - self.t0, 2),
                           "event": event, **fields}, ensure_ascii=False)
        for s in self.secrets:
            line = line.replace(s, "[redacted]")
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()


class Chunker:
    """Buffers streamed text and emits it as one event at least every `every` seconds (and on flush())."""

    def __init__(self, trace, event, every=5.0):
        self.trace, self.event, self.every = trace, event, every
        self.buf, self.full, self.last = [], [], time.time()

    def add(self, text):
        self.buf.append(text)
        self.full.append(text)
        if time.time() - self.last >= self.every:
            self.flush()

    def flush(self):
        if self.buf:
            self.trace.emit(self.event, text="".join(self.buf))
            self.buf = []
        self.last = time.time()
