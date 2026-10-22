#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="telegram-offer-parser"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "ERROR: $PROJECT_DIR/.env not found" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/data/account.session" ]]; then
  echo "ERROR: Telegram session not found. Run offer-parser auth first." >&2
  exit 1
fi

sudo tee "$UNIT_FILE" >/dev/null <<UNIT
[Unit]
Description=Telegram Offer Parser
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/offer-parser monitor
Restart=on-failure
RestartSec=10
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
Environment=OFFER_PARSER_HOME=$PROJECT_DIR
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"
