"""Content Card rendering: HTML template → 1080×1920 PNG via headless Chromium."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

from c2video.config.schema import C2VideoConfig
from c2video.pipeline.io import load_picks
from c2video.pipeline.models import Pick
from c2video.pipeline.workdir import resolve_run_dir
from c2video.util import (
    format_content_time,
    format_count,
    format_md_date,
    is_same_day_digest,
    punchline,
)

_TEMPLATE_PATH = Path(__file__).with_name("card_template.html")
_OPENER_PATH = Path(__file__).with_name("opener_template.html")
_COVER_PATH = Path(__file__).with_name("cover_template.html")
CARD_WIDTH = 1080
CARD_HEIGHT = 1920
COVER_WIDTH = 1080
COVER_HEIGHT = 1440
MAX_CONTENT_CHARS = 200
MAX_TRANSLATION_CHARS = 120


def is_image_url(url: str) -> bool:
    u = url.lower().split("?")[0]
    if u.endswith((".mp4", ".m3u8", ".webm", ".mov", ".gif")):
        return False
    return bool(url.strip())


def _initials(name: str) -> str:
    text = (name or "?").strip() or "?"
    return html.escape(text[:1].upper())


def clip_display_text(text: str, limit: int) -> str:
    """Keep the on-card body short so translation and subtitles stay on screen."""
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    cut = raw[:limit]
    for sep in ("\n", "。", ".", " ", "，"):
        idx = cut.rfind(sep)
        if idx > limit * 0.45:
            cut = cut[: idx + (1 if sep in "。." else 0)]
            break
    return cut.rstrip() + "…"


def background_style(pick: Pick) -> str:
    images = [u for u in pick.media_urls if is_image_url(u)]
    src = images[0] if images else (pick.author_avatar_url or "").strip()
    if src:
        return f"background-image:url('{html.escape(src, quote=True)}')"
    return (
        "background:radial-gradient(circle at 30% 20%, #3a3228, transparent 42%),"
        "radial-gradient(circle at 80% 70%, #1c2430, #0b0d10 70%)"
    )


def translation_caption(pick: Pick) -> str:
    source = str(pick.raw.get("source") or "").strip().lower()
    if source == "zhihu":
        return "知乎内容摘要 · 请以原文为准"
    if source in {"github", "hacker_news", "rss"}:
        return "公开来源摘要"
    lang = (pick.lang or "").lower()
    if lang.startswith("en"):
        return "译自英文"
    if lang.startswith("ja"):
        return "译自日文"
    if lang.startswith("ko"):
        return "译自韩文"
    text = pick.text or ""
    if text:
        latin = sum(1 for c in text if ord(c) < 128)
        if latin / max(len(text), 1) > 0.65:
            return "译自英文"
    return "译文"


def stats_html(pick: Pick) -> str:
    source = str(pick.raw.get("source") or "unknown").strip().lower()
    if source == "zhihu":
        labels = {"likes": "赞同", "replies": "评论", "favorites": "收藏"}
        available = pick.raw.get("available_metrics", [])
        stats = "".join(f"<span><b>{format_count(getattr(pick, key))}</b> {label}</span>"
                        for key, label in labels.items() if key in available)
        return stats or "<span>知乎来源 · 未提供互动数据</span>"
    if source == "github":
        values = (
            (pick.likes, "stars"),
            (pick.shares, "forks"),
            (pick.replies, "issues"),
        )
    elif source == "hacker_news":
        values = ((pick.likes, "points"), (pick.replies, "comments"))
    elif source == "rss":
        feed = html.escape(str(pick.raw.get("feed_title") or "RSS"))
        return f"<span><b>RSS</b> {feed}</span>"
    else:
        values = (
            (pick.likes, "likes"),
            (pick.shares, "shares"),
            (pick.views, "views"),
        )
    return "".join(
        f"<span><b>{format_count(value)}</b> {label}</span>"
        for value, label in values
    )


def build_card_html(
    pick: Pick,
    *,
    index: int | None = None,
    total: int | None = None,
    show_date: bool = True,
) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    images = [u for u in pick.media_urls if is_image_url(u)]
    media_html = ""
    if images:
        src = html.escape(images[0], quote=True)
        media_html = f'<div class="media"><img src="{src}" alt="" /></div>'

    verified = '<span class="verified" title="verified">✔</span>' if pick.author_verified else ""
    avatar = pick.author_avatar_url.strip()
    if avatar:
        avatar_html = (
            f'<img class="avatar-img" src="{html.escape(avatar, quote=True)}" alt="" />'
        )
    else:
        avatar_html = f'<div class="avatar-fallback">{_initials(pick.author_name)}</div>'

    body = "<br>".join(
        html.escape(line) for line in clip_display_text(pick.text, MAX_CONTENT_CHARS).splitlines()
    ) or "&nbsp;"
    translation = html.escape(punchline(pick.translation or pick.text or "内容摘要"))

    bg_style = background_style(pick)

    index_label = f"{int(index):02d}" if index else "01"
    short_class = "short" if len(pick.text or "") < 160 else ""

    mapping = {
        "{{BG_STYLE}}": bg_style,
        "{{SOURCE_LABEL}}": "知乎精选" if pick.raw.get("source") == "zhihu" else "内容精选",
        "{{INDEX}}": index_label,
        "{{DATE}}": html.escape(format_md_date(pick.created_at) if show_date and pick.created_at else ""),
        "{{SHORT_CLASS}}": short_class,
        "{{AUTHOR_NAME}}": html.escape(pick.author_name or pick.author_username or "Unknown"),
        "{{HANDLE}}": html.escape(pick.author_username or ""),
        "{{VERIFIED}}": verified,
        "{{TIME}}": html.escape(format_content_time(pick.created_at)),
        "{{AVATAR}}": avatar_html,
        "{{BODY}}": body,
        "{{TRANSLATION}}": translation,
        "{{CAPTION}}": html.escape(translation_caption(pick)),
        "{{MEDIA}}": media_html,
        "{{STATS}}": stats_html(pick),
        "{{LIKES}}": format_count(pick.likes),
        "{{SHARES}}": format_count(pick.shares),
        "{{REPLIES}}": format_count(pick.replies),
        "{{VIEWS}}": format_count(pick.views),
        "{{URL}}": html.escape(pick.url or ""),
    }
    out = template
    for key, value in mapping.items():
        out = out.replace(key, value)
    return out


def screenshot_html(
    html_doc: str,
    output: Path,
    *,
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Card rendering requires Playwright. Install with: "
            'pip install playwright && playwright install chromium'
        ) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            raise RuntimeError(
                "Chromium is not installed for Playwright. Run: playwright install chromium"
            ) from exc
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
        )
        # Production cards do not fetch untrusted remote images or local file URLs.
        # Operators may keep development media enabled; public deployment stays text-only.
        if os.getenv("C2VIDEO_ENV") == "production":
            page.route("**/*", lambda route: route.abort())
        page.set_content(html_doc, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            page.wait_for_timeout(1200)
        page.screenshot(path=str(output), full_page=False, type="png")
        browser.close()
    return output


def render_pick_card(pick: Pick, output: Path) -> Path:
    return screenshot_html(build_card_html(pick), output)


def build_opener_html(hook: str, *, count: int = 0, date_label: str = "") -> str:
    template = _OPENER_PATH.read_text(encoding="utf-8")
    meta = f"{count} 条热帖" if count else "内容精选速览"
    return (
        template.replace("{{HOOK}}", html.escape(hook or "圈里这几条都在转"))
        .replace("{{META}}", html.escape(meta))
        .replace("{{DATE}}", html.escape(date_label or format_md_date()))
    )


def render_opener(
    hook: str, output: Path, *, count: int = 0, date_label: str = ""
) -> Path:
    return screenshot_html(
        build_opener_html(hook, count=count, date_label=date_label), output
    )


def build_cover_html(
    *,
    title: str,
    date_label: str,
    sub: str,
    bg_style: str,
) -> str:
    template = _COVER_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{TITLE}}", html.escape(title or "内容精选"))
        .replace("{{DATE}}", html.escape(date_label or format_md_date()))
        .replace("{{SUB}}", html.escape(sub or "热帖速览"))
        .replace("{{BG_STYLE}}", bg_style)
    )


def render_cover(
    output: Path,
    *,
    title: str,
    date_label: str,
    sub: str,
    bg_style: str,
) -> Path:
    return screenshot_html(
        build_cover_html(
            title=title, date_label=date_label, sub=sub, bg_style=bg_style
        ),
        output,
        width=COVER_WIDTH,
        height=COVER_HEIGHT,
    )


def run_card(
    cfg: C2VideoConfig,
    *,
    date: str | None = None,
    input_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    run_dir = resolve_run_dir(cfg.work_dir, date)
    src = input_path or (run_dir / "picks.json")
    if not src.exists():
        raise FileNotFoundError(f"Picks file not found: {src}. Run `c2video curate` first.")
    _meta, picks = load_picks(src)
    dest = output_dir or (run_dir / "cards")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    total = len(picks)
    show_date = not is_same_day_digest(picks)
    for i, pick in enumerate(picks, start=1):
        path = dest / f"card_{pick.id}.png"
        html_path = dest / f"card_{pick.id}.html"
        doc = build_card_html(pick, index=i, total=total, show_date=show_date)
        html_path.write_text(doc, encoding="utf-8")
        screenshot_html(doc, path)
        written.append(path)
    return {"input": src, "output_dir": dest, "cards": written, "count": len(written)}
