import json
import os

import pytest

from lane_runner import agent, cli
from lane_runner.tools import Tools, ToolError, allowed
from tests.fake_server import FakeServer, finish_step, tool_step

KEY = "sk-SECRET-key-123"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", KEY)
    monkeypatch.setenv("LANE_RUNNER_STALL_S", "0.6")
    monkeypatch.setenv("LANE_RUNNER_BACKOFF_S", "0.01")


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "work"
    (d / "src").mkdir(parents=True)
    (tmp_path / "task.md").write_text("Do the thing.")
    return d


@pytest.fixture
def serve(monkeypatch):
    servers = []

    def make(script, loop=None):
        s = FakeServer(script, loop)
        servers.append(s)
        monkeypatch.setenv("MIMO_BASE_URL", s.url)
        return s
    yield make
    for s in servers:
        s.close()


def run(work, *extra, log=None):
    log = log or work.parent / "t.jsonl"
    code = cli.main(["run", "--task", str(work.parent / "task.md"), "--dir", str(work),
                     "--allow", "src/**", "--log", str(log), *extra])
    events = [json.loads(l) for l in log.read_text().splitlines()]
    return code, events, log


def results(events, name=None):
    return [e for e in events if e["event"] == "tool_result" and (name is None or e["name"] == name)]


def test_round_trip_ends_in_finish(work, serve, capsys):
    s = serve([tool_step("write_file", {"path": "src/a.py", "content": "x = 1\n"}),
               tool_step("run", {"cmd": "cat src/a.py"}),
               finish_step("changed src/a.py")])
    code, events, log = run(work)
    assert code == 0
    assert (work / "src/a.py").read_text() == "x = 1\n"
    assert "x = 1" in results(events, "run")[0]["output"]
    assert (log.parent / "t.jsonl.report.md").read_text() == "changed src/a.py"
    # the tool result was fed back to the model
    assert s.requests[2]["messages"][-1]["role"] == "tool"
    assert events[-1]["event"] == "run_end" and events[-1]["status"] == "finished"
    assert "[step 3]" in capsys.readouterr().out


def test_write_outside_allow_refused(work, serve):
    serve([tool_step("write_file", {"path": "README.md", "content": "no"}),
           tool_step("write_file", {"path": ".git/config", "content": "no"}), finish_step()])
    code, events, _ = run(work)
    assert code == 0 and not (work / "README.md").exists()
    assert [r["ok"] for r in results(events, "write_file")] == [False, False]
    assert "write refused" in results(events, "write_file")[0]["output"]


def test_path_escape_refused(work, serve, tmp_path):
    (tmp_path / "secret.txt").write_text("outside")
    (work / "src" / "link").symlink_to(tmp_path)
    serve([tool_step("read_file", {"path": "../secret.txt"}),
           tool_step("read_file", {"path": "src/link/secret.txt"}),
           tool_step("write_file", {"path": "src/link/new.txt", "content": "x"}), finish_step()])
    code, events, _ = run(work)
    assert code == 0
    assert [r["ok"] for r in results(events)[:3]] == [False] * 3
    assert not (tmp_path / "new.txt").exists()
    assert "outside" not in json.dumps(events)


def test_denied_git_command_refused(work, serve):
    cmds = ["touch marker && git push origin main", "git reset --hard HEAD", "git branch -D x", "git checkout -- .",
            "git clean -fd", "git commit --amend -m x", "echo hi --force", "rm -rf /tmp/x", "sudo ls"]
    serve([tool_step("run", {"cmd": c}) for c in cmds] + [finish_step()])
    code, events, _ = run(work)
    assert code == 0 and not (work / "marker").exists()
    rs = results(events, "run")
    assert len(rs) == len(cmds) and all(not r["ok"] and "refused" in r["output"] for r in rs)


def test_stall_retries_then_succeeds(work, serve):
    s = serve([("stall", 3), finish_step()])
    code, events, _ = run(work)
    assert code == 0 and len(s.requests) == 2
    assert any(e["event"] == "retry" for e in events)


def test_retry_on_429_then_gives_up_with_exit_1(work, serve):
    s = serve([("status", 429), ("status", 503), finish_step()])
    assert run(work)[0] == 0 and len(s.requests) == 3
    s2 = serve([], loop=("status", 500))
    code, events, _ = run(work, log=work.parent / "t2.jsonl")
    assert code == 1 and len(s2.requests) == 4 and events[-1]["status"] == "error"


def test_http_400_is_unrecoverable(work, serve):
    s = serve([("status", 400)])
    assert run(work)[0] == 1 and len(s.requests) == 1


def test_max_steps_budget_exit_3(work, serve):
    s = serve([], loop=tool_step("list_dir", {"path": "."}))
    code, events, _ = run(work, "--max-steps", "2")
    assert code == 3 and len(s.requests) == 2
    assert events[-1]["status"] == "budget" and events[-1]["totals"]["steps"] == 2


def test_token_budget_exit_3(work, serve):
    serve([], loop=tool_step("list_dir", {"path": "."}, prompt=900, completion=200))
    code, events, _ = run(work, "--max-total-tokens", "1000")
    assert code == 3 and events[-1]["totals"]["steps"] == 1


@pytest.mark.parametrize("mode,check", [
    ("off", lambda b: b["thinking"] == {"type": "disabled"} and "reasoning_effort" not in b),
    ("low", lambda b: b["reasoning_effort"] == "low" and "thinking" not in b),
    ("on", lambda b: "thinking" not in b and "reasoning_effort" not in b)])
def test_thinking_param(work, serve, mode, check):
    s = serve([finish_step()])
    assert run(work, "--thinking", mode)[0] == 0
    assert check(s.requests[0])


def test_escalation_after_two_failing_runs(work, serve):
    s = serve([tool_step("run", {"cmd": "false"}), tool_step("run", {"cmd": "false"}),
               tool_step("list_dir", {"path": "."}), finish_step()])
    code, events, _ = run(work)
    assert code == 0
    modes = ["low" if b.get("reasoning_effort") == "low" else "off" for b in s.requests]
    assert modes == ["off", "off", "low", "off"]
    assert [e["thinking"] for e in events if e["event"] == "step_start"] == ["off", "off", "low", "off"]
    assert results(events, "run")[0]["exit_code"] == 1


def test_trace_events_and_secrets(work, serve, capsys):
    serve([tool_step("run", {"cmd": "echo key=[$MIMO_API_KEY]"}, thought="plan A"), finish_step()])
    code, events, log = run(work)
    kinds = {e["event"] for e in events}
    assert {"run_start", "step_start", "reasoning", "tool_call", "tool_result", "usage", "step_end", "run_end"} <= kinds
    assert next(e for e in events if e["event"] == "reasoning")["text"] == "plan A"
    u = next(e for e in events if e["event"] == "usage")
    assert (u["prompt_tokens"], u["cached_tokens"], u["completion_tokens"], u["reasoning_tokens"]) == (100, 40, 10, 0)
    assert "key=[]" in results(events, "run")[0]["output"]  # key scrubbed from the shell env
    assert KEY not in log.read_text() + capsys.readouterr().out


def test_missing_credentials_exit(monkeypatch, work):
    monkeypatch.delenv("MIMO_API_KEY")
    monkeypatch.delenv("MIMO_BASE_URL", raising=False)
    monkeypatch.setattr("lane_runner.config.OPENCODE_CONFIG", work / "nope.json")
    with pytest.raises(SystemExit):
        run(work)


# -- unit tests
def test_glob_matching():
    assert allowed("src/a/b.py", ["src/**"]) and allowed("src/b.py", ["src/**/*.py"])
    assert allowed("tests/t.py", ["src/**", "tests/**"]) and not allowed("lib/x.py", ["src/**"])


def test_edit_file_semantics(tmp_path):
    (tmp_path / "f.txt").write_text("a a b")
    t = Tools(tmp_path, ["*"])
    with pytest.raises(ToolError, match="not found"):
        t.call("edit_file", {"path": "f.txt", "old_string": "zzz", "new_string": "y"})
    with pytest.raises(ToolError, match="matches 2"):
        t.call("edit_file", {"path": "f.txt", "old_string": "a", "new_string": "y"})
    t.call("edit_file", {"path": "f.txt", "old_string": "a", "new_string": "y", "replace_all": True})
    assert (tmp_path / "f.txt").read_text() == "y y b"


def test_read_grep_list(tmp_path):
    (tmp_path / "f.py").write_text("one\ntwo needle\nthree\n")
    t = Tools(tmp_path, [])
    assert "     2\ttwo needle" in t.call("read_file", {"path": "f.py", "offset": 2, "limit": 1})[0]
    assert "f.py:2" in t.call("grep", {"pattern": "needle"})[0]
    assert "f.py" in t.call("list_dir", {"path": "."})[0]
    assert "f.py:2" in t._pygrep("needle", tmp_path, "*.py")[0]


def test_run_timeout_and_truncation(tmp_path):
    t = Tools(tmp_path, [])
    out, extra = t.call("run", {"cmd": "sleep 5", "timeout_s": 1})
    assert extra["exit_code"] == 124 and "timeout" in out
    out, _ = t.call("run", {"cmd": "python3 -c \"print('x'*50000)\""})
    assert "chars truncated" in out and len(out) < 21000


def test_context_truncation():
    msgs = [{"role": "system", "content": "s"}] + [
        {"role": "tool", "tool_call_id": str(i), "content": "x" * 70000} for i in range(10)]
    assert agent.truncate_history(msgs, 1000, high=400_000, low=250_000) == 0
    n = agent.truncate_history(msgs, 450_000, high=400_000, low=100_000)
    assert n > 0 and msgs[1]["content"] == agent.PLACEHOLDER and msgs[-1]["content"] != agent.PLACEHOLDER
    assert agent.estimate_tokens(msgs) <= 100_000
