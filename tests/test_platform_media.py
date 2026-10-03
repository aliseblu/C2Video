"""Inspect generated media, not mocked ffprobe output. All fixtures are local."""
import json
import subprocess

import pytest

from c2video.application import ApplicationService
from c2video.config.schema import C2VideoConfig
from c2video.domain.models import EditorialDecision
from c2video.io_atomic import atomic_write_text
from c2video.media_tools import ffmpeg_path
from c2video.pipeline.io import load_picks, write_picks
from c2video.pipeline.models import Pick
from c2video.pipeline.workdir import resolve_run_dir
from c2video.tools.base import ToolContext
from c2video.tools.legacy_pipeline import _apply_approved_selection
from c2video.tools.live_quality import LiveQualityTool


@pytest.mark.parametrize("size,passes", [("1080x1920", True), ("320x180", False)])
def test_real_media_validation(tmp_path, size, passes):
    service = ApplicationService(work_dir=str(tmp_path))
    run_id = service.create_run(query="media fixture")["run"]["run_id"]
    root = tmp_path / "agent_runs" / run_id
    publish = root / "publish_kit"
    publish.mkdir(parents=True)
    ffmpeg = ffmpeg_path()
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=5", "-f", "lavfi", "-i",
                    "sine=frequency=1000:sample_rate=44100", "-t", "9",
                    "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(publish / "video.mp4")],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1080x1920", "-frames:v", "1", "-threads", "1",
                    "-y", str(publish / "cover.png")],
                   check=True, capture_output=True, timeout=30)
    atomic_write_text(root / "script.final.json", '{"segments":[{}]}')
    tool = LiveQualityTool(service.store)
    context = ToolContext(run_id, "qc", str(tmp_path))
    if passes:
        tool.inspect(context)
    else:
        with pytest.raises(RuntimeError, match="VERTICAL_VIDEO"):
            tool.inspect(context)
    report = json.loads((publish / "qc.after.json").read_text())
    assert report["ok"] is passes
    assert report["checks"]["audio_stream"]
    assert report["checks"]["silence"]
    assert report["checks"]["black_frames"]
    assert report["ffmpeg_diagnostics"]["mean_volume_db"] is not None


def test_approved_selection_reorders_materialized_inputs(tmp_path):
    service = ApplicationService(work_dir=str(tmp_path))
    run_id = service.create_run(query="selection")["run"]["run_id"]
    config = C2VideoConfig(work_dir=str(tmp_path / "pipeline"))
    day = resolve_run_dir(config.work_dir, "2026-09-15")
    write_picks(day / "picks.json", [Pick(id="a", text="A"), Pick(id="b", text="B")])
    for candidate_id, rank, selected in [("a", 2, False), ("b", 1, True)]:
        service.store.add_decision(EditorialDecision(
            run_id=run_id, candidate_id=candidate_id, selected=selected,
            rank=rank, confidence=.8, decision_summary="reviewed"))
    _apply_approved_selection(config, "2026-09-15", run_id, service.store)
    assert [p.id for p in load_picks(day / "picks.json")[1]] == ["b"]
    assert [p.id for p in load_picks(day / "picks.proposed.json")[1]] == ["a", "b"]
