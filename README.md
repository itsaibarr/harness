# harness

Evidence-based agent harness for solo dev work (built Aug 2026, Claude Code era → pi-portable). Every adoption in here survived a PoC; every rejection has evidence: `docs/agent-harness/tool-evaluation.md` + `decision-log.md`.

## Load into a project

```bash
bash ~/harness/bootstrap.sh [--web] [--automation]   # idempotent; run from project root
```

Gives the project: orientation CLAUDE.md (150-line cap), permissions starter, doc index. Guide: `docs/agent-harness/new-project-bootstrap.md`.

## Layers

- `agent/core/` — orientation template, doc-index generator, task-router, slash commands, framework masters (agent-skills 7-skill core + ponytail)
- `agent/modules/web-development/` — sight loop (verify-ui.mjs + odiff), scoped MCP template, perf recipes
- `agent/modules/automation/` — Mailpit verify-before-send gate, dead-man monitor, secrets hardening
- `agent/modules/research/` — evidence templates, Graphify break-glass, MemPalace search recipes
- `agent/modules/pi-stack/` — pi+cmux stack (permissions extension, install/first-launch checklist)
- `docs/agent-harness/` — the full record: baseline audit, operating principles, evaluations (3 rounds), plans, validation report, framework comparison (+PDF), transition plan, decision log

## Global wiring (machine-level, not per-project)

Skills/commands live in `~/.claude/{skills,commands}` (Claude Code + OpenCode read them; pi points at them via `~/.pi/agent/settings.json`). Masters for all of it are under `agent/core/framework/` — restore with a copy. pi permissions extension master: `agent/modules/pi-stack/permissions.ts` → `~/.pi/agent/extensions/`.
