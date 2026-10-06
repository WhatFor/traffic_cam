"""Rerun logging of what the pipeline sees, for viewing live or in a replay."""

import numpy as np
import rerun as rr
import supervision as sv

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage, SignalChange, SignalState
from trafficcam.geometry import Observation
from trafficcam.inference import CLASS_NAMES
from trafficcam.pipeline import FrameResult
from trafficcam.sources import Frame

GRPC_PORT = 9876
MEMORY_LIMIT = "512MiB"
JPEG_QUALITY = 75
# Pixels in the logged image between a track's box and the anchor of its id label.
ID_LABEL_OFFSET = 24
SIGNAL_COLOURS = {
    SignalState.red: (255, 0, 0),
    SignalState.red_amber: (255, 120, 0),
    SignalState.green: (0, 220, 0),
    SignalState.amber: (255, 200, 0),
    SignalState.unknown: (128, 128, 128),
}


class RerunSink:
    """Serves a Rerun gRPC server and logs every frame's result and every passage to it.

    Everything is drawn over the camera image, which is smaller than the full frame that
    detections and site geometry are measured in, so coordinates are scaled on the way out.
    """

    def __init__(self, config: SiteConfig, image_size: tuple[int, int]) -> None:
        self._scale = image_size[0] / config.camera.size[0]
        # Just left of each head, so the marker does not cover its lamps.
        self._heads = {
            name: (
                (min(x for x, _, _, _ in head.lamps.values()) - 12) * self._scale,
                float(np.mean([y + h / 2 for _, y, _, h in head.lamps.values()])) * self._scale,
            )
            for name, head in config.signal_heads.items()
        }
        rr.init("trafficcam")
        # Returns immediately; the server lives only as long as this process.
        uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit=MEMORY_LIMIT)
        print(f"Rerun gRPC server at {uri}", flush=True)
        self._log_site(config)

    def _log_site(self, config: SiteConfig) -> None:
        zones = list(config.zones.items())
        rr.log(
            "camera/site/zones",
            rr.LineStrips2D(
                [np.array([*zone.polygon, zone.polygon[0]]) * self._scale for _, zone in zones],
                labels=[name for name, _ in zones],
                class_ids=list(range(len(zones))),
            ),
            static=True,
        )
        rr.log(
            "camera/site/lines",
            rr.LineStrips2D(
                [np.array(line.points) * self._scale for line in config.lines.values()],
                labels=list(config.lines),
                colors=(0, 255, 0),
            ),
            static=True,
        )

    def observe(self, result: FrameResult) -> None:
        self.frame(result.frame)
        self.detections(result.detections, result.inference_ms)
        self.observation(result.observation)
        if result.signals:
            names = list(result.signals)
            rr.log(
                "camera/signals",
                rr.Points2D(
                    [self._heads[name] for name in names],
                    radii=5,
                    colors=[SIGNAL_COLOURS[result.signals[name]] for name in names],
                    labels=[f"{name} {result.signals[name].value}" for name in names],
                ),
            )

    def close(self) -> None:
        pass

    def frame(self, frame: Frame) -> None:
        rr.set_time("frame", sequence=frame.index)
        rr.set_time("capture_time", timestamp=frame.timestamp)
        # A replay of a track log has no pictures.
        if frame.image.size:
            rr.log("camera", rr.Image(frame.image).compress(jpeg_quality=JPEG_QUALITY))

    def detections(self, detections: sv.Detections, elapsed_ms: float) -> None:
        confidence = detections.confidence if detections.confidence is not None else []
        class_id = detections.class_id if detections.class_id is not None else []
        rr.log("stats/inference_ms", rr.Scalars(elapsed_ms))
        rr.log(
            "camera/detections",
            rr.Boxes2D(
                array=detections.xyxy * self._scale,
                array_format=rr.Box2DFormat.XYXY,
                class_ids=class_id,
                labels=[
                    f"{CLASS_NAMES[int(c)].value} {score:.0%}"
                    for c, score in zip(class_id, confidence, strict=True)
                ],
            ),
        )

    def observation(self, observation: Observation) -> None:
        tracks = observation.tracks
        boxes = tracks.xyxy * self._scale
        ids = tracks.tracker_id if tracks.tracker_id is not None else []
        rr.log(
            "camera/tracks",
            rr.Boxes2D(array=boxes, array_format=rr.Box2DFormat.XYXY, class_ids=ids),
        )
        # Rerun draws a box's label under the box, where the detection's label already is.
        # It draws a point's label under the point, so a point above the box puts the id there.
        anchors = np.column_stack([(boxes[:, 0] + boxes[:, 2]) / 2, boxes[:, 1] - ID_LABEL_OFFSET])
        labels = [
            " ".join([f"#{track_id}", *observation.zones_of(index)])
            for index, track_id in enumerate(ids)
        ]
        rr.log("camera/tracks/ids", rr.Points2D(anchors, radii=0, class_ids=ids, labels=labels))
        for crossing in observation.crossings:
            rr.log("events/crossings", rr.TextLog(f"#{crossing.track_id} crossed {crossing.line}"))

    def passage(self, passage: Passage) -> None:
        route = passage.movement or f"{passage.entry_zone or '?'}->{passage.exit_zone or '?'}"
        parts = [f"{passage.class_.value if passage.class_ else '?'} {route}"]
        if passage.stopline is not None:
            parts.append(f"crossed {passage.stopline}")
        parts.append(f"{(passage.last_seen - passage.first_seen).total_seconds():.1f} s")
        rr.log("events/passages", rr.TextLog(f"#{passage.track_id} " + ", ".join(parts)))

    def event(self, event: Event) -> None:
        details = ", ".join(f"{key} {value}" for key, value in event.attrs.items())
        rr.log(
            "events/detections",
            rr.TextLog(f"#{event.track_id} {event.type}: {details}", level=rr.TextLogLevel.WARN),
        )

    def signal(self, change: SignalChange) -> None:
        was = change.from_state.value if change.from_state else "nothing"
        rr.log("events/signals", rr.TextLog(f"{change.head_id}: {was} -> {change.to_state.value}"))
