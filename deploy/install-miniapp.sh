#!/usr/bin/env bash
# Deploy the Radar mini app (app.py, index.html) to ~/obsidian-miniapp on the main VM.
# install-quality-v3.sh updates only the parser; the mini app files live apart and need this.
# A file is replaced only if the server copy matches one of the known repo versions below,
# so hand edits on the server are never overwritten. Backups: ~/obsidian-miniapp/backups/<stamp>/.
# Run as fluixoo:  bash install-miniapp.sh
set -euo pipefail

M="$HOME/obsidian-miniapp"
KNOWN=(afdc833 453532c 16d3e11)   # mini app after PR #33, PR #34, and PR #39 if it was copied by hand
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
R=$(mktemp -d)
trap 'rm -rf "$R"' EXIT
git clone -q https://github.com/Floopi10/telegram-intelligence-system.git "$R"

sha() { tr -d '\r' < "$1" | sha256sum | cut -c1-64; }
known() {
  local file=$1 rev
  for rev in "${KNOWN[@]}" HEAD; do
    [ "$(sha "$M/$file")" = "$(git -C "$R" show "$rev:apps/miniapp/$file" | tr -d '\r' | sha256sum | cut -c1-64)" ] && return 0
  done
  return 1
}

for file in app.py index.html; do
  if ! known "$file"; then
    echo "MINIAPP=SKIPPED ($file on the server differs from every known version)"
    exit 1
  fi
done

mkdir -p "$M/backups/$STAMP"
cp -a "$M/app.py" "$M/index.html" "$M/backups/$STAMP/"
cp "$R/apps/miniapp/app.py" "$R/apps/miniapp/index.html" "$M/"
"$M/.venv/bin/python" -m py_compile "$M/app.py" 2>/dev/null || python3 -m py_compile "$M/app.py"
sudo -n systemctl restart obsidian-miniapp.service
sleep 4
echo "MINIAPP=$(systemctl is-active obsidian-miniapp.service) health=$(curl -s -m 5 http://127.0.0.1:8090/health || echo none)"
grep -q 'data-tab="offers"' "$M/index.html" && echo "OFFERS_TAB=present"
echo "BACKUP=$M/backups/$STAMP"
