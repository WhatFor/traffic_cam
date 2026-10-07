"""Incident candidates: what a crash might look like in the tracks. Expect false ones.

Two patterns. The first is a contact and its aftermath: two tracks on crossing paths at
the same spot at nearly the same time, after which one of them stays put. The second is a
vehicle standing for a long time where traffic does not queue. The weights were set so
that ordinary traffic raises few. The one real collision seen so far, on 2026-10-07, was
not caught as a contact; the two cars pulled over on an exit and were caught standing.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage
from trafficcam.detectors.conflicts import Conflict, Conflicts
from trafficcam.detectors.standstill import Spell, Standstills
from trafficcam.geometry import Observation

EVENT_TYPE = "incident_candidate"
DETECTOR_VERSION = "1"

# What each sign adds to a candidate's confidence. A contact is not enough alone, nor with
# a sudden stop: vehicles turning right pass close and then stop to wait, all day long.
# It takes one of the two staying put.
CONTACT = 0.3
SUDDEN_STOP = 0.1
ONE_STANDS = 0.3
BOTH_STAND = 0.5
LONE_STANDSTILL = 0.5

# A sudden stop: moving at the contact, still within this long after it.
MOVING_MPS = 3.0
STILL_MPS = 0.5
STOPS_WITHIN = timedelta(seconds=2)
# A standstill belongs to a contact if it begins this soon after it.
STANDS_WITHIN = timedelta(seconds=5)
# This many other vehicles standing as well is a queue, not an incident.
QUEUE_OF = 2
QUEUED_FOR = timedelta(seconds=5)


@dataclass(slots=True)
class _Contact:
    conflict: Conflict
    sudden_stop: bool = False
    standing: set[int] = field(default_factory=set)


class Incidents:
    """Scores contacts by what follows them, and watches for long standstills."""

    def __init__(self, config: SiteConfig, config_hash: str, conflicts: Conflicts) -> None:
        settings = config.detectors.incident
        ground_map = config.ground_map()
        if settings is None or ground_map is None:
            raise ValueError("detectors.incident is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._conflicts = conflicts
        self._ground_map = ground_map
        self._contact_s = settings.contact_s
        self._standstill_after = timedelta(seconds=settings.standstill_after_s)
        self._lone_standstill = timedelta(seconds=settings.lone_standstill_s)
        self._min_confidence = settings.min_confidence
        self._standing = Standstills(settings.stationary_radius_px)
        # Standing counts in the junction and the exits. An approach is where traffic queues.
        self._flowing = [
            name
            for name, zone in config.zones.items()
            if name == config.junction or zone.role == "exit"
        ]
        self._queueing = [name for name, zone in config.zones.items() if zone.role == "approach"]
        self._contacts: dict[frozenset[int], _Contact] = {}
        self._spells: dict[int, Spell] = {}
        self._reported: set[int] = set()

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        for conflict in self._conflicts.found(observation, timestamp):
            pair = frozenset((conflict.first, conflict.second))
            contact = self._contacts.get(pair)
            if contact is None and conflict.gap_s < self._contact_s:
                contact = self._contacts[pair] = _Contact(conflict)
            # Once followed, a pair is judged where its paths come closest, whatever the gap there.
            if contact is not None and conflict.distance_m < contact.conflict.distance_m:
                contact.conflict = conflict
        self._follow_standing(observation, timestamp)
        events = []
        for pair, contact in list(self._contacts.items()):
            self._follow_contact(contact, timestamp)
            if timestamp - contact.conflict.at > self._standstill_after + STANDS_WITHIN:
                del self._contacts[pair]
                events += self._contact_event(contact)
        return events + self._lone_standstills(timestamp)

    def passage_closed(self, passage: Passage) -> list[Event]:
        if passage.track_id is not None:
            self._standing.take(passage.track_id)
            self._spells.pop(passage.track_id, None)
            self._reported.discard(passage.track_id)
        return []

    def _follow_standing(self, observation: Observation, timestamp: datetime) -> None:
        ids = observation.tracks.tracker_id
        if ids is None or len(ids) == 0:
            self._spells = {}
            return
        flowing = np.any([observation.zones[name] for name in self._flowing], axis=0)
        if self._queueing:
            flowing &= ~np.any([observation.zones[name] for name in self._queueing], axis=0)
        spells = {}
        for track_id, here, (x, y) in zip(
            ids.tolist(), flowing, observation.ground_points, strict=True
        ):
            if here:
                spells[track_id] = self._standing.at(track_id, timestamp, x, y)
            else:
                self._standing.away(track_id)
        self._spells = spells

    def _follow_contact(self, contact: _Contact, timestamp: datetime) -> None:
        conflict = contact.conflict
        since = timestamp - conflict.at
        for track_id, was_mps in (
            (conflict.first, conflict.first_mps),
            (conflict.second, conflict.second_mps),
        ):
            motion = self._conflicts.motions.get(track_id)
            if motion is not None and since <= STOPS_WITHIN:
                if was_mps >= MOVING_MPS and motion.speed <= STILL_MPS:
                    contact.sudden_stop = True
            spell = self._spells.get(track_id)
            if spell is not None and spell.duration >= self._standstill_after:
                if spell.started - conflict.at <= STANDS_WITHIN:
                    contact.standing.add(track_id)

    def _contact_event(self, contact: _Contact) -> list[Event]:
        conflict = contact.conflict
        if conflict.gap_s >= self._contact_s:
            return []
        signs = ["contact"]
        confidence = CONTACT
        if contact.sudden_stop:
            signs.append("sudden_stop")
            confidence += SUDDEN_STOP
        if contact.standing:
            signs.append("standstill")
            confidence += BOTH_STAND if len(contact.standing) == 2 else ONE_STANDS
        if confidence < self._min_confidence:
            return []
        self._reported |= {conflict.first, conflict.second}
        ((pixel_x, pixel_y),) = self._ground_map.to_pixels(np.array([[conflict.x, conflict.y]]))
        attrs = {
            "signs": signs,
            "pet_s": round(conflict.gap_s, 2),
            "tracks": [conflict.first, conflict.second],
            "standing": sorted(contact.standing),
            "ground_m": [round(conflict.x, 1), round(conflict.y, 1)],
            "pixel": [round(float(pixel_x)), round(float(pixel_y))],
        }
        return [self._event(conflict.at, conflict.second, min(confidence, 1.0), attrs)]

    def _lone_standstills(self, timestamp: datetime) -> list[Event]:
        events = []
        for track_id, spell in self._spells.items():
            if track_id in self._reported or spell.duration < self._lone_standstill:
                continue
            others = sum(
                1
                for other_id, other in self._spells.items()
                if other_id != track_id and other.duration >= QUEUED_FOR
            )
            if others >= QUEUE_OF:
                continue
            self._reported.add(track_id)
            attrs = {
                "signs": ["lone_standstill"],
                "standing_s": round(spell.duration.total_seconds()),
                "tracks": [track_id],
                "pixel": [round(spell.x), round(spell.y)],
            }
            if LONE_STANDSTILL >= self._min_confidence:
                events.append(self._event(spell.started, track_id, LONE_STANDSTILL, attrs))
        return events

    def _event(
        self, at: datetime, track_id: int, confidence: float, attrs: dict[str, object]
    ) -> Event:
        name = f"trafficcam/{self._camera}/{EVENT_TYPE}/{at.isoformat()}/{track_id}"
        return Event.model_validate(
            {
                # Derived from what identifies the candidate, so a replay gives the same id.
                "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
                "ts": at,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "type": EVENT_TYPE,
                "detector_version": DETECTOR_VERSION,
                # Raised while the vehicles are still there, before any passage has closed.
                "passage_id": None,
                "track_id": track_id,
                "class": None,
                "confidence": round(confidence, 2),
                "attrs": attrs,
                "clip_id": None,
            }
        )
