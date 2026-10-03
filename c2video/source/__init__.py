"""Pluggable Zhihu and public-feed candidate sources."""

from c2video.source.base import CandidateSource
from c2video.source.factory import create_source
from c2video.source.models import CandidateItem

__all__ = [
    "CandidateSource",
    "CandidateItem",
    "create_source",
]

