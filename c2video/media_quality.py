"""Separate measured quiet intervals from blocking defects in narrated videos.

FFmpeg's 0.5 s detection window is an observation threshold, not a quality
failure by itself. These application limits tolerate normal sentence breaks
while still blocking long gaps and predominantly silent audio.
"""

from __future__ import annotations

import math
import re
from typing import Any

SILENCE_NOISE_DB = -50
SILENCE_DETECTION_SECONDS = 0.5
MAX_SILENCE_GAP_SECONDS = 1.5
MAX_SILENCE_RATIO = 0.35
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_EVENT = re.compile(rf"\bsilence_(start|end):\s*({_NUMBER})")


def assess_silence(output: str, *, duration_seconds: float) -> dict[str, Any]:
    """Pair start/end timestamps and apply the narrated-video quality policy.

    An unfinished interval extends to EOF. Union intervals before calculating
    the silent fraction, so overlapping/duplicate reports cannot inflate it.
    Invalid measurements fail closed rather than yielding a false pass.
    """
    duration = float(duration_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Silence inspection requires a positive finite duration")
    raw_intervals: list[tuple[float, float]] = []
    pending: float | None = None
    for line in output.splitlines():
        events = list(_EVENT.finditer(line))
        if ("silence_start:" in line or "silence_end:" in line) and not events:
            raise ValueError("Unparseable silence timestamp")
        for event in events:
            kind, value = event.groups()
            seconds = float(value)
            if not math.isfinite(seconds):
                raise ValueError("Invalid silence timestamp")
            if kind == "start":
                if pending is not None:
                    raise ValueError("Silence start without a matching end")
                pending = seconds
            else:
                if pending is None:
                    span = re.search(rf"silence_duration:\s*({_NUMBER})", line)
                    if not span:
                        raise ValueError("Silence end without a start or duration")
                    pending = seconds - float(span.group(1))
                if seconds < pending:
                    raise ValueError("Silence interval ends before it starts")
                raw_intervals.append((pending, seconds))
                pending = None
    if pending is not None:
        raw_intervals.append((pending, duration))

    merged: list[list[float]] = []
    for start, end in sorted(raw_intervals):
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    intervals = [
        {
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "duration_seconds": round(end - start, 6),
        }
        for start, end in merged
    ]
    total = sum(end - start for start, end in merged)
    longest = max((end - start for start, end in merged), default=0.0)
    ratio = total / duration
    long_intervals = [s for s in intervals if s["duration_seconds"] >= MAX_SILENCE_GAP_SECONDS]
    failures = [
        f"连续静音 {s['start_seconds']:.2f}–{s['end_seconds']:.2f} 秒"
        f"（{s['duration_seconds']:.2f} 秒，阈值 {MAX_SILENCE_GAP_SECONDS:.1f} 秒）"
        for s in long_intervals
    ]
    if ratio >= MAX_SILENCE_RATIO:
        failures.append(f"静音占比 {ratio:.1%}，达到 {MAX_SILENCE_RATIO:.0%} 阈值")
    affected = long_intervals or (intervals if failures else [])
    return {
        "silence_intervals": intervals,
        "silence_failures": failures,
        "silence_first_failure_seconds": affected[0]["start_seconds"] if affected else None,
        "silence_summary": {
            "detected_count": len(intervals),
            "total_seconds": round(total, 6),
            "longest_seconds": round(longest, 6),
            "ratio": round(ratio, 6),
            "policy": {
                "noise_db": SILENCE_NOISE_DB,
                "detection_seconds": SILENCE_DETECTION_SECONDS,
                "max_gap_seconds": MAX_SILENCE_GAP_SECONDS,
                "max_ratio": MAX_SILENCE_RATIO,
            },
        },
    }
