from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


MONEY_PATTERN = re.compile(
    r"(?i)(?:[$€£]\s?\d[\d ,.]{0,12}|\d[\d ,.]{0,12}\s?(?:usdt|usdc|usd|eur|руб(?:лей|ля)?|₽))"
)


@dataclass(frozen=True)
class MatchResult:
    score: int
    matched: tuple[str, ...]
    compensation: tuple[str, ...]


class OfferMatcher:
    def __init__(self, rules_path: Path):
        with rules_path.open("r", encoding="utf-8") as file:
            raw_rules = json.load(file)
        self.rules: list[tuple[int, str, re.Pattern[str]]] = []
        for section in ("positive", "negative"):
            for weight_raw, phrases in raw_rules.get(section, {}).items():
                weight = int(weight_raw)
                for phrase in phrases:
                    normalized = phrase.casefold().strip()
                    pattern = re.compile(rf"(?<!\w){re.escape(normalized)}(?!\w)")
                    self.rules.append((weight, normalized, pattern))

    def evaluate(self, text: str) -> MatchResult:
        normalized = " ".join(text.casefold().split())
        weights: list[int] = []
        matched: list[str] = []
        for weight, phrase, pattern in self.rules:
            if pattern.search(normalized):
                weights.append(weight)
                matched.append(f"{phrase} ({weight:+d})")
        # Generic context words (web3, Telegram, Discord, etc.) may support an
        # offer, but a long platform list must not become an offer by itself.
        score = sum(weight for weight in weights if weight != 1)
        score += min(sum(1 for weight in weights if weight == 1), 2)
        compensation = tuple(dict.fromkeys(match.group(0).strip() for match in MONEY_PATTERN.finditer(text)))
        return MatchResult(score, tuple(matched), compensation)
