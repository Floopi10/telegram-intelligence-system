# Obsidian Telegram bot

Private Telegram inbox bot that turns manual messages into structured Markdown
notes. Copy `.env.example` to `.env`, configure a bot token, allowed Telegram user
IDs, OpenRouter key/model and the local Obsidian vault path.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Only IDs listed in `ALLOWED_USER_IDS` can use the bot. Keep the vault and `.env`
outside Git.

