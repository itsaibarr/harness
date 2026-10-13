#!/usr/bin/env bash
# Idempotent installer: makes route-task a native part of the harness on this machine.
#   - ~/.local/bin/route-task            -> this checkout's launcher (symlink; re-run if the checkout moves)
#   - ~/.claude/skills/route-task        <- skill master (copy; Claude Code and OpenCode both read ~/.claude/skills)
#   - ~/.claude/commands/route.md        <- /route command master (copy)
#   - ~/.route-task/config.toml          <- example config, only if you have none (never overwrites your edits)
# Usage: bash agent/modules/orchestration/route-task/install.sh
set -euo pipefail

MOD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW="$(cd "$MOD/../../../core/framework" && pwd)"
BIN="${ROUTE_TASK_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$BIN" "$HOME/.claude/skills/route-task" "$HOME/.claude/commands" "$HOME/.route-task/tasks"
ln -sfn "$MOD/route-task" "$BIN/route-task"
cp "$FW/skills/route-task/SKILL.md" "$HOME/.claude/skills/route-task/SKILL.md"
cp "$FW/commands/route.md" "$HOME/.claude/commands/route.md"
[ -e "$HOME/.route-task/config.toml" ] || cp "$MOD/route-task.example.toml" "$HOME/.route-task/config.toml"

echo "route-task installed:"
echo "  launcher  $BIN/route-task -> $MOD/route-task"
echo "  skill     $HOME/.claude/skills/route-task/SKILL.md"
echo "  command   $HOME/.claude/commands/route.md"
echo "  config    $HOME/.route-task/config.toml"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  note: $BIN is not on PATH";; esac
