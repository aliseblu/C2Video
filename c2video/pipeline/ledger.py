"""Ledger: persistent record of seen content IDs and completed Picks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from c2video.io_atomic import atomic_write_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    """On-disk ledger at ``work/ledger.json``.

    * ``seen`` — content IDs already pulled by fetch (skip next day)
    * ``picks`` — content IDs already turned into a digest Pick
    """

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        payload = data or {}
        seen = payload.get("seen") or {}
        picks = payload.get("picks") or {}
        # Allow a bare list of ids from older drafts
        if isinstance(seen, list):
            seen = {str(i): {} for i in seen}
        if isinstance(picks, list):
            picks = {str(i): {} for i in picks}
        self.seen: dict[str, dict[str, Any]] = {str(k): dict(v) for k, v in seen.items()}
        self.picks: dict[str, dict[str, Any]] = {str(k): dict(v) for k, v in picks.items()}

    @classmethod
    def load(cls, work_dir: str | Path) -> Ledger:
        path = Path(work_dir) / "ledger.json"
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Ledger 无法读取，请检查或从备份恢复；不会清空去重历史。") from exc
        if not isinstance(raw, dict):
            raise ValueError("Ledger 格式无效；不会清空去重历史。")
        return cls(path, raw)

    def is_seen(self, content_id: str) -> bool:
        return str(content_id) in self.seen

    def is_pick(self, content_id: str) -> bool:
        return str(content_id) in self.picks

    def mark_seen(self, content_ids: Iterable[str], *, extra: dict[str, Any] | None = None) -> None:
        for content_id in content_ids:
            tid = str(content_id)
            record = dict(self.seen.get(tid) or {})
            record.setdefault("first_seen", _now_iso())
            record["last_seen"] = _now_iso()
            if extra:
                record.update(extra)
            self.seen[tid] = record

    def mark_picks(self, content_ids: Iterable[str], *, extra: dict[str, Any] | None = None) -> None:
        for content_id in content_ids:
            tid = str(content_id)
            record = dict(self.picks.get(tid) or {})
            record.setdefault("picked_at", _now_iso())
            if extra:
                record.update(extra)
            self.picks[tid] = record
            if tid not in self.seen:
                self.mark_seen([tid])

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen": self.seen,
            "picks": self.picks,
        }
        atomic_write_text(self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        return self.path
