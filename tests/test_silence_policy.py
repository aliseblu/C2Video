"""Narration pauses are observations; sustained/mass silence remains blocking."""

import json
import subprocess

import pytest

from c2video.application import ApplicationService
from c2video.domain.models import QualityIssue
from c2video.io_atomic import atomic_write_text
from c2video.media_quality import assess_silence
from c2video.media_tools import ffmpeg_path
from c2video.tools.base import ToolContext
from c2video.tools.content import _probe_media_checks
from c2video.tools.live_quality import LiveQualityTool


def log_intervals(*intervals):
    return "\n".join(
        f"[silencedetect] silence_start: {start}\n"
        f"[silencedetect] silence_end: {end} | silence_duration: {end - start}"
        for start, end in intervals
    )


def test_normal_sentence_pauses_are_measured_but_not_blocking():
    result = assess_silence(log_intervals((3.6, 4.3), (10.4, 11.1)), duration_seconds=20)
    assert not result["silence_failures"]
    assert result["silence_summary"]["detected_count"] == 2
    assert result["silence_summary"]["total_seconds"] == pytest.approx(1.4)
    assert result["silence_summary"]["ratio"] == pytest.approx(0.07)
    assert result["silence_first_failure_seconds"] is None


def test_long_gap_reports_exact_interval():
    result = assess_silence(log_intervals((5, 7)), duration_seconds=20)
    assert result["silence_failures"] and "5.00–7.00" in result["silence_failures"][0]
    assert result["silence_first_failure_seconds"] == 5


def test_short_fully_silent_audio_still_fails():
    result = assess_silence(log_intervals((0, 1)), duration_seconds=1)
    assert result["silence_summary"]["ratio"] == 1
    assert "静音占比" in result["silence_failures"][0]


def test_repeated_short_dropouts_fail_high_silent_ratio():
    result = assess_silence(log_intervals(*[(i, i + 0.7) for i in range(10)]), duration_seconds=10)
    assert result["silence_summary"]["longest_seconds"] == 0.7
    assert result["silence_summary"]["ratio"] == pytest.approx(0.7)
    assert "静音占比" in result["silence_failures"][0]


def test_open_interval_at_eof_is_not_ignored():
    result = assess_silence("silence_start: 8", duration_seconds=10)
    assert result["silence_intervals"] == [
        {"start_seconds": 8, "end_seconds": 10, "duration_seconds": 2}
    ]
    assert result["silence_failures"]


def test_end_with_explicit_duration_can_reconstruct_interval():
    result = assess_silence("silence_end: 3 | silence_duration: 2", duration_seconds=10)
    assert result["silence_first_failure_seconds"] == 1


def test_overlaps_and_codec_padding_do_not_double_count_silence():
    result = assess_silence(log_intervals((-0.05, 0.7), (0.6, 1)), duration_seconds=10)
    assert result["silence_summary"]["total_seconds"] == 1
    assert result["silence_summary"]["detected_count"] == 1


def test_no_detected_silence_has_zero_fraction():
    result = assess_silence("mean_volume: -20.0 dB", duration_seconds=20)
    assert not result["silence_intervals"] and not result["silence_failures"]
    assert result["silence_summary"]["ratio"] == 0


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_media_duration_fails_closed(duration):
    with pytest.raises(ValueError):
        assess_silence("", duration_seconds=duration)


@pytest.mark.parametrize(
    "output",
    [
        "silence_start: nan",
        "silence_end: 4",
        "silence_start: 4\nsilence_start: 5",
        "silence_start: 5\nsilence_end: 3",
    ],
)
def test_bad_probe_timestamps_cannot_be_treated_as_pass(output):
    with pytest.raises(ValueError):
        assess_silence(output, duration_seconds=10)


@pytest.mark.parametrize(
    "volume,passes,has_audio",
    [
        ("if(between(t,3,3.7),0,1)", True, True),
        ("if(between(t,3,5),0,1)", False, True),
        ("0", False, True),
        ("if(lt(mod(t,1),0.7),0,1)", False, True),
        ("1", False, False),
    ],
)
def test_real_video_quality_distinguishes_pauses_and_audio_failures(
    tmp_path, volume, passes, has_audio
):
    service = ApplicationService(work_dir=str(tmp_path))
    run_id = service.create_run(query="silence regression fixture")["run"]["run_id"]
    root = tmp_path / "agent_runs" / run_id
    publish = root / "publish_kit"
    publish.mkdir(parents=True)
    ffmpeg = ffmpeg_path()
    command = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=5"]
    if has_audio:
        command += [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000:duration=9",
            "-af",
            f"volume='{volume}':eval=frame",
            "-c:a",
            "aac",
        ]
    command += [
        "-t",
        "9",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-y",
        str(publish / "video.mp4"),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(publish / "video.mp4"),
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(publish / "cover.png"),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    atomic_write_text(root / "script.final.json", '{"segments":[{}]}')
    tool = LiveQualityTool(service.store)
    context = ToolContext(run_id, "qc", str(tmp_path))
    if passes:
        # A real re-inspection clears only the previous matching false positive.
        service.store.add_quality_issue(
            QualityIssue(
                run_id=run_id,
                issue_id=f"quality_{run_id}_silence",
                code="SILENCE",
                category="audio",
                severity="blocker",
                description="old half-second rule",
            )
        )
        tool.inspect(context)
        previous = service.store.payloads("quality_issues", run_id)
        assert previous and previous[0]["resolved"]
    else:
        with pytest.raises(RuntimeError, match="SILENCE" if has_audio else "AUDIO_STREAM"):
            tool.inspect(context)
    report = json.loads((publish / "qc.after.json").read_text())
    assert report["ok"] is passes
    assert report["checks"]["audio_stream"] is has_audio
    if has_audio:
        diagnostics = report["ffmpeg_diagnostics"]
        assert diagnostics["silence_segments"]  # Raw observations are never suppressed.
        assert bool(diagnostics["silence_failures"]) is not passes
        if not passes:
            issue = next(i for i in report["issues"] if i["code"] == "SILENCE")
            assert issue["timestamp_seconds"] is not None
            assert any("秒" in e or "占比" in e for e in issue["evidence"])


def test_fully_silent_one_second_fixture_is_still_a_detected_defect(tmp_path):
    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            ffmpeg_path(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    result = _probe_media_checks(video)
    assert result["silence_segments"] and result["silence_failures"]
