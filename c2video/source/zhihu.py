"""Zhihu official API and explicit local-material import (no browser scraping)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from c2video.source.models import CandidateItem
from c2video.source.public import _coerce_int, _is_recent, _matches_keywords, _plain_text

API_ROOT = "https://developer.zhihu.com/api/v1/content"
MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ITEMS = 100


def _content_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    valid_path = (
        parsed.hostname in {"www.zhihu.com", "zhihu.com"}
        and re.fullmatch(r"/(?:question/\d+(?:/answer/\d+)?|answer/\d+)/?", parsed.path)
    ) or (
        parsed.hostname == "zhuanlan.zhihu.com"
        and re.fullmatch(r"/p/\d+/?", parsed.path)
    )
    if parsed.scheme != "https" or parsed.netloc != parsed.hostname or not valid_path:
        raise ValueError("素材链接必须是知乎问题、回答或专栏文章的 HTTPS 原文链接。")
    return url


def _image_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return url if parsed.scheme == "https" and host.endswith(".zhimg.com") else ""


def _date(value: Any) -> str:
    if not value:
        return ""  # Do not invent publication dates for imported content or hot-list items.
    try:
        dt = (datetime.fromtimestamp(float(value), tz=timezone.utc)
              if isinstance(value, (int, float))
              else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError("素材时间必须是 ISO 日期时间或秒级时间戳。") from exc


def normalize_item(item: dict[str, Any], *, acquisition: str) -> CandidateItem:
    url = _content_url(item.get("Url") or item.get("url"))
    title = _plain_text(str(item.get("Title") or item.get("title") or ""))
    body = _plain_text(str(item.get("ContentText") or item.get("Summary")
                           or item.get("content") or item.get("text") or ""))
    if not title:
        raise ValueError("每条知乎素材必须包含 title（标题）。")
    if len(title) > 500 or len(body) > 20_000:
        raise ValueError("素材过长：标题最多 500 字，正文或摘要最多 20000 字。")
    parsed = urlsplit(url)
    canonical = urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))
    source_type = "zhihu_article" if parsed.hostname == "zhuanlan.zhihu.com" else (
        "zhihu_answer" if "/answer/" in parsed.path else "zhihu_question"
    )
    avatar = _image_url(item.get("AuthorAvatar") or item.get("author_avatar_url"))
    cover = _image_url(item.get("ThumbnailUrl") or item.get("cover_url"))
    return CandidateItem(
        id="zhihu-" + hashlib.sha256(canonical.encode()).hexdigest()[:24],
        text=title + ("\n" + body if body else ""),
        created_at=_date(item.get("EditTime") or item.get("created_at")),
        author_name=_plain_text(str(item.get("AuthorName") or item.get("author") or "")),
        author_avatar_url=avatar,
        author_verified=bool(item.get("AuthorBadgeText")),
        likes=_coerce_int(item.get("VoteUpCount", item.get("voteup_count"))),
        replies=_coerce_int(item.get("CommentCount", item.get("comment_count"))),
        favorites=_coerce_int(item.get("favorite_count")),
        url=url,
        lang="zh-CN",
        media_urls=[cover] if cover else [],
        raw={
            "source": "zhihu", "source_type": source_type, "title": title,
            "summary": body, "acquisition": acquisition,
            "available_metrics": [name for name, keys in {
                "likes": ("VoteUpCount", "voteup_count"),
                "replies": ("CommentCount", "comment_count"),
                "favorites": ("favorite_count",),
            }.items() if any(key in item for key in keys)],
            "content_scope": "provided_text" if acquisition == "import" else "api_summary",
        },
    )


def parse_materials(payload: Any) -> list[CandidateItem]:
    """Validate user-provided JSON; reject invalid entries, never silently drop them."""
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_IMPORT_BYTES:
        raise ValueError("素材文件不能超过 1 MB。")
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_IMPORT_ITEMS:
        raise ValueError("素材 JSON 应是包含 1–100 条内容的数组，或包含 items 数组的对象。")
    unique: dict[str, CandidateItem] = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条素材必须是 JSON 对象。")
        try:
            candidate = normalize_item(item, acquisition="import")
        except ValueError as exc:
            raise ValueError(f"第 {index} 条素材：{exc}") from exc
        unique.setdefault(candidate.id, candidate)
    return list(unique.values())


def read_materials(path: str | Path) -> Any:
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValueError("尚未导入知乎素材。请在新建页面选择 JSON 文件，或执行 c2video zhihu import。")
    if file.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("素材文件不能超过 1 MB。")
    try:
        payload = json.loads(file.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("素材文件必须是 UTF-8 编码的有效 JSON。") from exc
    parse_materials(payload)
    return payload


class ZhihuSource:
    name = "zhihu"

    def __init__(self, *, mode: str = "api", access_secret: str = "",
                 method: str = "search", import_file: str = "work/zhihu-materials.json",
                 timeout: float = 30, http_client: httpx.Client | None = None) -> None:
        self.mode, self.method = mode, method
        self.access_secret = access_secret.strip()
        self.import_file, self.timeout = import_file, timeout
        self._http_client = http_client

    def fetch(self, keywords: list[str], *, time_window_hours: int = 24,
              max_results: int = 50) -> list[CandidateItem]:
        if max_results <= 0:
            return []
        if self.mode == "import":
            return parse_materials(read_materials(self.import_file))[:max_results]
        if self.mode != "api" or self.method not in {"search", "hot_list"}:
            raise ValueError("知乎模式应为 api/import；API 方法应为 search/hot_list。")
        if not self.access_secret:
            raise ValueError("缺少知乎 Access Secret。请设置 C2VIDEO_SOURCE_ZHIHU_ACCESS_SECRET，"
                             "或在新建页面选择知乎素材 JSON 文件。")
        queries = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))[:6]
        if self.method == "search" and not queries:
            raise ValueError("知乎搜索需要至少一个关键词。")
        client = self._http_client or httpx.Client(timeout=self.timeout, follow_redirects=False)
        results: dict[str, CandidateItem] = {}
        try:
            for query in queries if self.method == "search" else [""]:
                remaining = max_results - len(results)
                params = ({"Query": query, "Count": min(remaining, 10)}
                          if self.method == "search" else {"Limit": 30})
                endpoint = "zhihu_search" if self.method == "search" else "hot_list"
                try:
                    response = client.get(
                        f"{API_ROOT}/{endpoint}", params=params,
                        headers={"Authorization": f"Bearer {self.access_secret}",
                                 "X-Request-Timestamp": str(int(time.time())),
                                 "Content-Type": "application/json"},
                        follow_redirects=False,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(f"知乎接口 HTTP {exc.response.status_code}；"
                                       "请检查凭证、权限、额度和网络。不会自动转用其他来源。") from None
                except httpx.RequestError:
                    raise RuntimeError("知乎接口网络请求失败，请检查网络后重试。") from None
                try:
                    data = response.json()
                except ValueError:
                    raise RuntimeError("知乎接口未返回有效 JSON。") from None
                if not isinstance(data, dict) or data.get("Code") != 0:
                    code = data.get("Code") if isinstance(data, dict) else None
                    errors = {20001: "凭证无效", 30001: "调用过于频繁", 30002: "额度不足"}
                    raise RuntimeError("知乎接口失败：" + errors.get(code, "返回非成功状态"))
                body = data.get("Data")
                items = body.get("Items") if isinstance(body, dict) else None
                if not isinstance(items, list):
                    raise RuntimeError("知乎接口响应缺少 Data.Items，无法解析。")
                for item in items:
                    if not isinstance(item, dict):
                        raise RuntimeError("知乎接口返回了无效条目。")
                    candidate = normalize_item(item, acquisition="api")
                    if not _is_recent(candidate.created_at, time_window_hours):
                        continue
                    if self.method == "hot_list" and not _matches_keywords(candidate.text, keywords):
                        continue
                    results.setdefault(candidate.id, candidate)
                if len(results) >= max_results:
                    break
        finally:
            if self._http_client is None:
                client.close()
        return list(results.values())[:max_results]
