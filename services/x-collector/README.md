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

`POST /search` with `{"query": "...", "limit": 30}` opens the "Latest" tab of X search in
the same profile and returns `{"posts": [{id, author, url, text, published_at}]}`
(`x_search.py`). A profile sent to login or to the access check answers 503: pass the
check by hand, the service does not work around it.

Use a separate X account and bind to localhost or a private network. Never expose
the endpoint without a firewall and a strong bearer token.

