"""Outputs: opens the sinks and observers that the command line asks for."""

import argparse
import contextlib
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trafficcam.clips import ClipRecorder, PacketRing
from trafficcam.clips.recorder import RingBufferRecorder
from trafficcam.clips.writer import VideoStream
from trafficcam.config import SiteConfig
from trafficcam.health.metrics import Metrics
from trafficcam.health.watchdog import Watchdog
from trafficcam.pipeline import FrameObserver, send_clips
from trafficcam.sinks import EventSink
from trafficcam.sinks.jsonl import JsonlSink
from trafficcam.sinks.mqtt import MqttSink
from trafficcam.tracklog.writer import TrackLogWriter

if TYPE_CHECKING:
    from trafficcam.debug.rerun_sink import RerunSink


def open_mqtt(config: SiteConfig, recorder: ClipRecorder | None) -> MqttSink:
    password = os.environ.get("MQTT_PASSWORD")
    if not password:
        print("MQTT_PASSWORD is not set", file=sys.stderr)
        sys.exit(2)
    return MqttSink(
        host=os.environ.get("MQTT_HOST", "127.0.0.1"),
        port=int(os.environ.get("MQTT_PORT", "1883")),
        password=password,
        camera=config.camera.id,
        on_clip_command=None if recorder is None else recorder.command,
        on_clip_keep=None if recorder is None else recorder.keep,
    )


@dataclass(frozen=True, slots=True)
class Outputs:
    sinks: list[EventSink]
    observers: list[FrameObserver]
    rerun: "RerunSink | None"
    recorder: ClipRecorder | None


def open_outputs(
    stack: contextlib.ExitStack,
    config: SiteConfig,
    config_hash: str,
    args: argparse.Namespace,
    image_size: tuple[int, int],
    watchdog: Watchdog,
    ring: PacketRing | None = None,
) -> Outputs:
    """Open everything that receives results; `stack` closes each of them on exit.

    With a `ring` of the camera's encoded video, clips are recorded from it.
    """
    live = args.video is None and args.tracks is None
    sinks: list[EventSink] = []
    observers: list[FrameObserver] = []
    rerun = None
    metrics = None
    if args.metrics_port is not None:
        metrics = Metrics()
        metrics.serve(args.metrics_port)
        stack.callback(metrics.close)

    if args.jsonl is not None:
        sinks.append(JsonlSink(args.jsonl))
        stack.callback(sinks[-1].close)
    # A replay must never reach the broker: its passages would be stored as if they were new.
    recorder = None
    if ring is not None:
        width, height = config.camera.size
        recorder = RingBufferRecorder(
            ring,
            config.clips,
            VideoStream(width, height, config.camera.fps),
            camera=config.camera.id,
            config_hash=config_hash,
        )
        if metrics is not None:
            metrics.watch_dropped("clips", lambda: recorder.failed)
            metrics.watch_clips_folder(lambda: recorder.folder_bytes)
    if live:
        mqtt = open_mqtt(config, recorder)
        stack.callback(mqtt.close)
        sinks.append(mqtt)
        if metrics is not None:
            metrics.watch_mqtt(lambda: mqtt.connected)
            metrics.watch_dropped("mqtt", lambda: mqtt.dropped)
    if args.debug_rerun:
        # Imported here so the Rerun SDK is only loaded when it is used.
        from trafficcam.debug.rerun_sink import RerunSink

        rerun = RerunSink(config, image_size)
        sinks.append(rerun)
        observers.append(rerun)
    if args.record_tracks is not None:
        writer = TrackLogWriter(
            args.record_tracks,
            camera=config.camera.id,
            config_hash=config_hash,
            wait_when_full=not live,
        )
        stack.callback(writer.close)
        observers.append(writer)
        if metrics is not None:
            metrics.watch_dropped("tracklog", lambda: writer.dropped)
    if metrics is not None:
        sinks.append(metrics)
        observers.append(metrics)
    # Last, so a ping means a frame went through everything above.
    observers.append(watchdog)
    if recorder is not None:
        # Registered last, so it runs first: a clip cut short by the shutdown is still
        # finished and announced while the sinks are open.
        def finish_clips() -> None:
            recorder.close()
            send_clips(recorder, sinks)

        stack.callback(finish_clips)
    return Outputs(sinks, observers, rerun, recorder)
