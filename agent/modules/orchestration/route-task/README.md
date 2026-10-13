# route-task

Tiered CLI orchestrator for unattended coding work. One command takes task files and runs each through:

```
worker (OpenCode, economy) -> gate (your tests/lint) -> evaluator (Claude Sonnet, read-only)
   pass -> done
   fail -> retry worker with the feedback (max 2) -> premium (Claude Opus, once) -> gate -> evaluator once
   still failing / evaluator down -> needs_human          quota too high -> parked (waiting_quota), resumes later
```

It drives the already-logged-in `opencode` and `claude` binaries as subprocesses. It never reads credentials, never calls an API, never pushes or merges. Each task gets its own git worktree on branch `rt/<run-id>`; you review that branch (e.g. in T3 Code) and push it yourself.

Design/spec: `docs/superpowers/specs/2026-10-13-route-task-orchestrator-design.md` in the aibar repo. Verified invocation facts and guardrail findings: `SPIKES.md`.

## Use

```bash
cp route-task.example.toml ~/.route-task/config.toml        # optional; every key has a default
./route-task run task.md                                    # one task
./route-task run a.md b.md c.md --jobs 3                    # parallel (one worktree each; one Claude call at a time)
./route-task status                                         # all runs, parked ones show their resume time
./route-task report RUN_ID                                  # what was done, agents used, verification, unresolved
./route-task resume RUN_ID [--wait]                         # continue a parked / stopped / crashed run
./route-task stop                                           # kill switch: ends runs, kills child processes (rm ~/.route-task/STOP to re-arm)
```

Task file (`---` front matter + free-form body):

```markdown
---
title: Add retry to fetch client
repo: /path/to/repo           # default: --repo or cwd
tier: auto                    # economy (default) | premium | auto (keyword rules, optional classifier)
privacy: open                 # open | private (default private) - see Privacy
gate:                         # deterministic checks, run in the worktree; non-zero exit = fail
  - python3 -m unittest discover -s tests
acceptance:                   # what the evaluator judges against
  - fetch() retries 3x on 5xx
---
Free-form description and constraints.
```

Without `gate:` a run is "done" but reported as not machine-verified.

## Quota policy (usage-left based, no call counts)

Before every Claude call it reads your live 5-hour / 7-day utilization from Claude's own `rate_limit_event` (a one-token Haiku probe if none is cached). Defaults, all in config: Sonnet evaluator allowed while 5h < 0.75 and 7d < 0.90; Opus premium while 5h < 0.60 and 7d < 0.80; plus weekly pacing (refuse when 7d utilization > fraction of the week elapsed + 0.15) so unattended runs cannot front-load the week and interactive T3 Code use keeps headroom. A blocked run parks as `waiting_quota` with a resume time; `run --wait` (default for batches) sleeps through it. No reading = fail closed. Utilization is an observed fraction; nothing is converted into tokens or dollars.

## Privacy

`opencode/muse-spark-1.3-contributor-free` (the default economy model) is free in exchange for training on prompts and completions. A `privacy: private` task (the default) is refused on a training-data model unless `economy.private_model` is set, or the repo is public (`gh repo view`, fails closed). Mark throwaway or public work `privacy: open`. Claude calls are not affected.

## Guardrails: what is enforced and what is not

- **Enforced:** children run with a scrubbed environment: no `ANTHROPIC_API_KEY` (it would silently override your subscription), `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK`; git has no credential helper/askpass/ssh; `gh` has an empty config dir. A `git push` or `gh` call fails to authenticate whatever form the agent uses (verified through a nested `bash -c`, see `SPIKES.md`).
- **Advisory only:** Bash deny rules for push/remote/gh/vercel/railway/publish/sudo. Spikes S1/S2 showed `bash -c` and python `subprocess` bypass them in both CLIs.
- **Not provided:** an OS sandbox. "Full access" means no permission prompts inside the worktree. An agent can still read or write outside it, and other CLIs whose tokens live in files under `$HOME` are not blocked. Review the branch and `report.md` before merging anything; run unattended only on tasks you would hand to a junior with your laptop.
- Caps: 2 worker attempts, 1 premium per task, per-stage timeouts, kill switch checked between and during stages. Premium is only reached through the gate/evaluator, never for infrastructure failures.

## Notes and limits

- `claude -p` on a personal subscription login is the documented-ordinary-use case (Claude Code legal page, support article 2026-10-07), but Anthropic's wording on automation is ambiguous and may change. This tool keeps one Claude call in flight and touches no credentials, so switching to API credits later is an environment change.
- OpenCode `1.x` JSON events and `--auto` are what this was built against (1.18.32); OpenCode 2 is a rewrite, so re-run the live test after upgrading.
- The default worktree location and run state live under `~/.route-task/` (`runs/`, `worktrees/`, `claude.lock`, `STOP`). Worktrees are not deleted automatically; `git worktree remove` them when done. Running adds `.route-task/` to the repo's `.git/info/exclude` (shared with the main checkout, untracked).

## Tests

```bash
cd agent/modules/orchestration/route-task
python3 -m unittest discover -s tests -t . -v        # no network, no tokens (fake opencode/claude stubs)
RT_LIVE=1 python3 -m unittest tests.test_live -v     # opt-in: real CLIs, Haiku standing in for Sonnet/Opus, a few tokens
```
