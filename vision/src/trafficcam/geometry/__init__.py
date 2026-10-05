"""Geometry: where each track is on the road, relative to the site's zones and lines."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt
import supervision as sv

from trafficcam.config import SiteConfig
from trafficcam.geometry.lines import Crossing, LineCrossings, Points

__all__ = ["Crossing", "Observation", "SceneGeometry", "junction_centre"]


@dataclass(frozen=True, slots=True)
class Observation:
    """One frame's tracks, placed in the scene. Arrays and masks are in track order."""

    tracks: sv.Detections
    ground_points: Points  # full-frame pixels
    zones: dict[str, npt.NDArray[np.bool_]]  # zone name -> which tracks are in it
    crossings: list[Crossing]

    def zones_of(self, index: int) -> list[str]:
        return [name for name, inside in self.zones.items() if inside[index]]


def junction_centre(config: SiteConfig) -> Points:
    return np.mean(np.array(config.zones[config.junction].polygon, dtype=np.float64), axis=0)


class SceneGeometry:
    def __init__(self, config: SiteConfig) -> None:
        self._zones = {
            name: sv.PolygonZone(
                np.array(zone.polygon), triggering_anchors=(sv.Position.BOTTOM_CENTER,)
            )
            for name, zone in config.zones.items()
        }
        centre = junction_centre(config)
        self._lines = [
            LineCrossings(name, line, centre, config.tracking.lost_s)
            for name, line in config.lines.items()
        ]

    def observe(self, tracks: sv.Detections, timestamp: datetime) -> Observation:
        # A vehicle's box includes its roof; the middle of the bottom edge is where it stands.
        ground_points = tracks.get_anchors_coordinates(sv.Position.BOTTOM_CENTER).astype(np.float64)
        ids = tracks.tracker_id if tracks.tracker_id is not None else np.empty(0, dtype=np.int_)
        return Observation(
            tracks=tracks,
            ground_points=ground_points,
            zones={name: zone.trigger(tracks) for name, zone in self._zones.items()},
            crossings=[
                crossing
                for line in self._lines
                for crossing in line.update(ids, ground_points, timestamp)
            ],
        )
