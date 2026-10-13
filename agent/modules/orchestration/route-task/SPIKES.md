# Spike results (2026-10-13, claude 2.1.293, opencode 1.18.32, Python 3.14)

All spikes ran in scratch dirs under /tmp with local bare remotes; nothing touched a real remote.

## S1 — Claude deny rules under unattended mode
- `--dangerously-skip-permissions --disallowedTools "Bash(git push *)"`: direct `git push` DENIED (works while bypassing prompts). Control without the rule pushed.
- Also denied: `git -C . push`, `cd . && git push`, `g=git; $g push` (with patterns `Bash(git push*)` + `Bash(git * push*)`).
- **NOT denied: `bash -c 'git push …'` and `python3 -c "subprocess.run(['git','push',…])"`.** Real push reached the remote.
- Conclusion: Bash deny rules are an advisory first layer, not enforcement.

## S2 — OpenCode deny rules
- `OPENCODE_PERMISSION='{"bash":{"*":"allow","git push*":"deny"}}'` + `--auto`: direct push denied; `bash -c 'git push …'` EXECUTED. Same conclusion as S1.

## S3 — OpenCode `ask` non-interactive
- `OPENCODE_PERMISSION='{"bash":"ask"}'` + `opencode run`: prints "permission requested … auto-rejecting"; tool call errors, no hang. So unlisted tools fail safe.

## S4 — `claude -p --output-format stream-json --verbose --json-schema`
- Works together. Final `result` event carries `structured_output` (parsed object), `result` (JSON string), `session_id`, `total_cost_usd`, `is_error`, `permission_denials`, `modelUsage`. A `rate_limit_event` is also emitted before the assistant message.

## S5 — Quota probe
- A one-turn `--model haiku` call emits `rate_limit_event.rate_limit_info.unifiedWindows.{five_hour,seven_day}.{utilization,resetsAt}` (Team plan). Measured cost ≈ $0.004 equivalent; utilization effect not measurable.

## Enforcing layer (decided from S1/S2): credential starvation
Child processes get a scrubbed environment (`env -i` + allowlist) plus:
`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false GIT_SSH_COMMAND=/usr/bin/false GH_CONFIG_DIR=<empty tmp dir>`, no `GH_TOKEN`/`GITHUB_TOKEN`/`SSH_AUTH_SOCK`/`ANTHROPIC_API_KEY`.
Verified with `git push --dry-run` against the real GitHub remote: control (normal env) authenticated; starved env failed with "could not read Username", also through a nested `bash -c`; `gh auth status` reports not logged in. Both `claude -p` (rate_limit_event + result OK) and `opencode run` (Muse Spark) still work under this env.
Residual risk: other CLIs whose tokens live in files under `$HOME` (vercel, railway, …) are only covered by the advisory deny rules; this is not an OS sandbox.
