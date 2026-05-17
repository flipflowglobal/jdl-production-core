#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p data

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[setup] Created .env from .env.example — edit it with your keys"
fi

echo "[setup] Installing Node dependencies..."
npm ci

echo "[setup] Compiling TypeScript..."
npx tsc

if command -v cargo &> /dev/null; then
  echo "[setup] Building Rust core..."
  cd rust-core && cargo build --release && cd ..
else
  echo "[setup] Rust toolchain not found — skipping jdl-core binary build"
fi

if command -v pip3 &> /dev/null; then
  echo "[setup] Installing Python dependencies..."
  pip3 install -r python/requirements.txt
elif command -v pip &> /dev/null; then
  echo "[setup] Installing Python dependencies..."
  pip install -r python/requirements.txt
else
  echo "[setup] pip not found — skipping Python dependencies"
fi

echo "[setup] Done"
