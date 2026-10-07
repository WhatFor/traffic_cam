"""Prometheus metrics for the vision service, derived from what the pipeline produces."""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    start_http_server,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from trafficcam.contracts import (
    Clip,
    ClipDeleted,
    Event,
    GroupState,
    Passage,
    SignalChange,
    SignalState,
)
from trafficcam.health.image_quality import measure
from trafficcam.pipeline import FrameResult

NAMESPACE = "trafficcam"
SUBSYSTEM = "vision"
ADDRESS = "127.0.0.1"
IMAGE_QUALITY_INTERVAL = timedelta(seconds=10)
# The camera runs at 15 fps, so a healthy interval is 0.067 s.
FRAME_INTERVAL_BUCKETS = (0.05, 0.06, 0.07, 0.08, 0.1, 0.15, 0.25, 0.5, 1.0, 2.5)
INFERENCE_BUCKETS = (0.008, 0.01, 0.012, 0.015, 0.02, 0.03, 0.05, 0.1, 0.25)


class _Dropped(Collector):
    """Drop counts kept by the queues themselves, read when scraped."""

    def __init__(self) -> None:
        self.counts: dict[str, Callable[[], int]] = {}

    def collect(self) -> Iterator[CounterMetricFamily]:
        family = CounterMetricFamily(
            f"{NAMESPACE}_{SUBSYSTEM}_dropped",
            "Records dropped because a queue was full or could not be written.",
            labels=["queue"],
        )
        for queue, count in self.counts.items():
            family.add_metric([queue], count())
        yield family


class _PlanViolations(Collector):
    """Signal changes that came outside the plan of the signals, counted by the estimator."""

    def __init__(self) -> None:
        self.count: Callable[[], int] | None = None

    def collect(self) -> Iterator[CounterMetricFamily]:
        family = CounterMetricFamily(
            f"{NAMESPACE}_{SUBSYSTEM}_signal_plan_violations",
            "Signal changes seen outside a gap the plan of the signals fixes.",
        )
        if self.count is not None:
            family.add_metric([], self.count())
        yield family


class _DoubtedHeads(Collector):
    """Which signal heads are not believed where the plan of the signals rules them out."""

    def __init__(self) -> None:
        self.heads: list[str] = []
        self.doubted: Callable[[], frozenset[str]] = frozenset

    def collect(self) -> Iterator[GaugeMetricFamily]:
        family = GaugeMetricFamily(
            f"{NAMESPACE}_{SUBSYSTEM}_signal_head_doubted",
            "1 while a head's readings keep contradicting the plan of the signals.",
            labels=["head"],
        )
        doubted = self.doubted()
        for head in self.heads:
            family.add_metric([head], float(head in doubted))
        yield family


class Metrics:
    """Counts what goes through the pipeline. Observes frames and receives passages."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        names = {"namespace": NAMESPACE, "subsystem": SUBSYSTEM, "registry": self.registry}
        ProcessCollector(registry=self.registry)
        self._dropped = _Dropped()
        self.registry.register(self._dropped)
        self._plan_violations = _PlanViolations()
        self.registry.register(self._plan_violations)
        self._doubted_heads = _DoubtedHeads()
        self.registry.register(self._doubted_heads)
        self._frames = Counter("frames", "Frames processed.", **names)
        self._frame_interval = Histogram(
            "frame_interval_seconds",
            "Time between the sensor timestamps of consecutive frames.",
            buckets=FRAME_INTERVAL_BUCKETS,
            **names,
        )
        self._inference = Histogram(
            "inference_seconds",
            "Time spent detecting objects in one frame.",
            buckets=INFERENCE_BUCKETS,
            **names,
        )
        self._detections = Gauge("detections", "Detections in the latest frame.", **names)
        self._tracks = Gauge("active_tracks", "Confirmed tracks in the latest frame.", **names)
        self._passages = Counter(
            "passages", "Passages closed, by whether they have a movement.", ["complete"], **names
        )
        for complete in ("true", "false"):
            self._passages.labels(complete=complete)
        self._last_passage = Gauge(
            "last_passage_timestamp_seconds", "When the latest passage closed.", **names
        )
        self._events = Counter("events", "Detector events raised, by type.", ["type"], **names)
        self._last_event = Gauge(
            "last_event_timestamp_seconds",
            "When the latest event of a type was raised.",
            ["type"],
            **names,
        )
        self._signal_changes = Counter(
            "signal_changes", "Changes of state seen on a signal head.", ["head"], **names
        )
        self._signal_unknown = Gauge(
            "signal_unknown", "1 while a signal head's state is unknown, else 0.", ["head"], **names
        )
        self._clips = Counter("clips", "Clips written.", **names)
        self._clips_deleted = Counter("clips_deleted", "Clips deleted by retention.", **names)
        self._clips_bytes = Gauge("clips_bytes", "Size of the clips folder.", **names)
        self._mqtt_connected = Gauge(
            "mqtt_connected", "1 if connected to the MQTT broker, else 0.", **names
        )
        self._brightness = Gauge(
            "image_brightness", "Mean grey level of the camera image, 0 to 255.", **names
        )
        self._sharpness = Gauge(
            "image_sharpness", "Variance of the Laplacian of the camera image.", **names
        )
        self._last_frame_at: datetime | None = None
        self._measured_at: datetime | None = None
        self._server = None

    def watch_dropped(self, queue: str, count: Callable[[], int]) -> None:
        self._dropped.counts[queue] = count

    def watch_signal_plan(self, violations: Callable[[], int]) -> None:
        self._plan_violations.count = violations

    def watch_doubted_heads(self, heads: list[str], doubted: Callable[[], frozenset[str]]) -> None:
        self._doubted_heads.heads = heads
        self._doubted_heads.doubted = doubted

    def watch_clips_folder(self, size: Callable[[], int]) -> None:
        self._clips_bytes.set_function(lambda: float(size()))

    def watch_mqtt(self, connected: Callable[[], bool]) -> None:
        self._mqtt_connected.set_function(lambda: float(connected()))

    def serve(self, port: int) -> None:
        """Answer scrapes on localhost, from a thread of its own."""
        self._server, _ = start_http_server(port, addr=ADDRESS, registry=self.registry)

    def observe(self, result: FrameResult) -> None:
        frame = result.frame
        self._frames.inc()
        if self._last_frame_at is not None:
            self._frame_interval.observe((frame.timestamp - self._last_frame_at).total_seconds())
        self._last_frame_at = frame.timestamp
        self._inference.observe(result.inference_ms / 1000)
        self._detections.set(len(result.detections))
        for head, state in result.signals.items():
            self._signal_unknown.labels(head=head).set(state == SignalState.unknown)
        self._tracks.set(len(result.observation.tracks))
        due = self._measured_at is None or (
            frame.timestamp - self._measured_at >= IMAGE_QUALITY_INTERVAL
        )
        # A replay of a track log has no pictures.
        if due and frame.image.size:
            self._measured_at = frame.timestamp
            quality = measure(frame.image)
            self._brightness.set(quality.brightness)
            self._sharpness.set(quality.sharpness)

    def passage(self, passage: Passage) -> None:
        self._passages.labels(complete=str(passage.movement is not None).lower()).inc()
        self._last_passage.set(passage.ts.timestamp())

    def event(self, event: Event) -> None:
        self._events.labels(type=event.type).inc()
        self._last_event.labels(type=event.type).set_to_current_time()

    def signal(self, change: SignalChange) -> None:
        self._signal_changes.labels(head=change.head_id).inc()

    def group_state(self, state: GroupState) -> None:
        pass

    def clip(self, clip: Clip) -> None:
        self._clips.inc()

    def clip_deleted(self, deleted: ClipDeleted) -> None:
        self._clips_deleted.inc()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
