#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:?Set PROJECT_DIR}"
BACKUP_DIR="${BACKUP_DIR:?Set BACKUP_DIR returned by the installer backup location}"
SERVICE_NAME="${SERVICE_NAME:-telegram-offer-parser.service}"
SERVICE_USER="${SERVICE_USER:-$(stat -c '%U' "$PROJECT_DIR")}"

[[ $EUID -eq 0 ]] || exec sudo --preserve-env=PROJECT_DIR,BACKUP_DIR,SERVICE_NAME,SERVICE_USER "$0" "$@"
[[ -d "$BACKUP_DIR" ]] || { echo "Backup not found" >&2; exit 66; }

systemctl stop "$SERVICE_NAME"
while IFS= read -r -d '' source; do
  relative="${source#"$BACKUP_DIR/"}"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    "$PROJECT_DIR/$(dirname "$relative")"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "$source" "$PROJECT_DIR/$relative"
done < <(find "$BACKUP_DIR" -type f -print0)
systemctl start "$SERVICE_NAME"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME"
echo "QUALITY_V3_ROLLBACK=PASS"

