#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/jdl-core"
SERVICE="jdl-core"

echo "[deploy] Pulling latest code..."
cd "$APP_DIR"
git pull

echo "[deploy] Running setup..."
bash scripts/setup.sh

echo "[deploy] Restarting $SERVICE service..."
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE"

echo "[deploy] Checking status..."
sleep 2
sudo systemctl status "$SERVICE" --no-pager

echo "[deploy] Done"
