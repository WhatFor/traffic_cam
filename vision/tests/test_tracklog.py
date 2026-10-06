"""Track logs: what the writer puts on disk, and what a replay makes of it."""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import supervision as sv
from test_geometry import nothing
from test_passages import CONFIG
from test_pipeline import CONFIG_HASH, DRIVE, DRIVE_AND_GONE, NO_IMAGE, pipeline, run

from trafficcam.geometry import Observation, SceneGeometry
from trafficcam.pipeline import FrameResult
from trafficcam.sources import Frame
from trafficcam.tracklog import FrameRecord, Header
from trafficcam.tracklog import writer as writer_module
from trafficcam.tracklog.replay import TrackLogReplay, read_track_log, to_detections
from trafficcam.tracklog.writer import TrackLogWriter

HOUR = datetime(2026, 10, 4, 13, tzinfo=UTC)


def writer_for(
    directory: Path, max_queued: int = 900, retry_s: float = 60.0, wait_when_full: bool = False
) -> TrackLogWriter:
    return TrackLogWriter(
        directory,
        camera="junction-1",
        config_hash=CONFIG_HASH,
        wait_when_full=wait_when_full,
        max_queued=max_queued,
        retry_s=retry_s,
    )


def empty_result(index: int, timestamp: datetime) -> FrameResult:
    return FrameResult(
        frame=Frame(index=index, timestamp=timestamp, image=NO_IMAGE),
        detections=sv.Detections.empty(),
        inference_ms=0.0,
        observation=SceneGeometry(CONFIG).observe(nothing(), timestamp),
        passages=[],
        events=[],
    )


def record(directory: Path, results: list[FrameResult]) -> None:
    writer = writer_for(directory)
    for result in results:
        writer.observe(result)
    writer.close()


def read(directory: Path) -> list[Header | FrameRecord]:
    return list(read_track_log(sorted(directory.glob("*.jsonl"))))


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_what_is_read_back_is_what_the_pipeline_saw(tmp_path: Path) -> None:
    _, results = run(DRIVE_AND_GONE)

    record(tmp_path, results)

    header, *records = read(tmp_path)
    assert isinstance(header, Header)
    assert (header.camera, header.config_hash) == ("junction-1", CONFIG_HASH)
    assert header.started == results[0].frame.timestamp
    assert len(records) == len(results)
    for written, result in zip(records, results, strict=True):
        assert isinstance(written, FrameRecord)
        assert (written.frame, written.ts) == (result.frame.index, result.frame.timestamp)
        replayed = to_detections(written.detections)
        assert replayed.xyxy.dtype == np.float32
        assert replayed.xyxy.tobytes() == result.detections.xyxy.tobytes()
        tracks = result.observation.tracks
        assert [track.id for track in written.tracks] == (
            [] if tracks.tracker_id is None else tracks.tracker_id.tolist()
        )
    frames = [written for written in records if isinstance(written, FrameRecord)]
    assert {zone for frame in frames for track in frame.tracks for zone in track.zones} == {
        "approach",
        "box",
        "exit",
    }
    assert [crossing.line for frame in frames for crossing in frame.crossings] == ["stopline"]


def test_each_hour_gets_its_own_file_and_header(tmp_path: Path) -> None:
    last_of_hour = HOUR + timedelta(minutes=59, seconds=59.9)
    first_of_next = HOUR + timedelta(hours=1)

    record(tmp_path, [empty_result(0, last_of_hour), empty_result(1, first_of_next)])

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "2026-10-04T13.jsonl",
        "2026-10-04T14.jsonl",
    ]
    assert [type(line) for line in read(tmp_path)] == [Header, FrameRecord, Header, FrameRecord]


def test_a_restart_within_the_hour_appends_under_a_new_header(tmp_path: Path) -> None:
    record(tmp_path, [empty_result(0, HOUR)])
    record(tmp_path, [empty_result(0, HOUR + timedelta(minutes=1))])

    assert [path.name for path in tmp_path.iterdir()] == ["2026-10-04T13.jsonl"]
    assert [type(line) for line in read(tmp_path)] == [Header, FrameRecord, Header, FrameRecord]


def test_only_old_track_logs_are_deleted(tmp_path: Path) -> None:
    for name in (
        "2026-09-27T12.jsonl",  # an hour past seven days old
        "2026-09-27T13.jsonl",
        "2026-09-01T00.jsonl.bak",
        "notes.txt",
    ):
        (tmp_path / name).write_text("")

    record(tmp_path, [empty_result(0, HOUR)])

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "2026-09-01T00.jsonl.bak",
        "2026-09-27T13.jsonl",
        "2026-10-04T13.jsonl",
        "notes.txt",
    ]


def test_a_full_queue_drops_and_counts_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writing, release = threading.Event(), threading.Event()
    to_record = writer_module.to_record

    def stalled(
        index: int, timestamp: datetime, detections: sv.Detections, observation: Observation
    ) -> FrameRecord:
        writing.set()
        release.wait()
        return to_record(index, timestamp, detections, observation)

    monkeypatch.setattr(writer_module, "to_record", stalled)
    writer = writer_for(tmp_path, max_queued=3)

    writer.observe(empty_result(0, HOUR))
    writing.wait()
    started = time.monotonic()
    for index in range(1, 6):
        writer.observe(empty_result(index, HOUR))
    elapsed = time.monotonic() - started
    release.set()
    writer.close()

    assert writer.dropped == 2
    assert elapsed < 1
    frames = [line.frame for line in read(tmp_path) if isinstance(line, FrameRecord)]
    assert frames == [0, 1, 2, 3]


def test_a_replay_waits_for_a_full_queue_and_drops_nothing(tmp_path: Path) -> None:
    writer = writer_for(tmp_path, max_queued=3, wait_when_full=True)
    result = empty_result(0, HOUR)

    for _ in range(300):
        writer.observe(result)
    writer.close()

    assert writer.dropped == 0
    assert len(read(tmp_path)) == 301


def test_a_missing_directory_is_waited_for_not_created(tmp_path: Path) -> None:
    directory = tmp_path / "tracklogs"
    writer = writer_for(directory, retry_s=0.05)

    writer.observe(empty_result(0, HOUR))
    wait_until(lambda: writer.dropped >= 1)
    assert not directory.exists()

    directory.mkdir()
    index = 1
    while not any(directory.iterdir()):
        writer.observe(empty_result(index, HOUR))
        index += 1
        assert index < 500, "nothing was written"
        time.sleep(0.01)
    writer.close()

    header, first, *_ = read(directory)
    assert isinstance(header, Header)
    assert isinstance(first, FrameRecord)


def test_a_replay_gives_the_passages_of_the_recorded_run(tmp_path: Path) -> None:
    live, results = run(DRIVE)
    recorded = [passage for result in results for passage in result.passages] + live.flush()[0]
    record(tmp_path, results)

    replay = TrackLogReplay(sorted(tmp_path.iterdir()), CONFIG_HASH)
    replaying = pipeline(replay)
    replayed = [
        passage for frame in replay.frames() for passage in replaying.process(frame).passages
    ]
    replayed += replaying.flush()[0]

    assert len(recorded) == 1
    assert replayed == recorded


def test_a_replay_notes_a_log_recorded_with_another_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record(tmp_path, [empty_result(0, HOUR)])

    frames = list(TrackLogReplay(sorted(tmp_path.iterdir()), "sha256:other").frames())

    assert [frame.index for frame in frames] == [0]
    assert "different config" in capsys.readouterr().err


def test_reading_stops_at_a_line_that_was_cut_short(tmp_path: Path) -> None:
    record(tmp_path, [empty_result(0, HOUR), empty_result(1, HOUR)])
    (path,) = tmp_path.iterdir()
    path.write_text(path.read_text()[:-20])

    assert [type(line) for line in read(tmp_path)] == [Header, FrameRecord]


def test_a_line_that_is_not_a_record_names_its_file_and_line(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-04T13.jsonl"
    path.write_text('{"frame": 0}\n')

    with pytest.raises(ValueError, match=r"2026-10-04T13\.jsonl:1"):
        read(tmp_path)
