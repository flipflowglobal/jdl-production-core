#!/data/data/com.termux/files/usr/bin/bash
# termux-boot-swarm.sh — Termux:Boot hook for the always-on parallel swarm scanner.
#
# Install (one-time, manual — Termux:Boot only runs scripts it finds here):
#   mkdir -p ~/.termux/boot
#   ln -sf ~/jdl-production-core/scripts/termux-boot-swarm.sh ~/.termux/boot/start-flash-swarm.sh
#   chmod +x ~/.termux/boot/start-flash-swarm.sh
# Requires the Termux:Boot app (github.com/termux/termux-boot) installed and
# granted autostart permission, so Android actually runs this after reboot;
# termux-wake-lock (used by start-swarm-daemon.sh) additionally requires the
# Termux:API app + `pkg install termux-api`.
#
# Termux:Boot expects each ~/.termux/boot/ script to return quickly — it does
# NOT supervise long-running foreground processes for you, so this backgrounds
# the actual daemon (via nohup + disown) and exits immediately, rather than
# blocking the boot sequence itself.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/jdl-production-core}"

if [ ! -x "$REPO_DIR/scripts/start-swarm-daemon.sh" ]; then
  echo "termux-boot-swarm.sh: $REPO_DIR/scripts/start-swarm-daemon.sh not found or not executable — set REPO_DIR if the repo lives elsewhere." >&2
  exit 1
fi

nohup bash "$REPO_DIR/scripts/start-swarm-daemon.sh" >/dev/null 2>&1 &
disown
