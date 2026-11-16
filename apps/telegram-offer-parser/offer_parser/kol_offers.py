"""KOL demand detector: "someone is looking for KOLs / creators and will pay".

The owner is a KOL. A useful offer is a buyer asking for creators ("looking for KOLs",
"need AI creators", "$30 per KOL", "нужны ИИ креаторы, цена в лс"). Service sellers
("20 Telegram channels + 15 X accounts", "I offer promotion"), scams, guides and
mutual-follow calls look similar on keywords, so they are rejected first. Rules only:
cheap enough for every message from the owner's chats and the Firsay feed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Who is wanted.
TALENT = (
    r"kols?|k\.o\.l|creators?|content creators?|influencers?|ambassadors?|shillers?|callers?|"
    r"yappers?|posters?|promoters?|profiles?|accounts?|pages?|tweeters?|clippers?|"
    r"колов|колы|кол|креатор\w*|блогер\w*|инфлюенсер\w*|амбассадор\w*|автор\w*|аккаунт\w*"
)
TALENT_RE = re.compile(rf"\b(?:{TALENT})\b", re.IGNORECASE)

# Primary signals: at least one is required.
DEMAND = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"\b(?:looking|searching|seeking|hunting) for\b[^.\n]{{0,60}}\b(?:{TALENT})\b",
    rf"\b(?:need|needs|needed|want|wanted|require[sd]?|hiring|recruiting|onboarding)\b[^.\n]{{0,40}}\b(?:{TALENT})\b",
    rf"\b(?:{TALENT})\b[^.\n]{{0,25}}\b(?:needed|wanted|required)\b",
    r"\b(?:calling all|attention|hello|hey|gm|yo)\b[^.\n]{0,15}\b(?:kols?|creators?|influencers?)\b",
    r"\b(?:paid|sponsored)\s+(?:post(?:ing|s)?|promo(?:tion)?|campaign|collab(?:oration)?|gig|offer|partnership|tweets?|deal)\b",
    r"\b(?:will|we'll|gonna|going to)\s+pay\b",
    r"\$\s?\d[\d,.]*\s*k?\s*(?:per|/|for each|each)\s*(?:kol|post|tweet|creator|week|thread)\b",
    r"\b\d[\d,.]*\s*(?:usd|usdt|usdc|\$)\s*(?:per|/|for each|each)\s*(?:kol|post|tweet|creator|week)\b",
    r"\bpay(?:s|ing)?\b[^.\n]{0,20}\b(?:per|for each)\s+(?:kol|post|tweet|creator)\b",
    rf"(?:^|\s)(?:ищу|ищем|нужны|нужен|нужна|требуются|набираю|набираем|собираю|собираем)\b[^.\n]{{0,40}}(?:{TALENT})",
    r"\bоплат\w*\b[^.\n]{0,25}\b(?:за пост|за публикац|за твит)",
    r"\bплатн\w+\s+(?:пост|размещени|коллаб|интеграци)",
))

# Supporting signals: add to the score, never make an offer alone
# ("кидайте ссылки на свои аккаунты" is mutual following, not a gig).
SUPPORT = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:drop|send|share)\b[^.\n]{0,25}\b(?:your |ur )?(?:links?|profiles?|accounts?|x|twitter|handles?|rates?|price)\b",
    r"(?:^|\s)(?:с|c)\s+(?:ценой|прайсом|ценами)\b",
    r"(?:^|\s)(?:скиньте|кидайте|пришлите|присылайте|оставляйте)\b[^.\n]{0,25}(?:прайс|цен)",
    r"\b(?:rates?|budget|compensation|payment)\b",
))

SELLER = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bi(?:'m| am)\s+(?:a|an)?\s*(?:kol|creator|influencer|marketer|promoter)\b",
    r"\b(?:i|we)\s+(?:offer|provide|can promote|can shill|can boost|will promote|do promotion)",
    r"\b(?:my|our)\s+(?:services?|rates?|price list|channels?|network|reach|audience|portfolio)\b",
    r"\b(?:project owners|for projects|your project|your token|your coin|boost your|grow your|promote your)\b",
    r"\b\d+\s*(?:telegram|tg)\s*channels?\b",
    r"\breach (?:investors|thousands|millions)\b",
    r"\b(?:promotion|marketing|shill|ama)\s+(?:package|service|offer)s?\b",
    r"\b(?:available for|open for|accepting)\s+(?:promos?|paid|collabs?|partnerships?|deals?)\b",
    r"\bhire me\b|\bdm (?:me )?for (?:promo|promotion|rates|price)\b",
    r"\btarget:\s*\$",
    r"\bпредлагаю\b|\bмои услуги\b|\bмой канал\b|\bпродвижение (?:вашего|вашей|ваших|проекта)\b|\bраскручу\b|\bоткрыт(?:а)? к сотрудничеству\b",
))

SCAM = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bguaranteed (?:profit|returns?)\b", r"\bconnect (?:your )?wallet\b", r"\bclaim (?:your )?(?:airdrop|reward)\b",
    r"\bsend (?:me )?\d+\s*(?:usdt|\$)\b", r"\bdouble your\b", r"\bpump signal", r"\bгарантирован\w+ (?:доход|прибыл)",
))

NEGATED_RU = re.compile(r"\bне\s+(?:нужн|ищ|требу)", re.IGNORECASE)
MAX_UNPAID_CHARS = 900  # real asks are short; long texts are guides and digests
PER_UNIT_PAY_RE = re.compile(
    r"(?:\$\s?\d[\d,.]*\s*k?|\b\d[\d,.]*\s*k?\s*(?:usd|usdt|usdc|\$))\s*(?:per|/|for each|each)\s*(?:kol|post|tweet|creator|thread)\b"
    r"|\b\d[\d,.]*\s*(?:\$|usdt|долл\w*)\s*за\s*(?:пост|публикац|твит)",
    re.IGNORECASE,
)

NICHES = (
    ("AI", r"\b(?:ai|a\.i\.|artificial intelligence|llm|gpt|claude|grok|agents?|ии|нейросет\w*)\b|_ai\b"),
    ("Финансы", r"\b(?:stocks?|market|finance|financial|trading|traders?|forex|investing|акци\w*|финанс\w*|трейд\w*)\b"),
    ("Web3/CT", r"\b(?:web3|crypto|ct|defi|nft|memecoin|meme coin|solana|sol|base|bnb|eth|airdrop|крипт\w*)\b"),
    ("Мемы", r"\b(?:meme|memes|memecoins?|мем\w*)\b"),
    ("Гейминг", r"\b(?:gaming|gamers?|game|esports|игр\w*)\b"),
    ("Видео", r"\b(?:tiktok|youtube|reels|shorts|ugc|clippers?|видео)\b"),
)

PAY_RE = re.compile(
    r"(?:\$\s?\d[\d,.]*\s*k?|\b\d[\d,.]*\s*k?\s*(?:usd|usdt|usdc|\$|долл\w*|баксов))"
    r"(?:\s*(?:per|/|for each|each|за)\s*(?:kol|post|tweet|creator|week|month|пост|неделю|месяц))?",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(r"(?:@[A-Za-z0-9_]{4,32}|https?://t\.me/[A-Za-z0-9_+/]{3,64}|https?://(?:x|twitter)\.com/[A-Za-z0-9_]{1,15})")
DM_RE = re.compile(r"\b(?:dm|dms|pm|hmu|inbox)\b|в лс|в личку|в личные", re.IGNORECASE)


@dataclass(frozen=True)
class KolOffer:
    is_offer: bool
    reason: str
    score: int = 0
    niches: tuple[str, ...] = ()
    pay: str = ""
    contacts: tuple[str, ...] = field(default=())
    signals: tuple[str, ...] = field(default=())


def detect(text: str) -> KolOffer:
    clean = " ".join(str(text or "").split())
    if len(clean) < 20:
        return KolOffer(False, "too_short")
    if any(p.search(clean) for p in SCAM):
        return KolOffer(False, "scam")
    if any(p.search(clean) for p in SELLER):
        return KolOffer(False, "seller")
    demand = [p.pattern[:40] for p in DEMAND if p.search(clean)]
    pay = PAY_RE.search(clean)
    if demand and len(demand) == 1 and NEGATED_RU.search(clean) and not pay:
        demand = []
    if not demand:
        return KolOffer(False, "no_demand")
    if len(clean) > MAX_UNPAID_CHARS and not PER_UNIT_PAY_RE.search(clean):
        # Guides and digests mention prices too ("$1.7 a month for a proxy"); a long real
        # brief names a rate per KOL / post.
        return KolOffer(False, "long_text")
    talent = bool(TALENT_RE.search(clean))
    if not talent:
        return KolOffer(False, "no_talent")
    dm = bool(DM_RE.search(clean))
    support = sum(bool(p.search(clean)) for p in SUPPORT)
    score = 2 * len(demand) + 2 * talent + 2 * bool(pay) + dm + support
    if score < 5:
        return KolOffer(False, "weak", score)
    niches = tuple(name for name, pattern in NICHES if re.search(pattern, clean, re.IGNORECASE))
    contacts = tuple(dict.fromkeys(CONTACT_RE.findall(clean)))[:4]
    return KolOffer(True, "kol_demand", score, niches, pay.group(0).strip() if pay else "", contacts, tuple(demand))


def format_offer(offer: KolOffer, *, text: str, chat_title: str, url: str | None, author: str = "") -> str:
    """Message for Saved Messages: niche, pay, contact, link, then the original text."""
    body = text if len(text) <= 1500 else text[:1500] + "…"
    contact = ", ".join(offer.contacts) or (f"@{author.lstrip('@')}" if author else "в чате")
    return "\n".join([
        "💸 ИЩУТ KOL" + (f" · {', '.join(offer.niches)}" if offer.niches else ""),
        f"Оплата: {offer.pay or 'не указана'}",
        f"Контакт: {contact}",
        f"Чат: {chat_title}",
        f"Ссылка: {url or 'нет'}",
        "",
        body,
    ])
