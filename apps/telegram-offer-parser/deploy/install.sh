#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sudo apt-get update
sudo apt-get install -y python3-venv

python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"

mkdir -p "$PROJECT_DIR/data"
chmod 700 "$PROJECT_DIR/data"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
fi
chmod 600 "$PROJECT_DIR/.env"

echo "INSTALL_OK"
echo "Config: $PROJECT_DIR/.env"
