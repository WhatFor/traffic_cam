# 0013. Box junction stops: standing still, and who is exempt

Date: 2026-10-06. Status: Accepted.

## Context

The first detector flags vehicles that stop in the yellow box. The rule has an exception that decides almost everything: a vehicle turning right may wait in the box for oncoming traffic. In 2.8 busy hours of track logs from 2026-10-06, 251 vehicles stood in the box for 3 seconds or more, and all but 3 of those whose movement could be told were right turners.

There is no mapping from pixels to metres yet, so "speed near zero" cannot be measured as a speed.

## Decision

**Standing still.** While a track's ground point is inside the junction zone it has an anchor point. The vehicle is standing for as long as it stays within `stationary_radius_px` (10) of the anchor; moving further starts a new anchor. The longest such spell is what counts, and it must last `min_stationary_s` (3 s). A standing vehicle's ground point wanders by about 4 px, and radii from 6 to 25 px find nearly the same stops.

**Decided when the passage closes**, because the exemption needs the exit. An event is raised only if the passage has both an entry and an exit zone and that pair is not listed in `exempt_movements`.

**An unknown movement raises nothing.** A stop with no entry or no exit is most often a right turner whose track broke. Flagging them would bury the few real events.

**The hidden south arm gets an exit zone.** Traffic bound for the south arm disappears behind the building at the box's south-east corner. `exit_south` is a small polygon there, drawn from where tracks from the west and north arms end. Without it, every right turn from the west arm had no exit, which is 92 of the 251 stops.

**An exit on the entry's own arm does not count.** Traffic coming out of the south arm first appears inside `exit_south`. Without this rule a broken track would be recorded as leaving by the arm it arrived on.

The event is `box_junction_stop`. Its time is when the stop began; its attributes hold the duration, the zone, the movement and where the vehicle stood. Its id is derived from the passage id.

## Consequences

- On the same three hours the rules give 3 events, about one an hour, with 221 stops exempt and 27 undecided. Passages with a complete movement rise from 88% to 94% of those through the box.
- Recall depends on tracking: a real offender whose track breaks is one of the undecided and is missed.
- Precision depends on the zones. A right turner recorded with the wrong exit would be flagged. None was in the three hours.
- The radius is in pixels, so the same number is a larger distance on the far side of the box than on the near side.
- None of this has been checked against video. The plan's acceptance needs 30 minutes of labelled footage.
- A slow creep through a queue in the box is not a stop under this rule, however long it takes.
