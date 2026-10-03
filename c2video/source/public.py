"""Free public candidate sources: RSS, Hacker News, and GitHub repositories."""

from __future__ import annotations

import hashlib
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from c2video.source.models import CandidateItem

DEFAULT_RSS_FEEDS = (
    "https://openai.com/news/rss.xml",
    "https://blog.google/technology/ai/rss/",
)
DEFAULT_HACKER_NEWS_API_BASE = "https://hacker-news.firebaseio.com/v0"
DEFAULT_GITHUB_API_BASE = "https://api.github.com"


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _utc_iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _is_recent(value: str, hours: int) -> bool:
    if not value or hours <= 0:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= datetime.now(timezone.utc) - timedelta(hours=hours)


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    folded = text.casefold()
    for raw in keywords:
        term = raw.strip().casefold()
        if not term:
            continue
        if term.isascii() and len(term) <= 3:
            if re.search(rf"\b{re.escape(term)}\b", folded):
                return True
        elif term in folded:
            return True
    return False


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1] in wanted and child.text:
            return child.text.strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1] != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        if href and child.attrib.get("rel", "alternate") in {"alternate", ""}:
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


class PublicFeedSource:
    """Aggregate recent public items without requiring an account or API key."""

    name = "public"

    def __init__(
        self,
        *,
        enabled_sources: list[str] | None = None,
        rss_feeds: list[str] | None = None,
        hacker_news_api_base: str = DEFAULT_HACKER_NEWS_API_BASE,
        github_api_base: str = DEFAULT_GITHUB_API_BASE,
        http_client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.enabled_sources = [
            value.strip().lower()
            for value in (enabled_sources or ["rss", "hacker_news", "github"])
            if value.strip()
        ]
        self.rss_feeds = list(rss_feeds or DEFAULT_RSS_FEEDS)
        self.hacker_news_api_base = hacker_news_api_base.rstrip("/")
        self.github_api_base = github_api_base.rstrip("/")
        self._http_client = http_client
        self.timeout = timeout
        self.last_errors: dict[str, str] = {}

    def fetch(
        self,
        keywords: list[str],
        *,
        time_window_hours: int = 24,
        max_results: int = 50,
    ) -> list[CandidateItem]:
        if max_results <= 0:
            return []
        enabled = list(dict.fromkeys(self.enabled_sources))
        if not enabled:
            raise ValueError("public source needs at least one enabled source")

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "C2Video/0.2 (+local public-feed reader)"},
        )
        per_source = max(
            6,
            min(20, (max_results + len(enabled) - 1) // len(enabled)),
        )
        collected: list[CandidateItem] = []
        self.last_errors = {}
        try:
            for source_name in enabled:
                try:
                    if source_name in {"hacker_news", "hackernews", "hn"}:
                        items = self._fetch_hacker_news(
                            client, keywords, time_window_hours, per_source
                        )
                    elif source_name in {"github", "github_trending"}:
                        items = self._fetch_github(
                            client, keywords, time_window_hours, per_source
                        )
                    elif source_name == "rss":
                        items = self._fetch_rss(
                            client, keywords, time_window_hours, per_source
                        )
                    else:
                        self.last_errors[source_name] = "unknown public source"
                        continue
                    collected.extend(items)
                except Exception as exc:  # noqa: BLE001 - one feed must not stop the others
                    self.last_errors[source_name] = str(exc)
        finally:
            if owns_client:
                client.close()

        unique: dict[str, CandidateItem] = {}
        for candidate in collected:
            key = candidate.url.strip() or candidate.id
            current = unique.get(key)
            if current is None or candidate.engagement_score() > current.engagement_score():
                unique[key] = candidate
        results = list(unique.values())
        results.sort(key=lambda item: item.engagement_score(), reverse=True)
        return results[:max_results]

    def _fetch_hacker_news(
        self,
        client: httpx.Client,
        keywords: list[str],
        hours: int,
        limit: int,
    ) -> list[CandidateItem]:
        response = client.get(f"{self.hacker_news_api_base}/topstories.json")
        response.raise_for_status()
        story_ids = response.json()
        if not isinstance(story_ids, list):
            return []

        results: list[CandidateItem] = []
        scan_limit = min(len(story_ids), max(limit * 3, 20), 80)
        for story_id in story_ids[:scan_limit]:
            item_response = client.get(
                f"{self.hacker_news_api_base}/item/{story_id}.json"
            )
            item_response.raise_for_status()
            item = item_response.json()
            if not isinstance(item, dict) or item.get("type") != "story":
                continue
            if item.get("deleted") or item.get("dead"):
                continue
            title = _plain_text(str(item.get("title") or ""))
            body = _plain_text(str(item.get("text") or ""))
            if re.fullmatch(r"https?://\S+", body):
                body = ""
            if not title or not _matches_keywords(f"{title} {body}", keywords):
                continue
            created_at = _utc_iso(item.get("time"))
            if not _is_recent(created_at, hours):
                continue
            item_id = str(item.get("id") or story_id)
            results.append(
                CandidateItem(
                    id=f"hn-{item_id}",
                    text=title + (f" — {body[:500]}" if body else ""),
                    created_at=created_at,
                    author_id=str(item.get("by") or ""),
                    author_name="Hacker News",
                    author_username=str(item.get("by") or "hn"),
                    likes=_coerce_int(item.get("score")),
                    replies=_coerce_int(item.get("descendants")),
                    views=(
                        _coerce_int(item.get("score"))
                        + _coerce_int(item.get("descendants"))
                    ),
                    url=str(
                        item.get("url")
                        or f"https://news.ycombinator.com/item?id={item_id}"
                    ),
                    lang="en",
                    raw={
                        **item,
                        "source": "hacker_news",
                        "source_type": "community_story",
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    def _fetch_github(
        self,
        client: httpx.Client,
        keywords: list[str],
        hours: int,
        limit: int,
    ) -> list[CandidateItem]:
        term = next(
            (value.strip() for value in keywords if re.search(r"[A-Za-z]", value)),
            "artificial intelligence",
        )
        term = term if len(term) > 2 else "AI"
        since = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 24))).date()
        query_term = f'"{term}"' if " " in term else term
        query = f"{query_term} in:name,description,readme pushed:>={since.isoformat()}"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = client.get(
            f"{self.github_api_base}/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": min(limit, 100),
            },
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else []
        results: list[CandidateItem] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name") or item.get("name") or "").strip()
            description = _plain_text(str(item.get("description") or ""))
            if not full_name:
                continue
            topics = item.get("topics") if isinstance(item.get("topics"), list) else []
            relevance_text = " ".join(
                [full_name, description, *[str(topic) for topic in topics]]
            )
            if not _matches_keywords(relevance_text, keywords):
                continue
            owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
            fallback_id = hashlib.sha256(full_name.encode()).hexdigest()[:16]
            results.append(
                CandidateItem(
                    id=f"github-{item.get('id') or fallback_id}",
                    text=f"{full_name}: {description}".rstrip(": "),
                    created_at=_utc_iso(item.get("pushed_at") or item.get("updated_at")),
                    author_id=str(owner.get("id") or ""),
                    author_name=str(owner.get("login") or full_name.split("/", 1)[0]),
                    author_username=full_name,
                    author_avatar_url=str(owner.get("avatar_url") or ""),
                    likes=_coerce_int(item.get("stargazers_count")),
                    shares=_coerce_int(item.get("forks_count")),
                    replies=_coerce_int(item.get("open_issues_count")),
                    views=_coerce_int(item.get("watchers_count")),
                    url=str(item.get("html_url") or ""),
                    lang="en",
                    raw={
                        "source": "github",
                        "source_type": "repository",
                        "github_id": item.get("id"),
                        "full_name": full_name,
                        "description": description,
                        "language": item.get("language"),
                        "topics": topics,
                        "stargazers_count": _coerce_int(item.get("stargazers_count")),
                        "forks_count": _coerce_int(item.get("forks_count")),
                        "open_issues_count": _coerce_int(item.get("open_issues_count")),
                    },
                )
            )
        return results[:limit]

    def _fetch_rss(
        self,
        client: httpx.Client,
        keywords: list[str],
        hours: int,
        limit: int,
    ) -> list[CandidateItem]:
        results: list[CandidateItem] = []
        for feed_url in self.rss_feeds:
            try:
                response = client.get(feed_url)
                response.raise_for_status()
                root = ET.fromstring(response.content)
            except Exception as exc:  # noqa: BLE001 - continue with the next feed
                self.last_errors[f"rss:{feed_url}"] = str(exc)
                continue
            channel = next(
                (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "channel"),
                root,
            )
            feed_title = _child_text(channel, "title") or "RSS"
            entries = [
                node
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}
            ]
            for entry in entries:
                title = _plain_text(_child_text(entry, "title"))
                summary = _plain_text(
                    _child_text(entry, "description", "summary", "content")
                )
                if not title:
                    continue
                created_at = _utc_iso(
                    _child_text(entry, "pubDate", "published", "updated", "date")
                )
                if not _is_recent(created_at, hours):
                    continue
                # These defaults are curated AI feeds. Keyword matches rank higher,
                # but entries remain useful when a title omits the literal term "AI".
                matched = _matches_keywords(f"{title} {summary}", keywords)
                link = _entry_link(entry)
                raw_id = _child_text(entry, "guid", "id") or link or title
                digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
                author = _child_text(entry, "author", "creator") or feed_title
                results.append(
                    CandidateItem(
                        id=f"rss-{digest}",
                        text=title + (f" — {summary[:500]}" if summary else ""),
                        created_at=created_at,
                        author_id=feed_title,
                        author_name=feed_title,
                        author_username=feed_title,
                        likes=1 if matched else 0,
                        url=link,
                        lang="en",
                        raw={
                            "source": "rss",
                            "source_type": "rss_entry",
                            "feed_title": feed_title,
                            "feed_url": feed_url,
                            "title": title,
                            "summary": summary,
                            "author": author,
                            "keyword_match": matched,
                        },
                    )
                )
                if len(results) >= limit:
                    return results
        return results

