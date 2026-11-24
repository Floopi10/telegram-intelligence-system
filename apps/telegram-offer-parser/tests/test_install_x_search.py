from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

# The installer lives in the repo's deploy/ folder; the server deploy copies only the parser.
TOOL = Path(__file__).resolve().parents[3] / "deploy" / "install_x_search.py"
installer = None
if TOOL.exists():
    spec = importlib.util.spec_from_file_location("install_x_search", TOOL)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

# Shaped like the server's own x_service.py: stale profile locks are removed before launch.
SERVICE = '''import hmac
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

TOKEN = os.environ["X_COLLECTOR_TOKEN"]
PROFILE = Path.home() / "x-collector" / "chrome-profile"
LOCK = threading.Lock()
BLOCKED = ("CreateTweet", "FavoriteTweet")


def collect(url):
    match = re.match(r"https://x.com/(\\w+)/status/(\\d+)", url)
    if not match:
        raise ValueError("bad url")
    with LOCK, sync_playwright() as playwright:
        for name in ("SingletonLock", "SingletonCookie"):
            (PROFILE / name).unlink(missing_ok=True)
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            args=["--disable-gpu"],
        )
        try:
            return {"url": url}
        finally:
            context.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != "/collect":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"collected": True}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
'''


class FakeContext:
    closed = 0
    pages = [types.SimpleNamespace(route=lambda pattern, guard: None, name="page")]

    def close(self):
        FakeContext.closed += 1


class FakePlaywright:
    def __enter__(self):
        launch = lambda *args, **kwargs: FakeContext()  # noqa: E731
        return types.SimpleNamespace(chromium=types.SimpleNamespace(launch_persistent_context=launch))

    def __exit__(self, *args):
        return False


def fake_modules(search_page):
    class LoginRequired(RuntimeError):
        pass

    playwright = types.ModuleType("playwright.sync_api")
    playwright.sync_playwright = FakePlaywright
    search = types.ModuleType("x_search")
    search.search_page, search.LoginRequired = search_page, LoginRequired
    return {"playwright": types.ModuleType("playwright"), "playwright.sync_api": playwright, "x_search": search}, LoginRequired


@unittest.skipIf(installer is None, "deploy/install_x_search.py is not deployed here")
class InstallXSearchTest(unittest.TestCase):
    def serve(self, search_page):
        modules, login_required = fake_modules(search_page)
        namespace = {"__name__": "x_service_under_test"}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {"X_COLLECTOR_TOKEN": "secret"}):
            exec(compile(installer.patch_source(SERVICE), "x_service.py", "exec"), namespace)
        server = ThreadingHTTPServer(("127.0.0.1", 0), namespace["Handler"])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.env = patch.dict(os.environ, {"X_COLLECTOR_TOKEN": "secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        return f"http://127.0.0.1:{server.server_address[1]}", login_required

    def post(self, base, path, payload, token="secret"):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            return error.code, json.loads(body) if body else {}

    def test_search_route_works_and_collect_is_untouched(self):
        seen = []

        def search_page(page, query, limit=30):
            seen.append((page.name, query, limit))
            return {"posts": [{"id": "1", "author": "a", "text": "hiring a ghostwriter"}]}

        base, _ = self.serve(search_page)
        closed = FakeContext.closed
        status, body = self.post(base, "/search", {"query": "ghostwriter", "limit": 5})
        self.assertEqual((status, body["posts"][0]["author"]), (200, "a"))
        self.assertEqual(seen, [("page", "ghostwriter", 5)])
        self.assertEqual(FakeContext.closed, closed + 1)
        self.assertEqual(self.post(base, "/collect", {"url": "x"}), (200, {"collected": True}))
        self.assertEqual(self.post(base, "/other", {})[0], 404)

    def test_search_needs_the_token_and_reports_errors(self):
        def search_page(page, query, limit=30):
            if query == "login":
                raise login_required("X profile needs login")
            raise ValueError("Search query is empty or too long")

        base, login_required = self.serve(search_page)
        self.assertEqual(self.post(base, "/search", {"query": "x"}, token="wrong")[0], 401)
        self.assertEqual(self.post(base, "/search", {"query": ""})[0], 400)
        self.assertEqual(self.post(base, "/search", {"query": "login"})[0], 503)

    def test_patch_is_applied_once_and_keeps_the_launch(self):
        patched = installer.patch_source(SERVICE)
        self.assertEqual(installer.patch_source(patched), patched)
        self.assertEqual(patched.count('(PROFILE / name).unlink(missing_ok=True)'), 2)
        self.assertEqual(patched.count('args=["--disable-gpu"],'), 2)
        self.assertTrue(patched.rstrip().endswith("serve_forever()"))

    def test_unknown_layout_is_refused(self):
        with self.assertRaises(installer.CannotPatch):
            installer.patch_source(SERVICE.replace("def collect(url):", "def gather(url):"))
        with self.assertRaises(installer.CannotPatch):
            installer.patch_source(SERVICE.replace("str(PROFILE),", "str(PROFILE / match.group(1)),"))

    def test_install_backs_up_and_test_mode_hides_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "x_service.py").write_text(SERVICE, encoding="utf-8")
            self.assertEqual(installer.install(folder), 0)
            self.assertIn(installer.MARKER, (folder / "x_service.py").read_text(encoding="utf-8"))
            self.assertTrue((folder / "x_search.py").exists())
            backups = list(folder.glob("x_service.py.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), SERVICE)
            self.assertEqual(installer.install(folder), 0)
            self.assertEqual(len(list(folder.glob("x_service.py.bak-*"))), 1)
            self.assertEqual(installer.install(folder / "missing"), 66)
            self.assertEqual(installer.test(folder), 2)


if __name__ == "__main__":
    unittest.main()
