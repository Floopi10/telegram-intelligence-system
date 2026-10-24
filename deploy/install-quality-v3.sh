#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:?Set PROJECT_DIR to the existing telegram-offer-parser directory}"
SERVICE_NAME="${SERVICE_NAME:-telegram-offer-parser.service}"
SERVICE_USER="${SERVICE_USER:-$(stat -c '%U' "$PROJECT_DIR")}"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/apps/telegram-offer-parser"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PROJECT_DIR/data/backups/quality-v3-$STAMP"

FILES=(
  offer_parser/knowledge_balance.py
  offer_parser/twitter_ai.py
  offer_parser/twitter_evidence.py
  offer_parser/twitter_niches.py
  offer_parser/service.py
  offer_parser/digest.py
  offer_parser/storage.py
  offer_parser/digest_guard.py
  offer_parser/vacancy_filter.py
  tests/test_twitter_radar_v27.py
  tests/test_twitter_evidence.py
  tests/test_knowledge_context.py
  tests/test_vacancy_filter.py
  tests/test_v29.py
  tests/test_digest_dedupe.py
)

[[ $EUID -eq 0 ]] || exec sudo --preserve-env=PROJECT_DIR,SERVICE_NAME,SERVICE_USER "$0" "$@"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Python venv not found" >&2; exit 66; }

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$BACKUP_DIR"
for relative in "${FILES[@]}"; do
  [[ -f "$SOURCE_DIR/$relative" ]] || { echo "Missing source: $relative" >&2; exit 66; }
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    "$BACKUP_DIR/$(dirname "$relative")"
  if [[ -f "$PROJECT_DIR/$relative" ]]; then
    cp -a "$PROJECT_DIR/$relative" "$BACKUP_DIR/$relative"
  fi
done

restore() {
  for relative in "${FILES[@]}"; do
    if [[ -f "$BACKUP_DIR/$relative" ]]; then
      install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
        "$BACKUP_DIR/$relative" "$PROJECT_DIR/$relative"
    fi
  done
  systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
}
trap restore ERR

systemctl stop "$SERVICE_NAME"
for relative in "${FILES[@]}"; do
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    "$PROJECT_DIR/$(dirname "$relative")"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "$SOURCE_DIR/$relative" "$PROJECT_DIR/$relative"
done

runuser -u "$SERVICE_USER" -- bash -lc \
  "cd '$PROJECT_DIR' && .venv/bin/python -m unittest discover -s tests -p 'test_*.py'"
systemctl start "$SERVICE_NAME"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME"

trap - ERR
echo "QUALITY_V3=PASS"

