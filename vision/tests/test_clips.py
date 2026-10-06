"""Clips are cut from the ring of recent video around each trigger, and kept for a while."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image
from test_pipeline import an_event

from trafficcam.clips import NullRecorder, PacketRing
from trafficcam.clips.plan import ClipPlan
from trafficcam.clips.recorder import RingBufferRecorder
from trafficcam.clips.retention import prune
from trafficcam.clips.writer import VideoStream
from trafficcam.config import Clips
from trafficcam.contracts import Clip, ClipCommand, ClipDeleted, ClipTrigger, Event

FPS = 10
WIDTH, HEIGHT = 64, 48
SECONDS = 20
ORIGIN = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
NOW = ORIGIN + timedelta(seconds=SECONDS)
BASE_LEVEL = 20  # frame n is a flat grey of this plus n, so a still says which frame it is

Encoded = list[tuple[bytes, bool, int]]


def at(seconds: float) -> datetime:
    return ORIGIN + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def encoded() -> Encoded:
    """Twenty seconds of H.264 as the camera's encoder gives it: a keyframe every second."""
    packets = []
    with av.open(os.devnull, "w", format="null") as container:
        stream = container.add_stream("libx264", rate=FPS)
        assert isinstance(stream, av.VideoStream)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.codec_context.gop_size = FPS
        stream.codec_context.time_base = Fraction(1, 1_000_000)
        stream.codec_context.options = {"preset": "ultrafast", "tune": "zerolatency"}
        for index in range(SECONDS * FPS):
            image = np.full((HEIGHT, WIDTH, 3), BASE_LEVEL + index, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = index * 1_000_000 // FPS
            for packet in stream.encode(frame):
                assert packet.pts is not None
                packets.append((bytes(packet), packet.is_keyframe, packet.pts))
    return packets


def fill(ring: PacketRing, encoded: Encoded, start_s: float = 0, end_s: float = SECONDS) -> None:
    for data, keyframe, pts_us in encoded:
        if start_s * 1_000_000 <= pts_us < end_s * 1_000_000:
            ring.append(data, keyframe, pts_us)


def a_ring(encoded: Encoded, *, held_s: float = 90, end_s: float = SECONDS) -> PacketRing:
    ring = PacketRing(held_s)
    ring.set_origin(ORIGIN)
    fill(ring, encoded, end_s=end_s)
    return ring


def settings(directory: Path) -> Clips:
    return Clips.model_validate(
        {
            "dir": directory,
            "pre_s": 5,
            "post_s": 6,
            "buffer_s": 90,
            "max_s": 12,
            "retention_days": 30,
            "max_gb": 1,
            "events": {
                "red_light": {"pre_s": 5, "post_s": 3},
                "speeding": {"pre_s": 2, "post_s": 2, "min": {"speed_mph": 45}},
            },
        }
    )


def a_recorder(ring: PacketRing, directory: Path) -> RingBufferRecorder:
    return RingBufferRecorder(
        ring,
        settings(directory),
        VideoStream(WIDTH, HEIGHT, FPS),
        camera="junction-1",
        config_hash="sha256:test",
        run_thread=False,
        now=lambda: NOW,
    )


def red_light(seconds: float) -> Event:
    return an_event("red_light").model_copy(update={"id": uuid.uuid4(), "ts": at(seconds)})


def only_clip(recorder: RingBufferRecorder) -> Clip:
    (clip,) = recorder.drain()
    assert isinstance(clip, Clip)
    return clip


def frame_levels(path: Path) -> list[int]:
    levels = []
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            levels.append(round(float(frame.to_ndarray(format="gray").mean())))
    return levels


def test_the_ring_keeps_only_its_span(encoded: Encoded) -> None:
    ring = a_ring(encoded, held_s=5)

    held = ring.from_keyframe(0)

    assert held[0].keyframe
    assert held[0].pts_us == 15_000_000
    assert held[-1].pts_us == 19_900_000
    assert ring.after(held[-3].seq) == held[-2:]


def test_the_ring_starts_a_clip_on_the_keyframe_before_the_moment(encoded: Encoded) -> None:
    ring = a_ring(encoded)

    assert ring.pts_of(at(7.5)) == 7_500_000
    assert ring.from_keyframe(7_500_000)[0].pts_us == 7_000_000
    assert ring.from_keyframe(7_000_000)[0].pts_us == 7_000_000
    assert ring.time_of(7_000_000) == at(7)
    assert PacketRing(5).pts_of(at(1)) is None


def trigger(seconds: float, kind: str = "red_light") -> ClipTrigger:
    return ClipTrigger(type=kind, at=at(seconds), event_id=None, reason=None)


def test_overlapping_triggers_share_a_clip_and_extend_it() -> None:
    plan = ClipPlan(max_s=60)

    first = plan.add(trigger(10), 5, 15)
    second = plan.add(trigger(22, "banned_turn"), 5, 15)

    assert second is first
    assert (first.start, first.end) == (at(5), at(37))
    assert [each.type for each in first.triggers] == ["red_light", "banned_turn"]


def test_a_trigger_joins_only_a_clip_that_gives_it_its_lead_in() -> None:
    plan = ClipPlan(max_s=60)
    clip = plan.add(trigger(10), 5, 15)

    # Raised later but about an earlier moment: the open clip starts too late for it.
    earlier = plan.add(trigger(8), 5, 15)
    apart = plan.add(trigger(40), 5, 15)

    assert len({clip.id, earlier.id, apart.id}) == 3
    assert (earlier.start, earlier.end) == (at(3), at(23))


def test_a_clip_stops_growing_at_its_longest() -> None:
    plan = ClipPlan(max_s=30)
    clip = plan.add(trigger(10), 5, 15)

    assert plan.add(trigger(24), 5, 15) is clip
    assert clip.end == at(35)
    assert plan.add(trigger(36), 5, 15) is not clip

    plan.close(clip)
    assert plan.add(trigger(12), 5, 15) is not clip


def test_an_event_gets_a_clip_around_its_moment(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded), tmp_path)
    event = red_light(10.5)

    published = recorder.for_event(event)
    recorder.step()
    clip = only_clip(recorder)

    assert published.clip_id == clip.id
    assert published.model_copy(update={"clip_id": None}) == event
    path = tmp_path / "2026" / "10" / "06" / f"{clip.id}.mp4"
    assert clip.path == str(path)
    assert clip.bytes == path.stat().st_size
    # From the keyframe at or before five seconds ahead of the event, to three seconds after it.
    assert (clip.started_at, clip.ended_at) == (at(5), at(13.5))
    levels = frame_levels(path)
    assert len(levels) == 86
    assert abs(levels[0] - (BASE_LEVEL + 50)) <= 2
    assert not list(tmp_path.rglob("*.part"))

    assert clip.event_id == event.id
    assert clip.triggers == [
        ClipTrigger(type="red_light", at=at(10.5), event_id=event.id, reason=None)
    ]
    assert Clip.model_validate_json(path.with_suffix(".json").read_text()) == clip


def test_the_still_frame_is_the_moment_of_the_event(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded), tmp_path)

    recorder.for_event(red_light(10.5))
    recorder.step()
    clip = only_clip(recorder)

    assert clip.keyframe_path is not None
    assert clip.keyframe_path == clip.path.replace(".mp4", ".jpg")
    with Image.open(clip.keyframe_path) as still:
        assert still.size == (WIDTH, HEIGHT)
        level = float(np.asarray(still.convert("L")).mean())
    assert abs(level - (BASE_LEVEL + 105)) <= 2


def test_a_clip_is_written_as_its_video_arrives(encoded: Encoded, tmp_path: Path) -> None:
    ring = a_ring(encoded, end_s=12)
    recorder = a_recorder(ring, tmp_path)

    recorder.for_event(red_light(10.5))
    recorder.step()

    assert recorder.drain() == []
    assert len(list(tmp_path.rglob("*.mp4.part"))) == 1

    fill(ring, encoded, start_s=12)
    recorder.step()

    assert only_clip(recorder).ended_at == at(13.5)


def test_a_second_event_during_a_clip_extends_it(encoded: Encoded, tmp_path: Path) -> None:
    ring = a_ring(encoded, end_s=9)
    recorder = a_recorder(ring, tmp_path)

    first = recorder.for_event(red_light(7))
    recorder.step()
    second = recorder.for_event(red_light(9))
    fill(ring, encoded, start_s=9)
    recorder.step()
    clip = only_clip(recorder)

    assert first.clip_id == second.clip_id == clip.id
    assert (clip.started_at, clip.ended_at) == (at(2), at(12))
    assert [each.at for each in clip.triggers] == [at(7), at(9)]
    assert clip.event_id == first.id


def test_a_command_gets_a_clip_of_the_default_length(encoded: Encoded, tmp_path: Path) -> None:
    ring = a_ring(encoded, end_s=14)
    recorder = a_recorder(ring, tmp_path)
    recorder._now = lambda: at(12)  # noqa: SLF001

    command = {"id": uuid.uuid4(), "ts": at(0), "camera": "junction-1", "reason": "to see"}
    recorder.command(ClipCommand.model_validate(command))
    fill(ring, encoded, start_s=14)
    recorder.step()
    clip = only_clip(recorder)

    assert (clip.started_at, clip.ended_at) == (at(7), at(18))
    assert clip.event_id is None
    assert clip.triggers == [ClipTrigger(type="manual", at=at(12), event_id=None, reason="to see")]


def test_an_event_of_another_type_gets_no_clip(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded), tmp_path)
    event = an_event("amber_crossing")

    assert recorder.for_event(event) is event
    recorder.step()

    assert recorder.drain() == []
    assert NullRecorder().for_event(event) is event


def test_an_event_below_its_types_threshold_gets_no_clip(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded), tmp_path)
    speeding = an_event("speeding").model_copy(update={"ts": at(10)})
    over = speeding.model_copy(update={"attrs": {"speed_mph": 38.0}})
    far_over = speeding.model_copy(update={"id": uuid.uuid4(), "attrs": {"speed_mph": 47.5}})

    assert recorder.for_event(over) is over
    assert recorder.for_event(far_over).clip_id is not None
    recorder.step()

    assert only_clip(recorder).triggers[0].type == "speeding"


def test_a_clip_is_dropped_and_counted_without_its_directory(
    encoded: Encoded, tmp_path: Path
) -> None:
    recorder = a_recorder(a_ring(encoded), tmp_path / "unmounted")

    recorder.for_event(red_light(10.5))
    recorder.step()
    recorder.step()

    assert recorder.drain() == []
    assert recorder.failed == 1
    assert not (tmp_path / "unmounted").exists()


def test_an_event_older_than_the_ring_gets_what_is_left(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded, held_s=5), tmp_path)

    recorder.for_event(red_light(13))
    recorder.for_event(red_light(4))
    recorder.step()
    clip = only_clip(recorder)

    assert (clip.started_at, clip.ended_at) == (at(15), at(16))
    assert recorder.failed == 1
    assert len(list(tmp_path.rglob("*.mp4"))) == 1


def test_shutting_down_finishes_a_clip_with_what_it_has(encoded: Encoded, tmp_path: Path) -> None:
    recorder = a_recorder(a_ring(encoded, end_s=12), tmp_path)

    recorder.for_event(red_light(10.5))
    recorder.step()
    recorder.finish_open()

    assert only_clip(recorder).ended_at == at(11.9)


def a_stored_clip(directory: Path, age_days: float, megabytes: int) -> uuid.UUID:
    clip_id = uuid.uuid4()
    directory.mkdir(parents=True, exist_ok=True)
    modified = (NOW - timedelta(days=age_days)).timestamp()
    for suffix, size in ((".mp4", megabytes * 1_000_000), (".jpg", 1000), (".json", 100)):
        path = directory / f"{clip_id}{suffix}"
        path.write_bytes(b"\0" * size)
        os.utime(path, (modified, modified))
    return clip_id


def test_clips_past_their_retention_are_deleted(tmp_path: Path) -> None:
    old = a_stored_clip(tmp_path / "2026" / "09" / "01", age_days=35, megabytes=1)
    kept = a_stored_clip(tmp_path / "2026" / "10" / "05", age_days=1, megabytes=1)
    stray = tmp_path / "2026" / "10" / "05" / "notes.txt"
    stray.write_text("not a clip")

    pruned = prune(tmp_path, NOW, timedelta(days=30), 10_000_000)

    assert pruned.deleted == [old]
    assert pruned.remaining_bytes == 1_001_100
    assert not (tmp_path / "2026" / "09").exists()
    assert sorted(path.name for path in (tmp_path / "2026" / "10" / "05").iterdir()) == sorted(
        [f"{kept}.mp4", f"{kept}.jpg", f"{kept}.json", "notes.txt"]
    )


def test_the_oldest_clips_go_first_when_the_folder_is_too_big(tmp_path: Path) -> None:
    day = tmp_path / "2026" / "10" / "05"
    oldest = a_stored_clip(day, age_days=3, megabytes=4)
    middle = a_stored_clip(day, age_days=2, megabytes=4)
    newest = a_stored_clip(day, age_days=1, megabytes=4)

    pruned = prune(tmp_path, NOW, timedelta(days=30), 9_000_000)

    assert pruned.deleted == [oldest]
    assert pruned.remaining_bytes == 8_002_200
    assert {path.stem for path in day.iterdir()} == {str(middle), str(newest)}


def test_part_written_files_are_cleared_only_when_asked(tmp_path: Path) -> None:
    day = tmp_path / "2026" / "10" / "05"
    day.mkdir(parents=True)
    part = day / f"{uuid.uuid4()}.mp4.part"
    part.write_bytes(b"\0")

    prune(tmp_path, NOW, timedelta(days=30), 1_000_000)
    assert part.exists()

    prune(tmp_path, NOW, timedelta(days=30), 1_000_000, unfinished=True)
    assert not tmp_path.joinpath("2026").exists()


def test_the_recorder_announces_what_retention_deletes(encoded: Encoded, tmp_path: Path) -> None:
    old = a_stored_clip(tmp_path / "2026" / "09" / "01", age_days=35, megabytes=1)
    recorder = a_recorder(a_ring(encoded), tmp_path)

    recorder.step()

    (deleted,) = recorder.drain()
    assert deleted == ClipDeleted.model_validate({"id": old, "ts": NOW, "camera": "junction-1"})
    assert recorder.folder_bytes == 0
