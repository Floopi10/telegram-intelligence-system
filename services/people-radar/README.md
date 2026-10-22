# People Radar

Scans messages from explicitly configured Telegram communities, enriches visible
profiles with bio data, detects professional roles and sends an XLSX export to
Saved Messages. KOL-only profiles are excluded by default.

Use only in communities you may lawfully access. Respect `FloodWait`; the default
profile delay is deliberately conservative. Do not publish generated databases or
spreadsheets.

```bash
cp sources.example.json sources.json
PYTHONPATH=../../apps/telegram-offer-parser \
  python people_radar.py \
  --sources sources.json \
  --database data/people-radar.db \
  --session data/people-radar \
  --output-dir exports
```

