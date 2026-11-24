"""Gig detector for a writer: "someone is hiring a reply guy / ghostwriter".

Made for one reader (a friend of the owner) who works as a reply guy and ghostwriter. A
useful message is a buyer: a vacancy or a short ask ("looking for a ghostwriter for my X",
"ищем реплай-гаев", "#вакансия гострайтер"). People offering themselves ("ищу работу
копирайтером", "I'm a ghostwriter, DM me"), courses and agencies selling the service look
the same on keywords, so they are rejected first. Rules only, so it can run on every
message of the owner's chats, the Firsay feed and the X search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PLATFORM = (
    r"(?:x|twitter|твиттер\w*|твитер\w*|telegram|tg|телеграм\w*|тг|linkedin|линкедин\w*|threads|substack)"
)

# Named roles: enough on their own.
GHOST = (
    r"ghost[\s-]?writ(?:er|ers|ing)|гост[\s-]?райт(?:ер\w*|инг\w*)|"
    r"(?:thread|tweet|x|twitter)[\s-]writers?|"
    rf"(?:автор\w*|копирайтер\w*|редактор\w*|райтер\w*)\s+(?:в|для|на)\s+(?:{PLATFORM}|личн\w+ бренд\w*|тред\w*|канал\w*)|"
    rf"(?:{PLATFORM})[\s-](?:копирайтер\w*|райтер\w*|автор\w*|контент[\s-]?мейкер\w*)|"
    rf"(?:content|copy)[\s-]?writers?\s+(?:for|on)\s+(?:our |my |the |a )?(?:{PLATFORM})\b"
)
REPLY = (
    r"reply[\s-]?(?:guys?|gu[yi]z|girls?|men|man|team|squad|army)|repl(?:y|ies)\s+(?:specialists?|managers?|writers?)|"
    r"repliers?|commenters?|comment(?:ing)?\s+(?:team|managers?|specialists?)|"
    r"реплай[\s-]?(?:га\w+|менеджер\w*|щик\w*|мейкер\w*)|реплаер\w*|реплайщик\w*|"
    r"комментатор\w*|комментер\w*|комментинг\w*"
)
ROLE = rf"(?:{GHOST}|{REPLY})"
GHOST_RE = re.compile(rf"(?<![\w@#/])(?:{GHOST})(?!\w)", re.IGNORECASE)
REPLY_RE = re.compile(rf"(?<![\w@#/])(?:{REPLY})(?!\w)", re.IGNORECASE)

# Tasks: "need someone to write threads / reply under big accounts" without naming the role.
TASK = (
    r"(?:write|writing|draft|drafting|ghost[\s-]?write)\s+(?:my |our |the |daily |viral |\d+ )?(?:tweets?|threads?|x posts?|twitter posts?|replies)|"
    r"(?:reply|replying|comment|commenting)\s+(?:under|to|on)\s+(?:big |large |top |other |our |my |ct |crypto )?(?:accounts?|posts?|tweets?|kols?|influencers?)|"
    r"(?:писать|пишет|написание|вести)\s+(?:реплаи|комментарии|комменты|твиты|треды|посты (?:в|для) (?:твиттер\w*|x|тг|телеграм\w*))|"
    r"(?:реплаить|комментировать|оставлять (?:реплаи|комментарии|комменты))\b"
)
TASK_RE = re.compile(rf"(?<![\w@#/])(?:{TASK})", re.IGNORECASE)

SEEK_EN = r"(?:looking|searching|seeking|hunting|on the lookout) for|(?:need|needs|needed|want|wanted|require[sd]?|hiring|recruiting|onboarding|hire)"
SEEK_RU = r"(?:ищу|ищем|ищет|ищут|нужны|нужен|нужна|требуется|требуются|набираю|набираем|в поиск\w*|возьму|возьмем|возьмём|приглашаем)"
VACANCY_RE = re.compile(
    r"#?(?:ваканси\w*|vacancy|vacancies|job|jobs|hiring|работа|удаленка|удалёнка)\b|"
    r"\b(?:is|are|we're|we are|now) hiring\b|\bopen (?:role|position)s?\b|\bоткрыт\w* (?:ваканси\w*|позици\w*)|"
    r"💼|\bдайджест\w*|\bapply\b|\bоткликнуться\b|(?:^|\n)\W{0,4}(?:что делать|требования|обязанности|условия|задачи|requirements|responsibilities)\s*:",
    re.IGNORECASE,
)
HEAD_CHARS = 320  # in a vacancy post the role is named at the top

DEMAND = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"\b(?:{SEEK_EN})\b[^.\n]{{0,70}}(?<![\w@#/])(?:{ROLE})(?!\w)",
    rf"(?<![\w@#/])(?:{ROLE})(?!\w)[^.\n]{{0,30}}\b(?:needed|wanted|required|vacancy|position|role|job)\b",
    rf"(?:^|[\s(«\"]){SEEK_RU}\b[^.\n]{{0,70}}(?<![\w@#/])(?:{ROLE})(?!\w)",
    rf"(?<![\w@#/])(?:{ROLE})(?!\w)[^.\n]{{0,30}}(?:нужен|нужны|нужна|требуется|требуются)\b",
    rf"\b(?:{SEEK_EN})\b[^.\n]{{0,60}}\b(?:someone|somebody|people|person|guys?|anyone|writers?|students?)\b[^.\n]{{0,50}}(?:{TASK})",
    rf"(?:^|[\s(«\"]){SEEK_RU}\b[^.\n]{{0,60}}(?:человек\w*|люд\w+|ребят\w*|автор\w*|исполнител\w+|тех,? кто|кто)\b[^.\n]{{0,60}}(?:{TASK})",
    r"\b(?:will pay|paying|pay|paid)\b[^.\n]{0,40}\b(?:per|for each|for)\s+(?:reply|replies|comment|thread|tweet)\b",
    r"\bплач\w+\b[^.\n]{0,30}\bза\s+(?:реплай|реплаи|коммент\w*|тред\w*|твит\w*)",
))

# The writer talks about himself, or sells the service, or teaches it.
SEEKER_HARD = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"#(?:резюме|ищуработу|ищу_работу|cv|resume|помогу|услуги|lookingforjob|opentowork)\b",
    r"(?:^|[\s(«\"])(?:ищу|в поиск\w*)\s+(?:работу|подработку|заказы|заказчик\w*|клиент\w*|проект\w*|нов\w+ проект\w*|ваканси\w*)",
    r"\bрассм\w+\s+(?:предложени\w+|ваканси\w+|проект\w+)\b|\bоткрыт\w*\s+(?:к|для)\s+(?:предложени\w+|сотрудничеств\w+|работ\w+)",
    r"(?:^|[\s(«\"])я\s+(?:—|-|–)?\s*(?:\w+\s+){0,3}(?:гост[\s-]?райтер|копирайтер|автор|редактор|реплай)",
    r"\b(?:напишу|пишу для вас|возьму (?:на ведение|в работу|заказ\w*)|беру (?:заказ\w*|проект\w*|клиент\w*)|мои услуги|мое портфолио|моё портфолио|обо мне)\b",
    r"\bi(?:'m| am)\s+(?:a |an )?(?:\w+\s+){0,3}(?:ghost[\s-]?writer|copywriter|writer|reply guy|content creator|freelancer)\b",
    r"\b(?:looking|searching) for (?:a |new |my next |more )?(?:job|work|gig|gigs|clients?|opportunit\w+|role|position)\b",
    r"\bhire me\b|\bopen (?:to|for) (?:work|offers|new clients|gigs)\b",
))
# Sales talk. Counted only in short texts: a long vacancy says "our clients" about the employer.
SEEKER_SOFT = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:i|we)\s+(?:offer|provide|can write|will write|write for|help founders|help brands|ghostwrite for)\b",
    r"\b(?:my|our)\s+(?:services?|portfolio|rates?|clients?|agency)\b",
    r"\bdm (?:me )?(?:for|if you need)\b|\bif (?:you|u|your project)\s+(?:need|are looking for|want)\b",
    r"\bavailable for\b|\bbook a call\b|\bslots? (?:left|open|available)\b",
))
COURSE = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:курс\w*|вебинар\w*|марафон\w*|интенсив\w*|мастер[\s-]?класс\w*|гайд\w*|урок\w*|научу|научим|как стать|чек[\s-]?лист\w*)\b",
    r"\b(?:course|webinar|masterclass|bootcamp|cohort|workshop|how to become|learn how|free guide|newsletter issue|ebook)\b",
    r"\bhow i (?:made|make|earn|built|got)\b|\bhere(?:'s| is) how\b|\b(?:steal|copy) my\b",
))
SCAM = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bguaranteed\b", r"\bconnect (?:your )?wallet\b", r"\bclaim (?:your )?(?:airdrop|reward)\b", r"\bdouble your\b",
    r"\bsend (?:me )?\d+\s*(?:usdt|\$)\b", r"\bгарантирован\w+ (?:доход|прибыл)", r"\bбез вложений\b",
))
# Same words, another trade: book and song ghostwriters, sports and broadcast commentators.
OTHER_TRADE = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:книг\w*|рукопис\w*|мемуар\w*|биографи\w*|издательств\w*|соавтор\w*|роман\w*|лейбл\w*|песн\w*|трек\w*|рэп\w*|реп)\b",
    r"\b(?:book|books|memoir|novel|manuscript|publisher|lyrics|songs?|rap|speech(?:es)?|academic|essay|thesis|dissertation)\b",
    r"\b(?:трансляци\w*|матч\w*|эфир\w*|спорт\w*|баскетбол\w*|футбол\w*|хокке\w*|киберспорт\w*|стрим\w*|пресс[\s-]?запрос\w*)\b",
    r"\b(?:sports?|match(?:es)?|broadcast\w*|esports|live stream\w*|play[\s-]by[\s-]play)\b",
))
DIGEST_RE = re.compile(r"дайджест|подборк\w+ ваканси|пакет \d|digest|roundup", re.IGNORECASE)
CONTEXT_CHARS = 280 # a digest lists many jobs: judge the words around the role, not the whole post
SHORT_CHARS = 700  # chat asks and ads; longer texts are vacancy posts, where "our clients" is normal
# "реплай-гай не подходит", "not a reply guy role": the role is named to rule it out.
NEGATED = re.compile(
    rf"(?:{ROLE})[^.\n]{{0,25}}(?:не подход\w+|не рассматрива\w+|не нуж\w+|not (?:a fit|suitable|needed))|"
    rf"(?:\bне\s+(?:{SEEK_RU})|\b(?:not|no|don't|do not|never)\s+(?:{SEEK_EN}))[^.\n]{{0,40}}(?:{ROLE})|"
    rf"\b(?:not|no|без|не)\s+(?:a |an |just |просто )?(?:{ROLE})",
    re.IGNORECASE,
)

PAY_RE = re.compile(
    r"(?:\$\s?\d[\d,.]*\s*k?(?:\s?[-–]\s?\$?\d[\d,.]*\s*k?)?|"
    r"\b\d[\d\s,.]*\s*(?:k|к|тыс\.?)?\s*(?:usd|usdt|usdc|\$|долл\w*|баксов|руб\w*|₽|р\.|eur|€))"
    r"(?:\s*(?:per|/|a|в|за)\s*(?:reply|comment|post|tweet|thread|hour|day|week|month|mo|реплай|коммент\w*|пост|тред|твит|час|день|неделю|месяц|мес))?",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(
    r"(?<![\w/])@[A-Za-z0-9_]{4,32}|https?://t\.me/[A-Za-z0-9_+/]{3,64}|https?://(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}|"
    r"https?://[^\s<>]{6,120}|[\w.+-]+@[\w-]+\.[\w.-]{2,}"
)
DM_RE = re.compile(r"\b(?:dm|dms|pm|hmu|inbox|apply)\b|в лс|в личку|в личные|откликнуться|отклик|писать", re.IGNORECASE)
REMOTE_RE = re.compile(r"\b(?:remote|удал[её]н\w+|worldwide|anywhere)\b", re.IGNORECASE)
MAX_CHARS = 6000


@dataclass(frozen=True)
class Gig:
    is_gig: bool
    reason: str
    score: int = 0
    roles: tuple[str, ...] = ()
    pay: str = ""
    contacts: tuple[str, ...] = field(default=())
    signals: tuple[str, ...] = field(default=())


def _roles(text: str) -> tuple[str, ...]:
    roles = []
    if REPLY_RE.search(text):
        roles.append("Reply guy")
    if GHOST_RE.search(text):
        roles.append("Гострайтер")
    return tuple(roles)


def _contexts(text: str) -> list[str]:
    """The text around every role or task mention."""
    spans = [m.span() for pattern in (GHOST_RE, REPLY_RE, TASK_RE) for m in pattern.finditer(text)]
    return [text[max(0, start - CONTEXT_CHARS): end + CONTEXT_CHARS] for start, end in spans]


def detect(text: str) -> Gig:
    raw = str(text or "").replace("’", "'").replace("‘", "'")
    # Line breaks are kept: a vacancy names the role on its own line, and "[^.\n]" must stop there.
    clean = "\n".join(" ".join(line.split()) for line in raw.splitlines() if line.strip())
    if len(clean) < 20:
        return Gig(False, "too_short")
    if len(clean) > MAX_CHARS:
        return Gig(False, "long_text")
    if any(p.search(clean) for p in SCAM):
        return Gig(False, "scam")
    # A negated mention must not count as the wanted role.
    positive = NEGATED.sub(" ", clean)
    roles = _roles(positive)
    task = bool(TASK_RE.search(positive))
    if not roles and not task:
        return Gig(False, "no_role")
    if any(p.search(clean) for p in SEEKER_HARD):
        return Gig(False, "seeker")
    if len(clean) <= SHORT_CHARS and any(p.search(clean) for p in SEEKER_SOFT):
        return Gig(False, "seller")
    # One clean mention is enough: a digest may list a sports commentator next to an X ghostwriter.
    contexts = _contexts(positive)
    usable = [c for c in contexts if not any(p.search(c) for p in COURSE + OTHER_TRADE)]
    # Outside digests a post is one vacancy and its first mention names it ("Гострайтер для книги").
    if usable and usable[0] is not contexts[0] and not DIGEST_RE.search(clean[:200]):
        usable = []
    if not usable:
        return Gig(False, "course" if any(p.search(c) for c in contexts for p in COURSE) else "other_trade")
    roles = _roles("\n".join(usable))
    demand = [p.pattern[:40] for p in DEMAND if p.search(positive)]
    head = positive[:HEAD_CHARS]
    if VACANCY_RE.search(positive) and (_roles(head) or (roles and TASK_RE.search(positive))):
        demand.append("vacancy_head")
    if not demand:
        return Gig(False, "no_demand")
    pay = PAY_RE.search(clean)
    score = (
        2 * len(demand) + 2 * bool(roles) + task + 2 * bool(pay)
        + bool(DM_RE.search(clean)) + bool(REMOTE_RE.search(clean))
    )
    if score < 4:
        return Gig(False, "weak", score)
    contacts = tuple(dict.fromkeys(CONTACT_RE.findall(clean)))[:4]
    return Gig(
        True, "gig", score, roles or ("Автор X/TG",), pay.group(0).strip() if pay else "", contacts, tuple(demand),
    )


def format_gig(gig: Gig, *, text: str, source: str, url: str | None, author: str = "") -> str:
    """Message for the reader: role, pay, contact, link, then the original text."""
    body = text if len(text) <= 2500 else text[:2500] + "…"
    contact = ", ".join(gig.contacts) or (f"@{author.lstrip('@')}" if author else "в источнике")
    return "\n".join([
        "✍️ " + " + ".join(gig.roles).upper(),
        f"Оплата: {gig.pay or 'не указана'}",
        f"Контакт: {contact}",
        f"Источник: {source}",
        f"Ссылка: {url or 'нет'}",
        "",
        body,
    ])
