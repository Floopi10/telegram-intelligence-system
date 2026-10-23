from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VacancyDecision:
    accepted: bool
    reason: str


VACANCY_PATTERNS = (
    r"\b(?:we(?:'re| are)?|now)\s+hiring\b", r"\bhiring\s*:",
    r"\b(?:is|are)\s+hiring\b",
    r"\bjob\s+(?:opening|opportunity)\b", r"\bopen\s+(?:role|position)\b",
    r"\bvacanc(?:y|ies)\b", r"\bposition\s*:",
    r"\bваканси", r"\bищем\s+(?:сотрудника|специалиста|менеджера|человека)\b",
    r"\bоткрыта\s+позиция\b", r"\bработа\s+в\s+(?:крипте|web3)\b",
)

ROLE_PATTERNS = (
    r"\bbusiness development\b", r"\bbiz\s*dev\b",
    r"\bbd\s+(?:manager|lead|specialist|associate|representative)\b",
    r"\bpartnerships?\s+(?:manager|lead|specialist|associate)\b",
    r"\boutreach\s+(?:manager|lead|specialist|associate)\b",
    r"\b(?:kol|influencer|affiliate|ambassador)\s+(?:manager|lead|specialist)\b",
    r"\bcommunity\s+(?:manager|lead|moderator|partnerships?|growth)\b",
    r"\bgrowth\s+(?:manager|marketer|marketing|lead)\b",
    r"\bweb3\s+(?:growth|marketing)\s+(?:manager|lead|specialist)\b",
    r"\bcrypto\s+(?:growth|marketing)\s+(?:manager|lead|specialist)\b",
    r"\bменеджер\w*\s+по\s+(?:развитию бизнеса|партнерствам|партнёрствам|работе с kol|инфлюенсерами|аффилиатами)\b",
    r"\b(?:бизнес[- ]девелопмент|бизнес[- ]девелопер|бд[- ]менеджер|аутрич|партнерств|партнёрств)\b",
    r"\b(?:комьюнити|community)[- ](?:менеджер|модератор)\b",
)

EXCLUDED_ROLE_PATTERNS = (
    r"\b(?:vp|vice president|director|head)\s+of\s+(?:public sector\s+)?sales\b",
    r"\bpublic[- ]sector\s+sales\b", r"\bgovernment\s+sales\b",
    r"\bfederal\s+sales\b", r"\bpublic[- ]sector\s+procurement\b",
    r"\b(?:social\s+)?media\s+(?:manager|associate)\b", r"\bмедиа[- ]менеджер\b",
    r"\bsmm\b", r"\bcontent\s+(?:writer|manager)\b", r"\bcopywriter\b",
    r"\bкопирайтер\b", r"\b(?:research|data|financial)\s+analyst\b",
    r"\bresearcher\b", r"\bаналитик\b",
    r"\b(?:graphic|creative|ui|ux)\s+designer\b", r"\bдизайнер\b",
    r"\b(?:product|media)\s+manager\b",
    r"\b(?:backend|frontend|fullstack|software)\s+(?:developer|engineer)\b",
    r"\baccount executive\b", r"\bтрейдер\b",
)

PAID_PATTERNS = (
    r"\bpaid\b", r"\bsalary\b", r"\bcompensation\b", r"\bbudget\b",
    r"\bretainer\b", r"\bhourly\s+rate\b", r"\bmonthly\s+rate\b",
    r"[$€£₽]\s?\d", r"\d\s?(?:usd|eur|usdt|rub|aed)\b",
    r"\bоплат", r"\bзарплат", r"\bоклад", r"\bбюджет", r"\bставк", r"\bгонорар",
)
UNPAID_PATTERNS = (
    r"\bunpaid\b", r"\bvolunteer\b", r"\bбез\s+оплаты\b", r"\bнеоплачиваем",
    r"\bcommission[- ]only\b", r"\bonly\s+commission\b", r"\bтолько\s+процент",
    r"\bno\s+(?:salary|retainer|base\s+pay)\b",
)
GLOBAL_PATTERNS = (
    r"\bremote\s+(?:global|worldwide|anywhere)\b", r"\bglobal\s+remote\b",
    r"\bworldwide\b", r"\bwork\s+from\s+anywhere\b",
    r"\banywhere\s+in\s+the\s+world\b", r"\bremote\s+without\s+location\s+restrictions\b",
    r"\bудаленн\w*\s+(?:по\s+всему\s+миру|без\s+ограничений)\b",
)
REMOTE_PATTERNS = (r"\bremote\b", r"\bremotely\b", r"\bудален", r"\bдистанцион")
ALLOWED_REGION_PATTERNS = (
    r"\brussia\b", r"\brussian federation\b", r"\bcis\b", r"\bснг\b", r"\bросси",
    r"\bказахстан", r"\bbelarus\b", r"\bбеларус", r"\barmenia\b", r"\bармен",
)
BLOCKED_REGION_PATTERNS = (
    r"\busa\b", r"\bu\.s\.\b", r"\bunited states\b", r"\bus[- ]only\b", r"\bсша\b",
    r"\blatam\b", r"\blatin america\b", r"\bлатам\b", r"\blатинск",
    r"\bmexico\b", r"\bcolombia\b", r"\bbrazil\b", r"\bargentina\b", r"\bchile\b",
    r"\bperu\b", r"\bvenezuela\b", r"\becuador\b", r"\bpanama\b", r"\bparaguay\b",
    r"\buruguay\b", r"\bbolivia\b", r"\bguatemala\b", r"\bhonduras\b",
    r"\bcosta rica\b", r"\bnicaragua\b", r"\bel salvador\b", r"\bpuerto rico\b",
    r"\bdominican republic\b", r"\bcuba\b", r"\bghana\b", r"\bafrica\b",
    r"\bdubai\b", r"\buae\b", r"\buk\b", r"\bunited kingdom\b",
    r"\beurope[- ]only\b", r"\beu[- ]only\b", r"\bcanada\b", r"\bapac\b", r"\bindia\b",
    r"\bfrance\b", r"\bmorocco\b", r"\bsoutheast asia\b", r"\bsouth[- ]east asia\b",
    r"\bsea\s+focus\b", r"\bindonesia\b", r"\bmalaysia\b", r"\bphilippines\b",
    r"\bthailand\b", r"\bvietnam\b", r"\bsingapore\b",
)


def _has(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_vacancy_text(text: str) -> bool:
    return _has(VACANCY_PATTERNS, " ".join(text.casefold().split()))


def evaluate_vacancy(text: str) -> VacancyDecision:
    normalized = " ".join(text.casefold().split())
    if _has(EXCLUDED_ROLE_PATTERNS, normalized):
        return VacancyDecision(False, "excluded_role")
    if not _has(ROLE_PATTERNS, normalized):
        return VacancyDecision(False, "irrelevant_role")
    if _has(UNPAID_PATTERNS, normalized):
        return VacancyDecision(False, "unpaid_or_commission_only")
    if not _has(PAID_PATTERNS, normalized):
        return VacancyDecision(False, "payment_not_confirmed")
    is_global = _has(GLOBAL_PATTERNS, normalized)
    if _has(BLOCKED_REGION_PATTERNS, normalized) and not is_global:
        return VacancyDecision(False, "blocked_region")
    if not (is_global or _has(REMOTE_PATTERNS, normalized) or _has(ALLOWED_REGION_PATTERNS, normalized)):
        return VacancyDecision(False, "location_not_remote_or_ru_cis")
    return VacancyDecision(True, "accepted")
