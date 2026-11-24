from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from offer_parser import outbox, x_gig_search
from offer_parser.firsay_feed import handle_item, to_event
from offer_parser.gig_offers import detect, format_gig
from offer_parser.gig_pipeline import notified_today, process_gig
from offer_parser.storage import Storage

# Shapes taken from public job channels (2024-2026) and from chat asks.
GIGS = [
    "1649 #Vacancy #Вакансия #MarketingManager #Remote\n\nWe are looking for a Reply guy / shilling-marketing manager\n\n"
    "At atm.day our main way of attracting new users is through Telegram, YouTube, Reddit and X.",
    "💼 Reply Guy\n🏛️ at Rekt Games\n🌍 Remote\n✅ Apply → https://cjl.ist/auw9zq3en",
    "Twitter контент-мейкер/копирайтер в тематике Crypto для ведения твиттер-профилей\n\n400 USDT в месяц\n\n"
    "Что делать:\n- Писать треды (10 в неделю)\n- Писать твиты (50 в день)",
    "🔥 ВАКАНСИЯ 🔥\n\nContent producer | Ghost writer в Twitter, удаленка, до $1,400 в зависимости от опыта\n\n"
    "Ищем специалиста для развития аккаунта блоггера в Twitter",
    "Специалист на комментинг, подработка\n\nМы ищем комментатора в Instagram и Threads.\n10 000 ₽ в месяц",
    "Looking for a ghostwriter for my X account. 3 threads a week, $400/month. DM me",
    "We're hiring reply guys for a new memecoin launch, paid weekly in USDT, dm @teamlead_x",
    "Ищем реплай-гаев в команду, оплата за каждый реплай, пишите в лс @hr_ct",
    "Нужен гострайтер для телеграм-канала основателя, 3 поста в неделю, бюджет 40к руб в месяц",
    "Need someone to write threads for our project account, will pay per thread, DM",
]

NOT_GIGS = [
    # people offering themselves
    "#Резюме #Copywriter #Twitter #X #Crypto\n\nИщу работу на позиции Копирайтер в Telegram / X (Twitter)\n\nЧем могу быть полезен: напишу пост, тред",
    "Ищу работу в роли копирайтера в Телеграме/Твиттере. Меня зовут Илья, пишу тексты для Твиттера 2 года",
    "I'm a ghostwriter for founders. I write threads that convert. DM me for rates",
    "Need a ghostwriter? If you need threads for your X account, DM me",
    "Do you need a Hard Shiller? I will shill your project with targeted replies under top crypto influencers. Contact me",
    # the role is named to rule it out
    "#Вакансия #SMM\n\nТребуется SMM-менеджер. Требования:\n- Опыт только ведения собственного Твиттера/реплай гай - не подходит.",
    # another trade
    "🔥ВАКАНСИЯ🔥\n\nГострайтер для книги Марии — на основе интервью написать книгу объёмом 300 страниц",
    "Молодой лейбл ищет гострайтеров на проектную занятость. Нужно хорошо писать поп и реп жанры",
    "Комментатор трансляций на немецком языке. Комментирование онлайн-трансляций в прямом эфире",
    # courses and talk
    "Как стать гострайтером и зарабатывать 200к: бесплатный вебинар в четверг, ищем гострайтеров-новичков",
    "Whether you're a creator, mod, reply guy, or KOL, this is your chance to lock in early with the project.",
    "being a reply guy is the best way to grow on X in 2026",
    "gm everyone, how is the market today",
    "Connect your wallet to claim your airdrop, we are looking for reply guys",
]


class DetectTest(unittest.TestCase):
    def test_gigs(self):
        for text in GIGS:
            self.assertTrue(detect(text).is_gig, (detect(text).reason, text[:50]))

    def test_not_gigs(self):
        for text in NOT_GIGS:
            self.assertFalse(detect(text).is_gig, text[:50])

    def test_reasons(self):
        self.assertEqual(detect(NOT_GIGS[0]).reason, "seeker")
        self.assertEqual(detect(NOT_GIGS[3]).reason, "seller")
        self.assertEqual(detect(NOT_GIGS[6]).reason, "other_trade")
        self.assertEqual(detect(NOT_GIGS[9]).reason, "course")

    def test_fields(self):
        self.assertEqual(detect(GIGS[0]).roles, ("Reply guy",))
        self.assertEqual(detect(GIGS[3]).roles, ("Гострайтер",))
        self.assertEqual(detect(GIGS[3]).pay, "$1,400")
        self.assertEqual(detect(GIGS[6]).contacts, ("@teamlead_x",))
        self.assertEqual(detect(GIGS[9]).roles, ("Автор X/TG",))

    def test_digest_is_judged_near_the_role(self):
        digest = (
            "💬 Дайджест выходного дня 💬\n\nFounder Ghostwriter for a U.S. based Google Ads agency\nПисать: https://remoteok.com/x\n\n"
            + "Редактор сайта: написание новостей, работа с авторами и корректорами. " * 6
            + "\n\nКомментатор футбольных матчей, прямой эфир, спорт"
        )
        self.assertTrue(detect(digest).is_gig, detect(digest).reason)

    def test_message_format(self):
        text = format_gig(detect(GIGS[6]), text=GIGS[6], source="CT Jobs", url="https://t.me/ctjobs/1")
        self.assertTrue(text.startswith("✍️ REPLY GUY"))
        self.assertIn("Контакт: @teamlead_x", text)
        self.assertIn("Ссылка: https://t.me/ctjobs/1", text)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "k.db")
        self.pipeline = SimpleNamespace(
            knowledge_storage=self.storage, storage=self.storage, knowledge_by_id={},
            knowledge_matcher=object(), settings=SimpleNamespace(notify_target="me"),
        )

        async def guarded(handler, storage, stream, event, text):
            await handler(event, text)

        self.pipeline._guarded = guarded
        self.env = patch.dict(os.environ, {"GIG_NOTIFY_TARGET": "bot:777"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.storage.close()
        self.temp.cleanup()

    def event(self, message_id: int, chat_id: int = -1001234567890, age_days: int = 0):
        return SimpleNamespace(
            chat_id=chat_id, id=message_id, sender=None, date=datetime.now(timezone.utc) - timedelta(days=age_days),
            get_chat=AsyncMock(return_value=SimpleNamespace(username="ctjobs")),
        )

    def outbox(self):
        return list(self.storage.connection.execute("SELECT target, body FROM outbox ORDER BY rowid"))

    async def test_gig_goes_to_the_reader_once(self):
        self.assertTrue(await process_gig(self.pipeline, self.event(1), GIGS[6], "CT Jobs"))
        self.assertTrue(await process_gig(self.pipeline, self.event(2, chat_id=-1009), GIGS[6], "Other"))
        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "bot:777")
        self.assertIn("Ссылка: https://t.me/ctjobs/1", rows[0][1])
        self.assertEqual(self.storage.connection.execute("SELECT item_kind FROM offers").fetchone()[0], "gig")
        self.assertEqual(notified_today(self.storage), 1)

    async def test_several_readers_get_a_copy_each(self):
        with patch.dict(os.environ, {"GIG_NOTIFY_TARGET": "bot:777, bot:888,bot:777"}):
            await process_gig(self.pipeline, self.event(1), GIGS[6], "CT Jobs")
        self.assertEqual([row[0] for row in self.outbox()], ["bot:777", "bot:888"])
        self.assertEqual(notified_today(self.storage), 1)

    async def test_off_without_a_target(self):
        with patch.dict(os.environ, {"GIG_NOTIFY_TARGET": ""}):
            self.assertFalse(await process_gig(self.pipeline, self.event(1), GIGS[6], "CT Jobs"))
        self.assertEqual(self.outbox(), [])

    async def test_old_posts_and_non_gigs_are_skipped(self):
        self.assertFalse(await process_gig(self.pipeline, self.event(1, age_days=30), GIGS[6], "CT Jobs"))
        self.assertFalse(await process_gig(self.pipeline, self.event(2), NOT_GIGS[2], "CT Jobs"))
        self.assertEqual(self.outbox(), [])

    async def test_daily_limit(self):
        with patch.dict(os.environ, {"GIG_NOTIFY_LIMIT": "1"}):
            await process_gig(self.pipeline, self.event(1), GIGS[6], "CT Jobs")
            await process_gig(self.pipeline, self.event(2), GIGS[7], "CT Jobs")
        self.assertEqual(len(self.outbox()), 1)

    async def test_firsay_item_is_checked_for_gigs(self):
        event, title = to_event({"url": "https://t.me/ctjobs/55", "text": GIGS[7], "sender": "hr_ct"})
        with patch.dict(os.environ, {"FIRSAY_FEED_MODE": "kol"}):
            await handle_item(self.pipeline, event, title, 10)
        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertIn("Источник: Firsay · ctjobs", rows[0][1])

    async def test_x_search_round(self):
        posts = [
            {"id": "1900000000000000001", "author": "founder", "text": GIGS[5], "published_at": datetime.now(timezone.utc).isoformat()},
            {"id": "1900000000000000002", "author": "seller", "text": NOT_GIGS[2], "published_at": None},
            {"id": "bad", "author": "x", "text": "x"},
        ]
        calls = []

        def fetch(base_url, token, query):
            calls.append(query)
            return posts if len(calls) == 1 else []

        import asyncio
        client = SimpleNamespace(base_url="http://c", token="t", _gate=asyncio.Semaphore(1))
        seen, found = await x_gig_search.search_once(self.pipeline, client, fetch_posts=fetch, pause_s=0)
        self.assertEqual((seen, found), (2, 1))
        self.assertEqual(len(calls), len(x_gig_search.DEFAULT_QUERIES))
        body = self.outbox()[0][1]
        self.assertIn("Источник: X · @founder", body)
        self.assertIn("Ссылка: https://x.com/founder/status/1900000000000000001", body)

    async def test_bot_target_uses_the_bot_api(self):
        self.storage.enqueue_message(dedupe_key="a", target="bot:777", body="hi")
        self.storage.enqueue_message(dedupe_key="b", target="me", body="own")
        client = SimpleNamespace(send_message=AsyncMock())
        with patch.object(outbox, "send_via_bot") as bot, patch.object(outbox.asyncio, "sleep", AsyncMock()):
            self.assertEqual(await outbox.drain_outbox(client, self.storage), 2)
        bot.assert_called_once_with("777", "hi")
        client.send_message.assert_awaited_once_with("me", "own", link_preview=False)


class XSearchSettingsTest(unittest.TestCase):
    def test_needs_flag_and_target(self):
        with patch.dict(os.environ, {"GIG_X_SEARCH": "1", "GIG_NOTIFY_TARGET": ""}):
            self.assertFalse(x_gig_search.enabled())
        with patch.dict(os.environ, {"GIG_X_SEARCH": "1", "GIG_NOTIFY_TARGET": "@friend"}):
            self.assertTrue(x_gig_search.enabled())

    def test_own_queries(self):
        with patch.dict(os.environ, {"GIG_X_QUERIES": 'a "b" || c'}):
            self.assertEqual(x_gig_search.queries(), ('a "b"', "c"))


# The collector lives outside the parser; the server deploy copies only the parser.
SEARCH = Path(__file__).resolve().parents[3] / "services" / "x-collector" / "x_search.py"
x_search = None
if SEARCH.exists():
    spec = importlib.util.spec_from_file_location("x_search", SEARCH)
    x_search = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(x_search)


class FakePage:
    url = "https://x.com/search"

    def __init__(self, screens):
        self.screens, self.index, self.opened = screens, 0, ""
        self.mouse = SimpleNamespace(wheel=lambda x, y: setattr(self, "index", min(self.index + 1, len(self.screens) - 1)))

    def goto(self, url, **kwargs):
        self.opened = url

    def locator(self, selector):
        return SimpleNamespace(first=SimpleNamespace(wait_for=lambda timeout: None))

    def evaluate(self, script):
        return self.screens[self.index]

    def wait_for_timeout(self, ms):
        pass


@unittest.skipIf(x_search is None, "services/x-collector is not deployed here")
class XSearchPageTest(unittest.TestCase):
    def test_posts_are_collected_across_scrolls(self):
        first = {"href": "/founder/status/11", "published_at": "2026-11-24T10:00:00.000Z", "text": "Looking for a ghostwriter"}
        page = FakePage([
            [first, {"href": "", "text": "ad without a link"}],
            [first, {"href": "/other/status/12", "published_at": None, "text": "hiring reply guys"}],
        ])
        result = x_search.search_page(page, '"looking for"  ghostwriter', scrolls=2)
        self.assertIn("q=%22looking%20for%22%20ghostwriter", page.opened)
        self.assertIn("f=live", page.opened)
        self.assertEqual([p["url"] for p in result["posts"]],
                         ["https://x.com/founder/status/11", "https://x.com/other/status/12"])

    def test_empty_query_is_rejected(self):
        with self.assertRaises(ValueError):
            x_search.search_page(FakePage([[]]), "  ")


if __name__ == "__main__":
    unittest.main()
