"""Real media validation before the live publication gate; no simulated repairs."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from c2video.domain.models import Artifact, QualityIssue
from c2video.io_atomic import atomic_write_text
from c2video.media_tools import ffprobe_path
from c2video.pipeline.qc import inspect_kit
from c2video.tools.base import AgentTool, ToolResult
from c2video.tools.content import _probe_media_checks


class LiveQualityTool(AgentTool):
    name = "live.quality"

    def __init__(self, store):
        self.store = store

    async def execute(self, context):
        return await asyncio.to_thread(self.inspect, context)

    def inspect(self, context):
        root = Path(context.work_dir) / "agent_runs" / context.run_id
        publish = root / "publish_kit"
        video, cover = publish / "video.mp4", publish / "cover.png"
        issues = []
        checks = {"inspection_completed": False}
        duration, diagnostics = None, {}
        report = {"ok": False, "errors": [], "warnings": []}
        try:
            script = json.loads((root / "script.final.json").read_text(encoding="utf-8"))
            report = inspect_kit(video=video, cover=cover, pick_count=len(script.get("segments", [])),
                                 min_picks=1)
            checks["files_and_duration"] = report["ok"]
            duration = None
            diagnostics = {}
            if report["ok"]:
                probe = subprocess.run(
                    [ffprobe_path(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                    capture_output=True, text=True, check=True, timeout=30)
                data = json.loads(probe.stdout)
                streams = data.get("streams", [])
                checks["vertical_video"] = any(s.get("codec_type") == "video"
                                               and s.get("width") == 1080 and s.get("height") == 1920
                                               for s in streams)
                checks["audio_stream"] = any(s.get("codec_type") == "audio" for s in streams)
                duration = float(data.get("format", {}).get("duration", 0))
                diagnostics = _probe_media_checks(video)
                checks["black_frames"] = not diagnostics["black_segments"]
                checks["silence"] = not diagnostics["silence_failures"]
                checks["audio_level_measured"] = diagnostics["mean_volume_db"] is not None
                checks["loudness"] = diagnostics["mean_volume_db"] is not None and diagnostics["mean_volume_db"] <= -12
            checks["inspection_completed"] = True
        except Exception as exc:
            # Persist a current failed report even when probes or source JSON fail.
            report["errors"] = [f"媒体检查异常：{type(exc).__name__}；请检查依赖与文件。"]
            checks["inspection_completed"] = False
        for name, ok in checks.items():
            if not ok:
                issue = QualityIssue(
                    run_id=context.run_id, issue_id=f"quality_{context.run_id}_{name}",
                    code=name.upper(), category="audio" if name in {"silence", "audio_stream", "audio_level_measured", "loudness"} else "visual", severity="blocker",
                    description=("检测到异常静音：" + "；".join(diagnostics["silence_failures"])
                                 if name == "silence" else f"媒体检查未通过：{name}，请人工检查后重试。"),
                    timestamp_seconds=(diagnostics.get("silence_first_failure_seconds")
                                       if name == "silence" else None),
                    auto_fixable=False,
                    evidence=diagnostics["silence_failures"] if name == "silence" else [name],
                )
                self.store.add_quality_issue(issue)
                issues.append(issue)
            else:
                for previous in self.store.payloads("quality_issues", context.run_id):
                    if previous["issue_id"] == f"quality_{context.run_id}_{name}" and not previous.get("resolved"):
                        self.store.resolve_quality_issue(QualityIssue.model_validate(previous))
        result = {**report, "schema_version": "1.0", "checks": checks,
                  "duration_seconds": duration, "ffmpeg_diagnostics": diagnostics,
                  "issues": [i.model_dump(mode="json") for i in issues],
                  "unresolved": [i.code for i in issues], "ok": not issues}
        # Full media inspection is actual work, not an invented 'repaired' label.
        text = json.dumps(result, ensure_ascii=False, indent=2)
        atomic_write_text(publish / "qc.before.json", text)
        atomic_write_text(publish / "qc.after.json", text)
        if issues:
            detail = "；".join(diagnostics.get("silence_failures", []))
            raise RuntimeError("媒体质检未通过：" + "、".join(i.code for i in issues)
                               + (f"。{detail}" if detail else ""))
        return ToolResult(summary="媒体质检通过；事实准确性与偏好效果仍需人工审核。",
                          artifacts=[Artifact(run_id=context.run_id, kind="quality_report",
                                              path=str(publish / "qc.after.json"))])

