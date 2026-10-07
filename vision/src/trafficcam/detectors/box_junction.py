"""Box junction stops: a vehicle standing in the box that was not waiting to turn right."""

import uuid
from datetime import datetime, timedelta

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage
from trafficcam.detectors.standstill import Standstills
from trafficcam.geometry import Observation

EVENT_TYPE = "box_junction_stop"
DETECTOR_VERSION = "1"


class BoxJunctionStops:
    """Follows how long each track stands still inside the junction zone.

    The decision waits for the track's passage, because whether a stop is allowed depends
    on where the vehicle went: one turning right may wait in the box for oncoming traffic.
    A passage without both an entry and an exit raises nothing, since it cannot be told
    from a right turn.
    """

    def __init__(self, config: SiteConfig, config_hash: str) -> None:
        settings = config.detectors.box_junction
        if settings is None:
            raise ValueError("detectors.box_junction is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._zone = config.junction
        self._standing = Standstills(settings.stationary_radius_px)
        self._minimum = timedelta(seconds=settings.min_stationary_s)
        self._exempt = {
            (config.movements[name].from_, config.movements[name].to)
            for name in settings.exempt_movements
        }

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        ids = observation.tracks.tracker_id
        if ids is None:
            return []
        inside = observation.zones[self._zone]
        for track_id, is_inside, (x, y) in zip(
            ids.tolist(), inside, observation.ground_points, strict=True
        ):
            if is_inside:
                self._standing.at(track_id, timestamp, x, y)
            else:
                self._standing.away(track_id)
        return []

    def passage_closed(self, passage: Passage) -> list[Event]:
        stop = self._standing.take(passage.track_id) if passage.track_id is not None else None
        if stop is None or stop.duration < self._minimum:
            return []
        if passage.entry_zone is None or passage.exit_zone is None:
            return []
        if (passage.entry_zone, passage.exit_zone) in self._exempt:
            return []
        return [
            Event.model_validate(
                {
                    # Derived from the passage, so a replay gives the same id.
                    "id": uuid.uuid5(passage.id, EVENT_TYPE),
                    "ts": stop.started,
                    "camera": self._camera,
                    "config_hash": self._config_hash,
                    "type": EVENT_TYPE,
                    "detector_version": DETECTOR_VERSION,
                    "passage_id": passage.id,
                    "track_id": passage.track_id,
                    "class": passage.class_,
                    "confidence": None,
                    "attrs": {
                        "stationary_s": round(stop.duration.total_seconds(), 2),
                        "zone": self._zone,
                        "movement": passage.movement,
                        "x": round(stop.x),
                        "y": round(stop.y),
                    },
                    "clip_id": None,
                }
            )
        ]
