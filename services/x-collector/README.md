# X collector

Read-only Playwright service that uses a dedicated authenticated Chrome profile.
It accepts direct status URLs and returns public post text and visible metrics.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export X_COLLECTOR_TOKEN="$(openssl rand -base64 48)"
python x_service.py
```

Use a separate X account and bind to localhost or a private network. Never expose
the endpoint without a firewall and a strong bearer token.

