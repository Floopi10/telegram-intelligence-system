from __future__ import annotations

import asyncio
from urllib.parse import quote

from python_socks.async_.asyncio import Proxy

from .config import Settings


TELEGRAM_DC = "149.154.167.51"
TELEGRAM_PORT = 443


async def _direct_test() -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(TELEGRAM_DC, TELEGRAM_PORT), timeout=5
        )
        del reader
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


async def _proxy_test(settings: Settings) -> bool:
    if not settings.proxy:
        return False
    proxy_type, host, port, _rdns, username, password = settings.proxy
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"
    proxy = Proxy.from_url(f"{proxy_type}://{auth}{host}:{port}")
    sock = await proxy.connect(
        dest_host=TELEGRAM_DC,
        dest_port=TELEGRAM_PORT,
        timeout=10,
    )
    sock.close()
    return True


async def diagnose(settings: Settings) -> None:
    direct = await _direct_test()
    print(f"Прямое подключение к Telegram: {'OK' if direct else 'НЕДОСТУПНО'}")
    if not settings.proxy:
        print("Прокси: НЕ НАСТРОЕН")
        return
    proxy_type, host, port, _rdns, _username, _password = settings.proxy
    try:
        available = await _proxy_test(settings)
    except Exception as exc:
        print(f"Прокси {proxy_type}://{host}:{port}: ОШИБКА ({type(exc).__name__})")
        raise RuntimeError("Прокси настроен, но не может соединиться с Telegram") from exc
    print(f"Прокси {proxy_type}://{host}:{port}: {'OK' if available else 'НЕДОСТУПЕН'}")

