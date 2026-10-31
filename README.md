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

## Quality-first digest

Twitter Radar does not fill a fixed quota. It separates metric-confirmed winners,
early signals and promising mechanics, requires visible and internally consistent
X metrics, ranks by velocity and engagement before LLM score, and drops low-view
posts that only received a high text-analysis score. A post needs at least 500
views to become a relative winner; posts with 100-499 views can only be labelled
as early signals. Vacancy and knowledge sections also re-check geography, role
relevance, campaign spam, greetings and conversational fragments before the daily
digest is rendered.

When a Telegram message explicitly reports an outcome next to an X link, the
radar also extracts follower gain, the result window, views-to-followers
conversion, revenue and content format. These claims remain labelled as
self-reported: they improve ordering and explainability, but cannot make a post a
winner without metrics collected directly from X.

Each digest covers everything since the previous one (at most 72 hours) and
re-reads X metrics for up to 30 candidates first, so a post is judged by its current
result rather than the moment it was first seen. A later snapshot can confirm a post
but never demote it.

Digest delivery retries temporary Telegram failures every five minutes instead of
silently stopping, and a retry skips the parts already delivered. Instant
notifications go through a persistent outbox, so a Telegram failure or flood wait
delays them instead of losing them. Operators can perform a controlled catch-up with
`offer-parser send-digest --force` while the monitor service is stopped.

## License

AGPL-3.0. Network-hosted modified versions must make their corresponding source
available under the same license.

