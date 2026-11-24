#!/usr/bin/env python3
"""Add POST /search to the X collector that runs on the server.

The server's x_service.py is its own patched version, so this does not replace it. It
copies x_search.py next to it and appends one block that reuses the service's own browser
launch (taken from collect()) and wraps the request handler. The old file is kept as
x_service.py.bak-<time>; a second run changes nothing.

  python install_x_search.py            install (then restart the x-collector service)
  python install_x_search.py --test     ask the running service for one search

No git on that VM: x_search.py is downloaded from the public repo unless it lies next to
this script.
"""
from __future__ import annotations

import ast
import json
import os
import py_compile
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/Floopi10/telegram-intelligence-system/main/services/x-collector/x_search.py"
MARKER = "# --- x_search: POST /search (added by deploy/install_x_search.py) ---"
TOKEN_ENV = "X_COLLECTOR_TOKEN"

BLOCK = '''
{marker}
import x_search as _x_search


def _search_guard(route):
    if any(value in route.request.url for value in BLOCKED):
        route.abort()
    else:
        route.continue_()


def search(query, limit=30):
    """One read-only search in the collector's profile; LOCK keeps one browser at a time."""
    with LOCK, sync_playwright() as playwright:
{launch}
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.route("**/*", _search_guard)
            return _x_search.search_page(page, query, limit=limit)
        finally:
            context.close()


def _search_reply(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


_collect_do_POST = Handler.do_POST


def _do_POST(self):
    if urlparse(self.path).path != "/search":
        return _collect_do_POST(self)
    supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
    expected = os.environ.get("{token_env}", "")
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        return _search_reply(self, 401, {{"error": "unauthorized"}})
    try:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            raise ValueError("Invalid request size")
        payload = json.loads(self.rfile.read(length))
        _search_reply(self, 200, search(str(payload.get("query", "")), int(payload.get("limit", 30))))
    except (ValueError, json.JSONDecodeError) as error:
        _search_reply(self, 400, {{"error": str(error)}})
    except _x_search.LoginRequired as error:
        _search_reply(self, 503, {{"error": str(error)}})
    except Exception as error:
        _search_reply(self, 502, {{"error": f"search_failed: {{type(error).__name__}}"}})


Handler.do_POST = _do_POST
# --- x_search: end ---

'''


class CannotPatch(RuntimeError):
    pass


def launch_lines(source: str, tree: ast.Module) -> str:
    """The lines of collect() that open the browser: from the start of its `with` to `context = ...`."""
    collect = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "collect"), None)
    if collect is None:
        raise CannotPatch("collect() not found")
    block = next((n for n in ast.walk(collect) if isinstance(n, ast.With) and any(
        isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == "playwright" for item in n.items
    )), None)
    if block is None:
        raise CannotPatch("`with ... as playwright` not found in collect()")
    if block.col_offset != 4:
        raise CannotPatch("unexpected indentation of the playwright block")
    preamble = []
    for statement in block.body:
        preamble.append(statement)
        targets = statement.targets if isinstance(statement, ast.Assign) else []
        if any(isinstance(t, ast.Name) and t.id == "context" for t in targets):
            break
    else:
        raise CannotPatch("`context = ...` not found in collect()")
    # The copied lines must not need anything collect() computed from its url.
    local = {arg.arg for arg in collect.args.args}
    for node in ast.walk(collect):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.lineno < block.lineno:
            local.add(node.id)
    used = {n.id for s in preamble for n in ast.walk(s) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    if used & local:
        raise CannotPatch(f"browser launch depends on collect() locals: {sorted(used & local)}")
    lines = source.splitlines()
    return "\n".join(lines[preamble[0].lineno - 1: preamble[-1].end_lineno])


def patch_source(source: str) -> str:
    """x_service.py with the /search block added; unchanged if it is already there."""
    if MARKER in source:
        return source
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for needed in ("LOCK", "BLOCKED", "sync_playwright", "urlparse", "hmac", "json", "os"):
        if needed not in names and f"import {needed}" not in source:
            raise CannotPatch(f"{needed} is not used in x_service.py")
    if TOKEN_ENV not in source:
        raise CannotPatch(f"{TOKEN_ENV} is not read by x_service.py")
    handler = next((i for i, n in enumerate(tree.body) if isinstance(n, ast.ClassDef) and n.name == "Handler"), None)
    if handler is None or "do_POST" not in {n.name for n in tree.body[handler].body if isinstance(n, ast.FunctionDef)}:
        raise CannotPatch("class Handler with do_POST not found")
    if handler + 1 >= len(tree.body):
        raise CannotPatch("nothing starts the server after class Handler")
    block = BLOCK.format(marker=MARKER, launch=launch_lines(source, tree), token_env=TOKEN_ENV)
    lines = source.splitlines(keepends=True)
    at = tree.body[handler + 1].lineno - 1  # before whatever starts the server
    return "".join(lines[:at]) + block.lstrip("\n") + "\n" + "".join(lines[at:])


def install(directory: Path) -> int:
    service = directory / "x_service.py"
    if not service.exists():
        print(f"Нет файла {service}: это не машина с X-коллектором.")
        return 66
    source = service.read_text(encoding="utf-8")
    try:
        patched = patch_source(source)
    except (CannotPatch, SyntaxError) as error:
        print(f"Ничего не изменено: x_service.py устроен иначе, чем ожидалось ({error}).")
        return 65
    module = directory / "x_search.py"
    beside = Path(__file__).resolve().parent.parent / "services" / "x-collector" / "x_search.py" if "__file__" in globals() else None
    if beside is not None and beside.exists():
        shutil.copyfile(beside, module)
    else:
        with urllib.request.urlopen(RAW, timeout=60) as response:
            module.write_bytes(response.read())
    py_compile.compile(str(module), doraise=True)
    if patched == source:
        print("Маршрут /search уже установлен, x_search.py обновлён.")
        return 0
    backup = directory / f"x_service.py.bak-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    shutil.copyfile(service, backup)
    candidate = directory / "x_service.py.new"
    candidate.write_text(patched, encoding="utf-8")
    try:
        py_compile.compile(str(candidate), doraise=True)
    except py_compile.PyCompileError as error:
        candidate.unlink()
        print(f"Ничего не изменено: новый файл не собирается ({error.msg}).")
        return 65
    candidate.replace(service)
    print(f"Готово. Старый файл: {backup.name}")
    print(f"Откат: cp {backup} {service} && sudo systemctl restart x-collector")
    return 0


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("'\"")
    return values


def test(directory: Path) -> int:
    env = read_env(directory / ".env")
    token = env.get(TOKEN_ENV, "")
    if not token:
        print(f"{TOKEN_ENV} не найден в {directory / '.env'}")
        return 2
    host = os.environ.get("X_COLLECTOR_BIND") or env.get("X_COLLECTOR_BIND") or "10.128.0.3"
    port = os.environ.get("X_COLLECTOR_PORT") or env.get("X_COLLECTOR_PORT") or "8765"
    request = urllib.request.Request(
        f"http://{host}:{port}/search",
        data=json.dumps({"query": '"looking for" ("ghostwriter" OR "reply guy") -filter:replies', "limit": 10}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=170) as response:
            posts = json.loads(response.read().decode("utf-8")).get("posts", [])
    except urllib.error.HTTPError as error:
        print(f"Поиск не сработал: HTTP {error.code} {error.read().decode('utf-8', 'replace')[:200]}")
        return 1
    except OSError as error:
        print(f"Сервис не отвечает на {host}:{port}: {type(error).__name__}")
        return 1
    print(f"Поиск работает, постов: {len(posts)}")
    for post in posts[:5]:
        print(f"  @{post.get('author')}: {str(post.get('text', '')).replace(chr(10), ' ')[:110]}")
    return 0


def main() -> int:
    directory = Path(os.environ.get("X_COLLECTOR_DIR", str(Path.home() / "x-collector")))
    return test(directory) if "--test" in sys.argv[1:] else install(directory)


if __name__ == "__main__":
    raise SystemExit(main())
