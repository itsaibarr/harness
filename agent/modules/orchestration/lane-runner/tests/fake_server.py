"""Fake OpenAI-compatible SSE server on localhost. Each POST pops the next scripted response."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def chunk(delta=None, finish=None, usage=None):
    c = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        c = {"choices": [], "usage": usage}
    return c


def usage(prompt=100, completion=10, reasoning=0, cached=40):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning}}


def tool_step(name, args, thought="thinking...", **u):
    """A response that calls one tool; arguments are split over two chunks like real streams."""
    raw = json.dumps(args)
    h = len(raw) // 2
    chunks = [chunk({"reasoning_content": thought}),
              chunk({"tool_calls": [{"index": 0, "id": f"call_{name}", "type": "function",
                                     "function": {"name": name, "arguments": raw[:h]}}]}),
              chunk({"tool_calls": [{"index": 0, "function": {"arguments": raw[h:]}}]}),
              chunk(finish="tool_calls"), chunk(usage=usage(**u))]
    return ("sse", chunks)


def finish_step(report="done", **u):
    return tool_step("finish", {"report": report}, **u)


class FakeServer:
    """script: list of ("sse", chunks) | ("status", code) | ("stall", seconds). Repeats `loop` once exhausted."""

    def __init__(self, script, loop=None):
        self.script, self.loop, self.requests = list(script), loop, []
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with fake.lock:
                    fake.requests.append(body)
                    item = fake.script.pop(0) if fake.script else fake.loop
                kind, val = item
                try:
                    if kind == "status":
                        self.send_response(val)
                        self.send_header("Content-Length", "2")
                        self.end_headers()
                        self.wfile.write(b"{}")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.flush()
                    if kind == "stall":
                        threading.Event().wait(val)
                        return
                    for c in val:
                        self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
