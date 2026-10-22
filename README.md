# Telegram Intelligence System

Open-source intelligence pipeline for Telegram, X and Obsidian. It monitors
Telegram sources available to an authorized user account, filters and deduplicates
messages, builds daily digests, analyzes X links, enriches X metrics, discovers
professional profiles, writes structured Obsidian notes and exposes a small
dashboard.

## Components

- `apps/telegram-offer-parser` — Telegram monitoring, vacancy/knowledge filters,
  semantic deduplication, Twitter radar and daily digest.
- `apps/obsidian-bot` — Telegram inbox bot that structures manually submitted
  notes and writes Markdown to an Obsidian vault.
- `apps/miniapp` — read-only web dashboard for recent results and metrics.
- `services/people-radar` — opt-in professional discovery from configured chats.
- `services/x-collector` — read-only browser collector for public X post text and
  engagement metrics.

## Safety and platform limits

- Use only accounts and chats you are authorized to access.
- Respect Telegram flood waits, X automation rules and local privacy law.
- The repository intentionally contains no sessions, cookies, tokens, chat
  exports, databases, vault contents or collected people data.
- The X collector blocks known write actions, but operators remain responsible
  for account security and platform compliance.

## Quick start

Requirements: Linux, Python 3.11+, systemd (optional), a Telegram `api_id` and
`api_hash`.

```bash
git clone https://github.com/Floopi10/telegram-intelligence-system.git
cd telegram-intelligence-system
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -e apps/telegram-offer-parser
offer-parser --help
```

Fill `.env` locally. Never commit it. Component-specific instructions are kept in
each component directory.

## License

AGPL-3.0. Network-hosted modified versions must make their corresponding source
available under the same license.

