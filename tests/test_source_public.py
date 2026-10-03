"""Public RSS, Hacker News, and GitHub source behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from c2video.source.public import PublicFeedSource


def test_public_source_aggregates_and_maps_metrics() -> None:
    now = datetime.now(timezone.utc)
    rss_xml = f"""<?xml version="1.0"?>
    <rss><channel><title>AI Lab</title><item>
      <title>New AI model released</title>
      <link>https://example.test/news</link>
      <pubDate>{format_datetime(now)}</pubDate>
      <description>A useful model update.</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "feeds.example.test":
            return httpx.Response(200, text=rss_xml)
        if request.url.path.endswith("/topstories.json"):
            return httpx.Response(200, json=[101])
        if request.url.path.endswith("/item/101.json"):
            return httpx.Response(
                200,
                json={
                    "id": 101,
                    "type": "story",
                    "by": "alice",
                    "time": int(now.timestamp()),
                    "title": "AI agents gain a new benchmark",
                    "url": "https://example.test/hn",
                    "score": 120,
                    "descendants": 34,
                },
            )
        if request.url.path.endswith("/search/repositories"):
            assert request.url.params["sort"] == "stars"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": 202,
                            "full_name": "acme/ai-tool",
                            "description": "Open source AI workflow tool",
                            "pushed_at": now.isoformat(),
                            "html_url": "https://github.com/acme/ai-tool",
                            "stargazers_count": 2400,
                            "forks_count": 180,
                            "open_issues_count": 12,
                            "watchers_count": 2400,
                            "owner": {"id": 8, "login": "acme", "avatar_url": ""},
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PublicFeedSource(
            rss_feeds=["https://feeds.example.test/rss"],
            http_client=client,
        )
        results = source.fetch(["AI"], time_window_hours=24, max_results=9)

    assert {item.raw["source"] for item in results} == {
        "rss",
        "hacker_news",
        "github",
    }
    github = next(item for item in results if item.raw["source"] == "github")
    assert github.likes == 2400
    assert github.shares == 180
    hacker_news = next(item for item in results if item.raw["source"] == "hacker_news")
    assert hacker_news.likes == 120
    assert hacker_news.replies == 34


def test_public_source_keeps_working_when_one_feed_fails() -> None:
    now = datetime.now(timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "broken.example.test":
            return httpx.Response(503, text="unavailable")
        if request.url.path.endswith("/topstories.json"):
            return httpx.Response(200, json=[9])
        return httpx.Response(
            200,
            json={
                "id": 9,
                "type": "story",
                "by": "bob",
                "time": int(now.timestamp()),
                "title": "AI release discussion",
                "score": 20,
                "descendants": 4,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PublicFeedSource(
            enabled_sources=["rss", "hacker_news"],
            rss_feeds=["https://broken.example.test/rss"],
            http_client=client,
        )
        results = source.fetch(["AI"], max_results=5)

    assert len(results) == 1
    assert results[0].raw["source"] == "hacker_news"
    assert any(key.startswith("rss:") for key in source.last_errors)

