"""Streaming OpenAI-compatible chat client on urllib. Stall = no bytes for `stall_s` -> retryable ApiError."""
import http.client
import json
import os
import ssl
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, msg, retryable=False, status=None):
        super().__init__(msg)
        self.retryable, self.status = retryable, status


def ssl_context():
    """certifi if importable, else the interpreter default, else the macOS system bundle (framework Python ships none)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    p = ssl.get_default_verify_paths()
    have = (p.cafile and os.path.exists(p.cafile)) or (p.capath and os.path.isdir(p.capath))
    if not have and os.path.exists("/etc/ssl/cert.pem"):
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


def stream_chat(base_url, key, body, stall_s, on_delta, ctx=None):
    """POST /chat/completions with stream=true. Calls on_delta(delta_dict) per chunk; returns
    {"finish_reason", "usage"}. Raises ApiError(retryable=...) on HTTP/network/stall problems."""
    req = urllib.request.Request(
        base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"})
    try:
        resp = urllib.request.urlopen(req, timeout=stall_s, context=ctx or ssl_context())
    except urllib.error.HTTPError as e:
        detail = e.read(500).decode("utf-8", "replace")
        raise ApiError(f"HTTP {e.code}: {detail}", retryable=e.code == 429 or e.code >= 500, status=e.code)
    except (OSError, http.client.HTTPException) as e:  # URLError, socket.timeout, resets
        raise ApiError(f"connect failed: {e}", retryable=True)
    finish, usage = None, None
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    if ch.get("delta"):
                        on_delta(ch["delta"])
                    finish = ch.get("finish_reason") or finish
    except (OSError, http.client.HTTPException) as e:  # includes socket.timeout = stall
        raise ApiError(f"stream interrupted (stall or reset): {e}", retryable=True)
    if finish is None:
        raise ApiError("stream ended without finish_reason", retryable=True)
    return {"finish_reason": finish, "usage": usage or {}}
