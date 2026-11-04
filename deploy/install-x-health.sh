#!/usr/bin/env bash
# Deploy: X collector alert + fresh-first metric refresh (enricher) and tag cleanup (Obsidian bot).
# Run on the main VM as fluixoo:  bash install-x-health.sh
# Every file is replaced only if the server copy is exactly the version this change was built on;
# otherwise it is skipped and reported. Backups go to ~/backups-x-health-<stamp>/.
set -euo pipefail

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$HOME/backups-x-health-$STAMP"
P="$HOME/telegram-offer-parser"
B="$HOME/obsidian-telegram-bot"
ENRICHER="$P/tools/x_metrics_enricher.py"
ENRICHER_BASE_SHA=5692a682ea17be64bba03e23528e19a8b51aa89660950ec6f21ebf8b017fb5ff
BOT_BASE=453532c

R=$(mktemp -d)
trap 'rm -rf "$R"' EXIT
git clone -q https://github.com/Floopi10/telegram-intelligence-system.git "$R"
mkdir -p "$BACKUP"

sha() { tr -d '\r' < "$1" | sha256sum | cut -c1-64; }
same_as() { [ "$(sha "$1")" = "$(git -C "$R" show "$2" | tr -d '\r' | sha256sum | cut -c1-64)" ]; }

echo "=== 1. enricher"
if [ "$(sha "$ENRICHER")" = "$ENRICHER_BASE_SHA" ]; then
  cp -a "$ENRICHER" "$BACKUP/"
  cp "$R/apps/telegram-offer-parser/tools/x_metrics_enricher.py" "$ENRICHER"
  python3 -m py_compile "$ENRICHER" && echo "ENRICHER=UPDATED"
else
  echo "ENRICHER=SKIPPED (server copy differs from the expected version)"
fi

echo "=== 2. alert flag"
DROPIN=/etc/systemd/system/x-metrics-enricher.service.d/alert.conf
LINE=$(systemctl cat x-metrics-enricher.service | grep '^ExecStart=.*x_metrics_enricher.py' | tail -1)
if [ -z "$LINE" ]; then
  echo "ALERT=SKIPPED (ExecStart not found)"
elif [[ "$LINE" == *--alert-env* ]]; then
  echo "ALERT=ALREADY_SET"
else
  sudo -n mkdir -p "$(dirname "$DROPIN")"
  printf '[Service]\nExecStart=\n%s --alert-env %s/.env\n' "$LINE" "$B" | sudo -n tee "$DROPIN" >/dev/null
  sudo -n systemctl daemon-reload && echo "ALERT=SET"
fi

echo "=== 3. bot"
if same_as "$B/bot.py" "$BOT_BASE:apps/obsidian-bot/bot.py" && same_as "$B/vault.py" "$BOT_BASE:apps/obsidian-bot/vault.py"; then
  cp -a "$B/bot.py" "$B/vault.py" "$BACKUP/"
  cp "$R/apps/obsidian-bot/bot.py" "$R/apps/obsidian-bot/vault.py" "$R/apps/obsidian-bot/tags.py" "$B/"
  "$B/.venv/bin/python" -m py_compile "$B/bot.py" "$B/vault.py" "$B/tags.py"
  sudo -n systemctl restart obsidian-telegram-bot.service; sleep 5
  echo "BOT=$(systemctl is-active obsidian-telegram-bot.service)"
else
  echo "BOT=SKIPPED (bot.py or vault.py differs from $BOT_BASE)"
fi

echo "=== 4. test run"
sudo -n systemctl start x-metrics-enricher.service || true
journalctl -u x-metrics-enricher.service -n 8 --no-pager -o cat
echo "BACKUP=$BACKUP"
