---
name: route-task
description: Delegate bounded, test-verifiable coding task(s) to the tiered route-task pipeline (cheap OpenCode worker, deterministic gate, Sonnet evaluator, Claude Opus only on failure) so they run unattended in an isolated git worktree and come back as a reviewable branch. Use when the user says delegate, queue, run in the background, overnight, while I'm away, do this cheaply, or route this; when they hand over several independent implementation tasks; or when a task has clear acceptance criteria that tests or lint can check and needs no interactive decisions. Not for high-risk work (auth, payments, credentials, prod deploys, live-data migrations, sends or posts, bulk deletion), UI work that needs the sight loop, vague asks, research, or anything they want to watch step by step.
---

# Route Task

`route-task` (on PATH; installed by `agent/modules/orchestration/route-task/install.sh`) runs each task as: OpenCode economy worker, your gate commands, read-only Sonnet evaluator, up to 2 retries with feedback, one Opus call, then a human. Each task gets its own git worktree on branch `rt/<run-id>`. It never pushes or merges. Claude calls are gated on live 5h/7d usage, so it never burns the quota meant for interactive work.

## When this is the right route

Bounded implementation, repo known, success checkable by commands, nothing needing his judgment mid-way. Several independent tasks fit well (they run in parallel). If a high-risk trigger appears (auth, payments, credentials, deploy, migration on live data, sends/posts, deletions, anything `git revert` cannot undo) it is D4: do not route, confirm first. UI changes need the sight loop (`/verify-ui`): do not route. Vague ask: interview first.

## Procedure

1. **Check the fit.** If you cannot name commands that prove it is done, say so and use the normal D2 flow (or ask one question). If the relevant work is uncommitted, tell him: the worktree starts from the repo's HEAD, so uncommitted changes are not seen.
2. **Find the gate commands** from the repo itself (`package.json` scripts, `pyproject`/pytest, `Makefile`, `cargo`/`go`). Never invent them. Prefer fast, deterministic ones (tests, typecheck, lint).
3. **Write a task file** to `~/.route-task/tasks/<yyyymmdd>-<slug>.md`:

   ```markdown
   ---
   title: <short imperative title>
   repo: <absolute path of the git repo>
   tier: auto            # keyword rules send architecture/security work to premium; everything else starts on the economy worker
   gate:
     - <command that must exit 0>
   acceptance:
     - <observable criterion the evaluator will judge>
   ---
   <what to build, constraints, files to touch or avoid>
   ```

   Leave `privacy` out (he has accepted the economy model's data terms in his config). One file per independent task.
4. **Run it in the background** (it takes minutes): `route-task run <file> [<file> ...] --jobs <N>` with Bash `run_in_background: true`. A macOS notification fires on done / parked / needs-human, so do not poll in a loop.
5. **Report from the outputs, not from memory:** `route-task status`, then `route-task report <run-id>` for each run: status, branch, which agents ran, whether premium was used, what was verified, what is unresolved. Quote the report's facts.
   - `waiting_quota`: say when it resumes; `route-task resume <run-id>` later.
   - `needs_human`: show the gate/evaluator notes and propose the next step (fix in the worktree by hand, or rerun with `tier: premium`).
6. **Never push or merge for him.** Point him at the branch (`git -C <repo> diff <base>...rt/<run-id>`; T3 Code shows branches). Ask before pushing, merging, or removing the worktree (`git worktree remove`).

## Rules

- Do not edit a worktree while its run is active. Stop runs with `route-task stop` (kill switch), never `pkill`.
- A run that changed nothing counts as failed, even if the gate passes; a missing gate means "not machine-verified": say so.
- Config lives in `~/.route-task/config.toml` (models, quota ceilings, routing rules); do not change it without being asked.
