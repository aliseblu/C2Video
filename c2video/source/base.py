"""Candidate source protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from c2video.source.models import CandidateItem


@runtime_checkable
class CandidateSource(Protocol):
    """Fetch candidate items for a set of domain keywords.

    Implementations are replaceable (ADR-0001 and ADR-0011): public feeds
    remain account-free; Zhihu supports official APIs and explicit file imports.
    """

    name: str

    def fetch(
        self,
        keywords: list[str],
        *,
        time_window_hours: int = 24,
        max_results: int = 50,
    ) -> list[CandidateItem]:
        """Return recent items matching *keywords* within the time window."""
        ...

