#!/usr/bin/env bash
# start-swarm-daemon.sh — foreground launcher for the always-on parallel swarm
# scanner (swarm_daemon.py), supervised by flash_supervisor.py so a crash gets
# auto-restarted instead of silently ending the scan.
#
# Meant to be the one thing every autostart path (Termux:Boot, UserLAnd's own
# autostart, a systemd user unit on plain Linux, or just `nohup ... &` by hand)
# points at — it stays in the foreground so whatever launched it can supervise
# the process (nohup/systemd/Termux:Boot all handle that fine); it does not
# background itself.
#
# Usage:
#   bash scripts/start-swarm-daemon.sh
#
# Env:
#   REPO_DIR (default: this script's repo root) — where python/ lives.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="$HOME/.flash_venv"
DATA_DIR="$HOME/.flash_loan_engine"
BOOT_LOG="$DATA_DIR/boot.log"

mkdir -p "$DATA_DIR"
{
  echo "── $(date -u +%Y-%m-%dT%H:%M:%SZ) starting swarm daemon ──"

  # Keep the CPU from being suspended mid-scan on Android/Termux. No-op
  # (silently skipped) anywhere termux-wake-lock isn't installed — e.g. plain
  # Linux, UserLAnd, or Termux without the Termux:API add-on.
  if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    echo "termux-wake-lock acquired"
  fi

  if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
  fi

  cd "$REPO_DIR/python"
  export SUPERVISOR_TARGET=swarm
  exec python3 flash_supervisor.py swarm
} >> "$BOOT_LOG" 2>&1
