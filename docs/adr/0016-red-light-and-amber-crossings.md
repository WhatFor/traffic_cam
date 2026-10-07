# 0016. Red-light and amber crossings

Date: 2026-10-06. Status: Accepted.

## Context

With a state for each stop line (0015), a crossing on red can be flagged. Three things make the plain rule, "the tracked point crossed the line while it was red", wrong here:

- Vehicles often stop a little past the line and wait there for green. In the first hour of running, one such car was flagged after waiting 48 s.
- West-arm traffic drives away from the camera. The tracked point is the bottom centre of the box, which is the vehicle's rear, so it crosses the line after the front has.
- The south head's red is inferred, not seen (0015).

## Decision

**Decided when the passage closes**, from the passage's stop-line crossing and the signal history.

**The signal is read at the moment the front crossed.** A line may have `lag_s` in `site.yaml`; the crossing time less that is taken as when the front crossed. The two west lines have 0.7 s.

**The vehicle must have gone on.** A crossing counts only if the track was later seen in a zone that is not an approach (the box, or an exit), and the line's signal at that moment had still not released it.

- `red_light`: the line was red when crossed, had been red for more than `grace_s` (0.5 s), and was still red when the vehicle went on.
- `amber_crossing`: the line was amber when crossed, and amber or red when the vehicle went on. `amber_events: false` turns these off.

**Only an observed state counts.** A line whose heads are `unknown`, disagree, or give an inferred red raises nothing.

**Red-and-amber raises nothing.** It is recorded on the passage like any other state.

The event's time is when the tracked point crossed. Its attributes hold the line, the movement and the time into red or amber. Its id is derived from the passage id.

**The forbidden left turn (0014) carries the signals.** `banned_turns.signal_heads` lists the south head and the pedestrian crossing. Each `banned_turn` event records what those heads showed when the vehicle was first seen and for how long each was green while it was in view. Nothing is decided from them yet.

## Consequences

- In two hours after dark on 2026-10-06, 1,352 crossings of the two west lines gave 3 `red_light` and 40 `amber_crossing` events. 19 passages crossed on red: of the 16 that raised nothing, 3 were inside the grace period, and most of the rest stayed in view for 20 to 60 s afterwards, waiting past the line.
- The three flagged tracks were looked at in the track log. Two crossed the slip-lane line at speed about 1 s into red. The third stood at the ahead line through amber and went through the box 3 s into red. None has been checked against video.
- A vehicle that crosses on red and whose track ends before the box is missed.
- A vehicle that waits past the line and sets off early, while the signal is still red, is flagged. That is a red-light offence, but its "time into red" describes when it first crossed the line, not when it set off.
- `lag_s` is one number for a line. It is right for a vehicle of ordinary length at ordinary speed, and too short for a long or slow one, which is then read slightly late. 0.7 s is an estimate that has not been measured against video.
- There is no red-light detection for the north arm (no head) or the south arm (no stop line in view, and an inferred red). Since 0022 a north-arm passage records the state inferred for its crossing, and so does a west-line passage whose heads could not be read; neither raises an event.
- An amber crossing is not an offence when the vehicle could not safely stop. These events are counts of behaviour, not of offences.
