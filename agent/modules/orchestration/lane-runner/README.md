# lane-runner

Direct-API coding-agent runner for unattended executor tasks. Replaces `opencode run` where the model is Xiaomi MiMo `mimo-v2.6-pro` (OpenAI-compatible). Stdlib-only Python, one task per process.

Why: through OpenCode the model spent ~96% of generated tokens on hidden reasoning (single steps of 627 s / 25k reasoning tokens) with no live visibility. Calling the API directly gives control of the thinking mode, a live trace, hard budgets, and scope rules enforced in code.

## Use

```bash
./lane-runner run --task task.md --dir /path/to/worktree \
    --allow 'src/**' --allow 'tests/**' \
    [--model mimo-v2.6-pro] [--thinking off|low|on] \
    [--max-steps 150] [--max-minutes 120] [--max-total-tokens 3000000] [--log run.jsonl]
```

`--task` file is the user message. `--allow` globs (repeatable; none = read-only) are the only paths `write_file`/`edit_file` may touch. Exit codes: `0` finished, `1` unrecoverable error, `2` bad CLI usage, `3` budget hit, `4` scope violation. `bash install.sh` symlinks the launcher to `~/.local/bin/lane-runner` (idempotent).

Credentials: `MIMO_API_KEY` + `MIMO_BASE_URL`, else provider `mimo` (`options.apiKey`, `options.baseURL`, `{env:NAME}` refs allowed) from `~/.config/opencode/opencode.json`. The key is never printed, never reaches the shell the model runs, and is redacted from the trace.

Launcher: `lane-runner` runs plain `python3` with `PYTHONPATH` set (symlink-safe, like `route-task`). No `uv run --with certifi` needed: the HTTPS context uses certifi if the interpreter has it, otherwise the default context, otherwise `/etc/ssl/cert.pem` (macOS framework Python ships no roots). That avoids a network/uv-cache dependency at startup. If your python lacks certs and `/etc/ssl/cert.pem`, set `SSL_CERT_FILE` or run `uv run --with certifi lane-runner ...`.

## Design

- One loop: stream a chat completion (`stream=true`, SSE), execute its tool calls, append results, repeat until `finish`.
- Retries per step (max 3, exponential backoff 2/4/8 s): no bytes for 120 s (stall), connection errors, truncated streams, HTTP 429/5xx. Other 4xx are unrecoverable. A retried step restarts the request.
- Thinking policy (provider facts: `thinking.budget_tokens` is ignored; `disabled` gives 0 reasoning tokens and ~5x speed):

| `--thinking` | request |
|---|---|
| `off` (default) | `"thinking":{"type":"disabled"}` |
| `low` | `"reasoning_effort":"low"` |
| `on` | nothing extra |

  Escalation: under `off`, when the last two `run` results both exited non-zero, the next single step uses `low`, then returns to `off` (`step_start.escalated=true`).
- Budgets are checked before every step: steps, wall minutes, cumulative prompt+completion tokens. Hit = `run_end.status="budget"`, exit 3.
- Context control: if the last `prompt_tokens` > 400k, the oldest tool-result contents are replaced by `[truncated by lane-runner]` until the estimate (chars / 3.5) is under 250k.
- `reasoning_content` is traced but not sent back to the model in later turns (stateless, avoids provider 400s; verified fine with `low`).

Tools: `read_file(path, offset?, limit?)` (line numbers), `list_dir(path)`, `grep(pattern, path?, glob?)` (ripgrep if on PATH, else Python; capped), `write_file`, `edit_file(path, old_string, new_string, replace_all?)` (exact; error if missing or not unique), `run(cmd, timeout_s?)` (bash in `--dir`, default 600 s, max 1800 s, output head+tail truncated to ~20k chars), `finish(report)`.

## Guardrails (enforced in code)

- Every path is resolved with symlinks followed and must stay inside `--dir`; `..` and symlink escapes are refused (reads and writes).
- Writes only for repo-relative paths matching an `--allow` glob (`fnmatch`; `**` works, `src/**/x.py` also matches `src/x.py`). `.git/` and `.lane-runner/` are never writable.
- `run` refuses commands matching: `git push`, `git reset --hard`, `git branch -D`, `git checkout --`, `git clean -f`, `git commit --amend`, `--force`, `rm -rf /`, `sudo`. A refusal is returned to the model as an error result; the run continues.
- `run` executes with `MIMO_API_KEY`, `MIMO_BASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK` removed from the environment, no stdin, own process group (killed on timeout).

- Post-run scope audit: when the run ends for any reason and `--dir` is a git work tree, every path changed vs the starting state (`git status --porcelain --untracked-files=all` minus paths already dirty at start, plus `git diff --name-only <start HEAD>..HEAD`; `.lane-runner/` ignored) must match an `--allow` glob. Otherwise a `scope_audit` event (`ok:false`, `violations`) is emitted, the paths are printed, and the run ends with status `scope_violation`, exit 4 (overrides finish, budget and error). Clean runs emit `scope_audit ok:true`; non-git dirs emit it with `skipped`. Gitignored files are invisible to it, and a path already dirty at start that the run edits further is not caught.

Limits: the command denylist is string matching, not a sandbox; a determined shell one-liner can evade it, and `run` can touch anything the user can outside `--dir` (shell writes inside the repo are caught after the fact by the audit; writes outside the repo are not). Run it only in a disposable worktree and review the diff. Git itself has no credentials scrubbed beyond the env vars above.

## Trace

Default `<dir>/.lane-runner/<timestamp>.jsonl` (added to the repo's `.git/info/exclude`), flushed per event:

```bash
tail -f /path/to/worktree/.lane-runner/*.jsonl | jq -c 'select(.event=="reasoning" or .event=="tool_call" or .event=="usage")'
```

Events (all have `ts`, `t` = seconds since start): `run_start` (args minus secrets), `step_start` (step, thinking, escalated), `reasoning` / `content` (text; flushed at least every 5 s while streaming, so thinking is live), `tool_call` (name, args; long strings clipped), `tool_result` (name, ok, exit_code, output clipped to 4k chars), `usage` (prompt_tokens, cached_tokens, completion_tokens, reasoning_tokens, latency_s), `retry`, `context_truncated`, `step_end`, `scope_audit` (ok, violations), `run_end` (status `finished|budget|error|interrupted|scope_violation`, totals, wall_s). On finish the report is also written to `<log>.report.md`. Stdout carries one compact line per step.

## Tests

```bash
cd agent/modules/orchestration/lane-runner
python3 -m pytest -q        # no network: fake SSE server on localhost; ~10 s (stall tests wait)
```
Test hooks: env `LANE_RUNNER_STALL_S` (default 120) and `LANE_RUNNER_BACKOFF_S` (default 2).
