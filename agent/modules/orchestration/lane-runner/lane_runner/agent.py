"""The step loop: stream a completion, run its tool calls, trace everything, enforce budgets."""
import json
import os
import sys
import time

from .client import ApiError, stream_chat
from .tools import SCHEMAS, ToolError, Tools, parse_args, truncate
from .trace import Chunker, Trace

SYSTEM_PROMPT = """You are an autonomous coding executor. Work only inside the current working directory.
- Read the task, inspect the relevant files, then make the smallest change that satisfies it.
- Use test-driven development: write or confirm a failing test first, then make it pass.
- Run the validation commands named in the task with the `run` tool and read their output; do not claim success without it.
- Prefer edit_file for small changes; write_file for new files. Writes outside the allowed scope are refused.
- Never push, force, reset, or delete history; those commands are refused.
- When done, call finish(report) with: files changed, tests run, pass count, and any deviations from the task.
Keep going until the task is complete; do not ask questions."""

PLACEHOLDER = "[truncated by lane-runner]"
HIGH, LOW, CHARS_PER_TOKEN = 400_000, 250_000, 3.5


def estimate_tokens(messages):
    return sum(len(json.dumps(m)) for m in messages) / CHARS_PER_TOKEN


def truncate_history(messages, last_prompt_tokens, high=HIGH, low=LOW):
    """Blank the oldest tool results until the estimate is under `low`. Returns how many were blanked."""
    if last_prompt_tokens <= high:
        return 0
    n = 0
    for m in messages:
        if estimate_tokens(messages) <= low:
            break
        if m["role"] == "tool" and m["content"] != PLACEHOLDER:
            m["content"], n = PLACEHOLDER, n + 1
    return n


def _clip_args(args):
    return {k: (v[:2000] + f"...[+{len(v) - 2000} chars]" if isinstance(v, str) and len(v) > 2000 else v)
            for k, v in args.items()} if isinstance(args, dict) else args


class Runner:
    def __init__(self, a, key, base_url, trace: Trace, tools: Tools, task_text, out=None):
        self.a, self.key, self.base, self.trace, self.tools = a, key, base_url, trace, tools
        self.out = out or sys.stdout
        self.stall_s = float(os.environ.get("LANE_RUNNER_STALL_S", 120))
        self.backoff_s = float(os.environ.get("LANE_RUNNER_BACKOFF_S", 2))
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task_text}]
        self.totals = {"steps": 0, "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0}
        self.fail_streak = 0
        self.last_prompt = 0
        self.report = None
        self.t0 = time.time()

    def say(self, msg):
        print(msg, file=self.out, flush=True)

    def budget_hit(self):
        t = self.totals
        if t["steps"] >= self.a.max_steps:
            return f"max steps ({self.a.max_steps})"
        if time.time() - self.t0 >= self.a.max_minutes * 60:
            return f"max minutes ({self.a.max_minutes})"
        if t["prompt"] + t["completion"] >= self.a.max_total_tokens:
            return f"max total tokens ({self.a.max_total_tokens})"
        return None

    def effective_thinking(self):
        policy = self.a.thinking
        if policy == "off" and self.fail_streak >= 2:  # two failing runs in a row: one cheap-reasoning step
            self.fail_streak = 0
            return "low", True
        return policy, False

    def request_body(self, thinking):
        body = {"model": self.a.model, "messages": self.messages, "tools": SCHEMAS, "stream": True,
                "stream_options": {"include_usage": True}}
        if thinking == "off":
            body["thinking"] = {"type": "disabled"}
        elif thinking == "low":
            body["reasoning_effort"] = "low"
        return body

    def stream_step(self, body):
        """One completion with retries. Returns (content_text, tool_calls, finish_reason, usage)."""
        for attempt in range(4):
            content, reasoning = Chunker(self.trace, "content"), Chunker(self.trace, "reasoning")
            calls = {}

            def on_delta(d):
                if d.get("reasoning_content"):
                    reasoning.add(d["reasoning_content"])
                if d.get("content"):
                    content.add(d["content"])
                for tc in d.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                    c["id"] = tc.get("id") or c["id"]
                    f = tc.get("function") or {}
                    c["name"] = f.get("name") or c["name"]
                    c["args"] += f.get("arguments") or ""

            try:
                res = stream_chat(self.base, self.key, body, self.stall_s, on_delta)
            except ApiError as e:
                reasoning.flush(), content.flush()
                if not e.retryable or attempt == 3:
                    raise
                wait = min(self.backoff_s * 2 ** attempt, 60)
                self.trace.emit("retry", attempt=attempt + 1, error=str(e)[:300], wait_s=wait)
                self.say(f"  retry {attempt + 1}/3 in {wait:g}s: {str(e)[:120]}")
                time.sleep(wait)
                continue
            reasoning.flush(), content.flush()
            return "".join(content.full), calls, res["finish_reason"], res["usage"]
        raise ApiError("retries exhausted")

    def step(self):
        thinking, escalated = self.effective_thinking()
        n = self.totals["steps"] + 1
        self.trace.emit("step_start", step=n, thinking=thinking, escalated=escalated)
        t = time.time()
        text, calls, finish_reason, usage = self.stream_step(self.request_body(thinking))
        latency = round(time.time() - t, 2)
        u = {"prompt": usage.get("prompt_tokens") or int(estimate_tokens(self.messages)),
             "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
             "completion": usage.get("completion_tokens") or 0,
             "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0}
        for k, v in u.items():
            self.totals[k] += v
        self.totals["steps"] = n
        self.last_prompt = u["prompt"]
        self.trace.emit("usage", step=n, prompt_tokens=u["prompt"], cached_tokens=u["cached"],
                        completion_tokens=u["completion"], reasoning_tokens=u["reasoning"], latency_s=latency)
        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = [{"id": c["id"] or f"call_{n}_{i}", "type": "function",
                                  "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                                 for i, c in sorted(calls.items())]
        self.messages.append(msg)
        names = []
        if not calls:
            self.messages.append({"role": "user", "content": "No tool call received. Continue with the tools, "
                                  "and call finish(report) when the task is complete."})
        for tc in msg.get("tool_calls", []):
            name = tc["function"]["name"]
            names.append(name)
            if self.run_tool(tc, name):
                break
        self.say(f"[step {n}] {latency}s think={thinking}{'*' if escalated else ''} prompt={u['prompt']} "
                 f"(cached {u['cached']}) out={u['completion']} reasoning={u['reasoning']} | "
                 f"{', '.join(names) or 'no tool call'}")
        self.trace.emit("step_end", step=n, finish_reason=finish_reason, tools=names)
        trimmed = truncate_history(self.messages, self.last_prompt)
        if trimmed:
            self.trace.emit("context_truncated", tool_results=trimmed)

    def run_tool(self, tc, name):
        """Execute one tool call, append its result. Returns True if the task finished."""
        try:
            args = parse_args(tc["function"]["arguments"])
        except ToolError as e:
            args = {}
            err = e
        else:
            err = None
        self.trace.emit("tool_call", name=name, args=_clip_args(args))
        ok, extra, out = True, {}, ""
        try:
            if err:
                raise err
            if name == "finish":
                self.report = str(args.get("report", ""))
                out = "ok"
            else:
                out, extra = self.tools.call(name, args)
        except ToolError as e:
            ok, out = False, f"error: {e}"
        except Exception as e:  # a tool bug must never kill the run
            ok, out = False, f"error: {type(e).__name__}: {e}"
        if name == "run" and "exit_code" in extra:
            self.fail_streak = self.fail_streak + 1 if extra["exit_code"] != 0 else 0
        self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
        self.trace.emit("tool_result", name=name, ok=ok, exit_code=extra.get("exit_code"), output=truncate(out, 4000))
        return name == "finish" and ok
