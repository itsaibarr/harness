#!/usr/bin/env bash
# Idempotent installer: symlinks the launcher to ~/.local/bin/lane-runner (override dir with LANE_RUNNER_BIN_DIR).
# Re-run if the checkout moves. No other side effects.
set -euo pipefail

MOD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${LANE_RUNNER_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$BIN"
ln -sfn "$MOD/lane-runner" "$BIN/lane-runner"

echo "lane-runner installed:"
echo "  launcher  $BIN/lane-runner -> $MOD/lane-runner"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  note: $BIN is not on PATH";; esac
