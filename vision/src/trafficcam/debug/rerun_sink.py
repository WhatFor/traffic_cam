"""Rerun logging of what the pipeline sees, for viewing live or in a replay."""

import numpy as np
import rerun as rr
import supervision as sv

from trafficcam.config import SiteConfig
from trafficcam.geometry import Observation
from trafficcam.inference import COCO_CLASS_IDS
from trafficcam.sources import Frame

GRPC_PORT = 9876
MEMORY_LIMIT = "512MiB"
JPEG_QUALITY = 75
# Pixels in the logged image between a track's box and the anchor of its id label.
ID_LABEL_OFFSET = 24

CLASS_NAMES = {class_id: name.value for name, class_id in COCO_CLASS_IDS.items()}


class RerunSink:
    """Serves a Rerun gRPC server and logs to it.

    Everything is drawn over the camera image, which is smaller than the full frame that
    detections and site geometry are measured in, so coordinates are scaled on the way out.
    """

    def __init__(self, config: SiteConfig, image_size: tuple[int, int]) -> None:
        self._scale = image_size[0] / config.camera.size[0]
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

    def frame(self, frame: Frame) -> None:
        rr.set_time("frame", sequence=frame.index)
        rr.set_time("capture_time", timestamp=frame.timestamp)
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
                    f"{CLASS_NAMES[int(c)]} {score:.0%}"
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
