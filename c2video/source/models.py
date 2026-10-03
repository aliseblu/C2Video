"""Shared source-neutral candidate model."""
from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field


class CandidateItem(BaseModel):
    """A candidate question, answer, article, or public-feed item."""
    id: str
    text: str
    created_at: str = ""
    author_id: str = ""
    author_name: str = ""
    author_username: str = ""
    author_avatar_url: str = ""
    author_verified: bool = False
    likes: int = 0
    # Read historical artifacts without rewriting their provenance.
    shares: int = Field(0, validation_alias=AliasChoices("shares", "retweets"))
    replies: int = 0
    favorites: int = 0
    views: int = 0
    url: str = ""
    lang: str = ""
    media_urls: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    def engagement_score(self) -> int:
        return self.likes + self.shares * 2 + self.replies + self.favorites

