from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class KnowledgeBalanceDecision:
    accepted: bool
    primary_niche: str
    niches: tuple[str, ...]
    reason: str


NICHE_PATTERNS: dict[str, tuple[str, ...]] = {
    "AI": (
        r"\bai\b", r"artificial intelligence", r"\bllm\b", r"\bagents?\b",
        r"claude", r"chatgpt", r"openai", r"gemini", r"deepseek", r"qwen",
        r"machine learning", r"neural", r"нейросет", r"искусственн\w+ интеллект",
        r"промпт", r"\bии\b", r"\bагент(?:а|ы|ов|ом|ами|ский|ские|ских)?\b",
    ),
    "Роботы": (
        r"robot", r"robotics", r"humanoid", r"embodied", r"autonomous",
        r"drone", r"optimus", r"робот", r"гуманоид", r"автономн", r"дрон",
    ),
    "Финансы": (
        r"financ", r"trading", r"trader", r"market", r"yield", r"portfolio",
        r"invest", r"macro", r"funding rate", r"derivative", r"perpetual",
        r"prediction market", r"финанс", r"трейдинг", r"рынок", r"доходност",
        r"инвест", r"портфел", r"фандинг",
    ),
    "Web3": (
        r"web3", r"crypto", r"blockchain", r"onchain", r"on-chain", r"token",
        r"protocol", r"airdrop", r"\bdefi\b", r"wallet", r"\bnft\b", r"\bl[12]\b",
        r"крипт", r"блокчейн", r"ончейн", r"токен", r"протокол", r"аирдроп",
    ),
    "BD": (
        r"business development", r"\bbd\b", r"bizdev", r"partnership",
        r"outreach", r"affiliate", r"\bkol\b", r"influencer", r"ambassador",
        r"партн[её]р", r"аутрич", r"аффилиат", r"инфлюенсер", r"амбассадор",
    ),
    "Маркетинг": (
        r"marketing", r"growth", r"acquisition", r"retention", r"funnel",
        r"conversion", r"campaign", r"\bseo\b", r"traffic", r"brand",
        r"маркетинг", r"воронк", r"конверси", r"кампани", r"трафик",
    ),
    "Контент": (
        r"twitter", r"x\.com", r"tweet", r"thread", r"hook", r"impression",
        r"creator", r"viral", r"content", r"твиттер", r"твит", r"тред",
        r"пост", r"хук", r"охват", r"вирал", r"контент",
    ),
    "Инструменты": (
        r"tool", r"framework", r"coding", r"developer", r"github", r"product",
        r"startup", r"\bsaas\b", r"\bapi\b", r"automation", r"no-code",
        r"vibe.?cod", r"инструмент", r"разработ", r"продукт", r"стартап",
        r"автоматизац", r"вайбкод",
    ),
    "Безопасность": (
        r"security", r"exploit", r"hack", r"scam", r"phishing", r"audit",
        r"research", r"analysis", r"threat", r"безопасност", r"эксплойт",
        r"взлом", r"скам", r"фишинг", r"аудит", r"исследован", r"аналитик",
    ),
}

PRIMARY_ORDER = (
    "Роботы", "BD", "AI", "Финансы", "Безопасность",
    "Инструменты", "Маркетинг", "Web3", "Контент",
)

NICHE_DAILY_LIMITS = {
    "Роботы": 2,
    "BD": 3,
    "AI": 3,
    "Финансы": 3,
    "Безопасность": 2,
    "Инструменты": 2,
    "Маркетинг": 2,
    "Web3": 4,
    "Контент": 2,
}

PRIORITY_ALPHA = "alpha professor"
PRIORITY_X = ("x family", "twitter вынос-team")

ENGAGEMENT_NOISE = (
    r"\b(like|comment|repost|quote|bookmark|follow|support|raid)\b.{0,35}\b(me|please|pls|plz|mutual|back)\b",
    r"\bplease\s+(like|comment|repost|support|follow)",
    r"(посиди|задержись)\s+на\s+посте", r"копи\s*(линк|ссылк)",
    r"дай(те)?\s+(сап|пуш|лайк|комм|квот|репост)", r"всем\s+взаим",
    r"лайк\s+комм", r"поддержи(те)?\s+(пост|твит)", r"новый\s+пост.{0,20}(сап|поддерж)",
)

INSIGHT_SIGNALS = (
    r"algorithm", r"shadow.?ban", r"impressions?", r"retention", r"conversion",
    r"case study", r"analytics?", r"benchmark", r"experiment", r"why it works",
    r"алгоритм", r"тенев(ой|ого) бан", r"просмотр", r"подписчик", r"удержан",
    r"конверси", r"аналитик", r"метрик", r"кейс", r"эксперимент", r"залетел",
    r"залетели", r"результат", r"почему сработал", r"разбор",
)

ALPHA_SIGNALS = (
    r"announc", r"launch", r"release", r"testnet", r"mainnet", r"waitlist",
    r"airdrop", r"mint", r"tokenomics", r"funding", r"raise", r"\d+(?:[.,]\d+)?%",
    r"анонс", r"запуст", r"релиз", r"тестнет", r"мейннет", r"лист ожид",
    r"аирдроп", r"минт", r"токеномик", r"раунд", r"инвестиц", r"эмисси",
)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def classify_niches(text: str) -> tuple[str, ...]:
    normalized = _normalized(text)
    found = tuple(
        niche for niche, patterns in NICHE_PATTERNS.items()
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)
    )
    return found


def _primary(niches: tuple[str, ...]) -> str:
    return next((name for name in PRIMARY_ORDER if name in niches), "Другое")


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w$%]+", text, re.UNICODE))


def _content_without_links(text: str) -> str:
    """Strip URLs before judging whether a message contains useful context."""
    return " ".join(re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE).split())


def evaluate_knowledge_balance(
    *,
    text: str,
    chat_title: str,
    matcher_score: int,
    daily_total: int,
    daily_by_niche: dict[str, int],
    daily_limit: int,
) -> KnowledgeBalanceDecision:
    normalized = _normalized(text)
    niches = classify_niches(text)
    primary = _primary(niches)
    title = _normalized(chat_title)
    context = _content_without_links(normalized)
    word_count = _word_count(context)
    is_alpha = PRIORITY_ALPHA in title

    if not is_alpha and (len(context) < 45 or word_count < 7):
        return KnowledgeBalanceDecision(False, primary, niches, "too_short")
    if _matches_any(normalized, ENGAGEMENT_NOISE):
        return KnowledgeBalanceDecision(False, primary, niches, "engagement_noise")
    if not niches:
        return KnowledgeBalanceDecision(False, primary, niches, "no_relevant_niche")
    if re.fullmatch(r"(?:https?://\S+|@\w+|\s)+", normalized):
        return KnowledgeBalanceDecision(False, primary, niches, "link_only")

    is_priority_x = any(name in title for name in PRIORITY_X)
    insight = _matches_any(normalized, INSIGHT_SIGNALS)
    alpha_signal = _matches_any(normalized, ALPHA_SIGNALS)

    if is_alpha:
        relevant = matcher_score >= 1 or bool(niches) or alpha_signal or insight
        enough_context = len(context) >= 12 and word_count >= 2
    elif is_priority_x:
        relevant = matcher_score >= 2 or insight
        enough_context = len(context) >= 80 and word_count >= 12
    else:
        strong_niche = primary in {"Роботы", "BD", "AI", "Финансы", "Безопасность", "Инструменты"}
        relevant = matcher_score >= 3 or (strong_niche and matcher_score >= 2)
        enough_context = len(context) >= (120 if matcher_score >= 3 else 180) and word_count >= 15

    if not relevant:
        return KnowledgeBalanceDecision(False, primary, niches, "weak_relevance")
    if not enough_context:
        return KnowledgeBalanceDecision(False, primary, niches, "not_enough_context")
    if daily_total >= daily_limit:
        return KnowledgeBalanceDecision(False, primary, niches, "daily_limit")
    if daily_by_niche.get(primary, 0) >= NICHE_DAILY_LIMITS[primary]:
        return KnowledgeBalanceDecision(False, primary, niches, "niche_daily_limit")
    return KnowledgeBalanceDecision(True, primary, niches, f"niche:{primary}")
