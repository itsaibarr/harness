# route-task

Tiered CLI orchestrator: OpenCode economy worker → deterministic gate → Sonnet evaluator → bounded retry → Claude premium escalation → human. Runs the already-authenticated `opencode` and `claude` CLIs as subprocesses; never reads credentials, never pushes.

Status: under construction (see `SPIKES.md` for verified invocation facts). Design: spec and plan live in the aibar repo (`docs/superpowers/specs/2026-10-13-route-task-orchestrator-design.md`).

Tests: `cd agent/modules/orchestration/route-task && python3 -m unittest discover -s tests -t . -v` (no network, no tokens).
