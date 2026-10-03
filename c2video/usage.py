"""Per-request model telemetry. Never store prompts, responses, or credentials."""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from c2video.config.schema import LLMConfig
from c2video.security import redact_text

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    usage_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    run_id TEXT,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cached_input_tokens INTEGER,
    reported_cost_usd REAL,
    estimated_cost_usd REAL,
    latency_ms INTEGER NOT NULL,
    error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_run ON llm_usage(run_id);
"""


def _tokens(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _money(value: Any) -> float | None:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def usage_values(data: dict[str, Any] | None, config: LLMConfig) -> dict[str, Any]:
    usage = (data or {}).get("usage")
    usage = usage if isinstance(usage, dict) else {}
    inputs = _tokens(usage.get("prompt_tokens", usage.get("input_tokens")))
    outputs = _tokens(usage.get("completion_tokens", usage.get("output_tokens")))
    total = _tokens(usage.get("total_tokens"))
    if total is None and inputs is not None and outputs is not None:
        total = inputs + outputs
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details"))
    cached = _tokens(details.get("cached_tokens")) if isinstance(details, dict) else None
    # A generic 'cost' field has no reliable currency/unit. Only accept explicit USD.
    reported = _money(usage.get("cost_usd"))
    estimated = None
    if (reported is None and inputs is not None and outputs is not None
            and config.input_usd_per_million is not None
            and config.output_usd_per_million is not None):
        estimated = (inputs * config.input_usd_per_million
                     + outputs * config.output_usd_per_million) / 1_000_000
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": total,
            "cached_input_tokens": cached, "reported_cost_usd": reported,
            "estimated_cost_usd": estimated}


def record_usage(
    config: LLMConfig,
    *,
    data: dict[str, Any] | None,
    status: str,
    http_status: int | None,
    latency_ms: int,
    error_type: str | None,
) -> None:
    if not config.telemetry_db_path:
        return
    path = Path(config.telemetry_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "usage_id": f"usage_{uuid4().hex}",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": config.telemetry_run_id,
        "purpose": config.telemetry_purpose,
        "model": redact_text(config.model)[:160],
        "status": status,
        "http_status": http_status,
        **usage_values(data, config),
        "latency_ms": max(0, latency_ms),
        "error_type": error_type,
    }
    db = sqlite3.connect(path, timeout=1)
    try:
        with db:
            db.executescript(USAGE_SCHEMA)
            names = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            db.execute(f"INSERT INTO llm_usage ({names}) VALUES ({placeholders})", tuple(values.values()))
    finally:
        db.close()


_AGGREGATES = """
    count(*) AS calls,
    sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_calls,
    sum(CASE WHEN status='canceled' THEN 1 ELSE 0 END) AS canceled_calls,
    sum(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL
        THEN 1 ELSE 0 END) AS unknown_usage_calls,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(cached_input_tokens) AS cached_input_tokens,
    sum(reported_cost_usd) AS reported_cost_usd,
    sum(estimated_cost_usd) AS estimated_cost_usd,
    sum(CASE WHEN reported_cost_usd IS NULL AND estimated_cost_usd IS NULL
        THEN 1 ELSE 0 END) AS unpriced_calls,
    avg(latency_ms) AS average_latency_ms
"""


def usage_dashboard(db_path: str | Path, *, days: int = 7) -> dict[str, Any]:
    if days not in {7, 30, 90}:
        raise ValueError("days must be 7, 30, or 90")
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    cutoff = start.isoformat()
    db = sqlite3.connect(db_path, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        # Keep the cards, breakdowns and timeline on one read snapshot.
        db.execute("BEGIN")
        totals = dict(db.execute(f"SELECT {_AGGREGATES} FROM llm_usage WHERE created_at>=?", (cutoff,)).fetchone())
        for name in ("failed_calls", "canceled_calls", "unknown_usage_calls", "unpriced_calls"):
            totals[name] = totals[name] or 0
        models = [dict(row) for row in db.execute(
            f"SELECT model,{_AGGREGATES} FROM llm_usage WHERE created_at>=? GROUP BY model ORDER BY calls DESC,model",
            (cutoff,),
        )]
        purposes = [dict(row) for row in db.execute(
            f"SELECT purpose,{_AGGREGATES} FROM llm_usage WHERE created_at>=? GROUP BY purpose ORDER BY calls DESC,purpose",
            (cutoff,),
        )]
        grouped = {row["date"]: dict(row) for row in db.execute(
            f"SELECT substr(created_at,1,10) AS date,{_AGGREGATES} FROM llm_usage WHERE created_at>=? GROUP BY date",
            (cutoff,),
        )}
        daily = []
        for offset in range(days):
            date = (start + timedelta(days=offset)).date().isoformat()
            daily.append(grouped.get(date, {"date": date, "calls": 0, "total_tokens": None,
                                           "unknown_usage_calls": 0}))
        recent = [dict(row) for row in db.execute(
            "SELECT * FROM llm_usage WHERE created_at>=? ORDER BY created_at DESC,usage_id DESC LIMIT 30", (cutoff,),
        )]
        runs = {row["state"]: row["count"] for row in db.execute(
            "SELECT state,count(*) AS count FROM runs WHERE created_at>=? GROUP BY state", (cutoff,),
        )}
        memories = db.execute("SELECT count(*) FROM memories WHERE status='approved'").fetchone()[0]
        new_memories = db.execute(
            "SELECT count(*) FROM memories WHERE created_at>=?", (cutoff,),
        ).fetchone()[0]
        feedback = db.execute("SELECT count(*) FROM feedback WHERE created_at>=?", (cutoff,),).fetchone()[0]
        return {"days": days, "timezone": "UTC", "generated_at": datetime.now(UTC).isoformat(),
                "totals": totals, "models": models, "purposes": purposes, "daily": daily,
                "recent": recent, "runs": runs,
                "memories": {"approved_now": memories, "created_in_period": new_memories},
                "feedback_count": feedback}
    finally:
        db.close()
