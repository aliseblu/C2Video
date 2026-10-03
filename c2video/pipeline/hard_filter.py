"""Hard Filter — rule-based engagement / time-window thresholds."""

from __future__ import annotations

from c2video.config.schema import HardFilterConfig
from c2video.source.models import CandidateItem
from c2video.util import content_age_hours


def passes_hard_filter(
    candidate: CandidateItem,
    config: HardFilterConfig,
) -> bool:
    if candidate.likes < config.min_likes:
        return False
    if candidate.shares < config.min_shares:
        return False
    if candidate.replies < config.min_replies:
        return False
    if config.views_threshold > 0 and candidate.views < config.views_threshold:
        return False
    if not is_fresh(candidate, config.max_age_hours):
        return False
    return True


def is_fresh(candidate: CandidateItem, max_age_hours: int) -> bool:
    if candidate.raw.get("acquisition") == "import":
        return True  # Explicitly selected materials need not be newly published.
    if max_age_hours <= 0:
        return True
    age = content_age_hours(candidate.created_at)
    if age is None:
        return True
    return age <= max_age_hours


def apply_hard_filter(
    candidates: list[CandidateItem],
    config: HardFilterConfig,
) -> list[CandidateItem]:
    return [c for c in candidates if passes_hard_filter(c, config)]

