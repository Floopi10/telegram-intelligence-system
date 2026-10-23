from __future__ import annotations

import re
from collections.abc import Iterable


NICHE_ORDER = (
    "AI",
    "Финансы",
    "Роботы",
    "Девушки и lifestyle",
    "Web3",
    "Технологии",
    "Маркетинг и контент",
    "Игры",
)

NICHE_PATTERNS: dict[str, tuple[str, ...]] = {
    "AI": (
        r"\bai\b", r"artificial intelligence", r"\bllm\b", r"claude",
        r"chatgpt", r"openai", r"gemini", r"deepseek", r"qwen",
        r"grok", r"opus", r"neural", r"machine learning", r"prompt",
        r"\bagents?\b", r"\bai model", r"model routing", r"нейросет", r"\bии\b",
    ),
    "Финансы": (
        r"financ", r"financial market", r"trading", r"trader", r"invest",
        r"stock", r"equity", r"credit suisse", r"banking", r"hedge fund",
        r"macro", r"portfolio", r"econom", r"polymarket", r"profit",
        r"финанс", r"рынок", r"трейд", r"инвест", r"банк", r"эконом",
    ),
    "Роботы": (
        r"robot", r"robotics", r"humanoid", r"embodied", r"drone",
        r"optimus", r"automation hardware", r"робот", r"гуманоид", r"дрон",
    ),
    "Девушки и lifestyle": (
        r"\bgirl", r"\bwomen?\b", r"female", r"beauty", r"fashion",
        r"makeup", r"skincare", r"outfit", r"fashion model", r"lifestyle",
        r"девуш", r"женщин", r"мода", r"красот", r"макияж", r"уход",
    ),
    "Web3": (
        r"web3", r"crypto", r"blockchain", r"onchain", r"token", r"defi",
        r"airdrop", r"wallet", r"protocol", r"dao", r"крипт", r"блокчейн",
        r"токен", r"ончейн", r"аирдроп",
    ),
    "Технологии": (
        r"software", r"developer", r"coding", r"github", r"open.source",
        r"startup", r"saas", r"\bapi\b", r"hardware", r"tech", r"zero.day",
        r"vulnerabil", r"remote code execution", r"windows process", r"railway",
        r"infrastructure", r"terminal", r"sandbox", r"registry cred",
        r"разработ", r"код", r"приложен", r"стартап", r"технолог",
    ),
    "Маркетинг и контент": (
        r"marketing", r"creator", r"content", r"viral", r"hook", r"growth",
        r"audience", r"brand", r"conversion", r"retention", r"storytelling",
        r"маркетинг", r"контент", r"вирал", r"хук", r"аудитори", r"бренд",
    ),
    "Игры": (
        r"gaming", r"game dev", r"game studio", r"gameplay", r"steam",
        r"unity", r"unreal engine", r"игр", r"геймдев",
    ),
}

SPORT_PATTERNS = (
    r"football", r"soccer", r"basketball", r"tennis", r"baseball",
    r"hockey", r"champions league", r"premier league", r"bundesliga",
    r"\bxg\b", r"corners? per match", r"shots? per match", r"bayern",
    r"real madrid", r"barcelona", r"manchester", r"nba", r"nfl", r"nhl",
    r"футбол", r"баскетбол", r"теннис", r"хоккей", r"матч", r"голов?",
)

RADAR_NOISE_PATTERNS = (
    r"\b(?:betting|sportsbook|bet slip|odds|parlay)\b",
    r"\b(?:hiring|job opening|open position|apply now|vacancy)\b",
    r"\b(?:ambassador program|giveaway|airdrop campaign|referral code)\b",
    r"\b(?:dm me|join our channel|limited spots|guaranteed profit)\b",
)


def _combined(values: Iterable[str]) -> str:
    return " ".join(str(value or "") for value in values).casefold()


def is_sport(*values: str) -> bool:
    text = _combined(values)
    return any(re.search(pattern, text, re.I) for pattern in SPORT_PATTERNS)


def is_twitter_radar_noise(*values: str) -> bool:
    text = _combined(values)
    return is_sport(text) or any(
        re.search(pattern, text, re.I) for pattern in RADAR_NOISE_PATTERNS
    )


def classify_twitter_niche(*values: str) -> str | None:
    text = _combined(values)
    if is_sport(text):
        return None
    scores = {
        niche: sum(bool(re.search(pattern, text, re.I)) for pattern in patterns)
        for niche, patterns in NICHE_PATTERNS.items()
    }
    best = max(NICHE_ORDER, key=lambda niche: scores[niche])
    return best if scores[best] else None
