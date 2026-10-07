# 0020. Near misses and incident candidates

Date: 2026-10-07. Status: Accepted. One real collision has been seen; see the consequences.

## Context

Two detectors from the build plan's last phase: near misses, by post-encroachment time, and candidates for a crash. Both need distances, which the ground map gives inside the calibrated area (0019). Neither has ground truth: no crash has been recorded, and nobody has labelled a near miss. They were shaped by replaying 17 hours of recorded tracks (2026-10-06 15:00 to 2026-10-07 08:00 UTC, 30,384 passages) and drawing what they raised.

## Decision

**A conflict is two tracks on crossing paths at the same spot.** Each moving track leaves a trail of where it has just been. When another track comes within 1.5 m of a point on that trail, with a heading at least `min_angle_deg` (60) different and both going at least `min_speed_mph` (8), the pair is in conflict. The gap is the time between the two at that point.

**The gap is taken where the two paths come closest**, whatever it is there. Taking the smallest gap anywhere within reach counted pairs whose paths really met later: the first version raised 62 near misses where this one raises 3 at the same limit.

**Only a track that is plainly a moving vehicle counts.** This is most of the detector. Without it nearly every close conflict was a tracking fault:

- A vehicle waiting to turn has its box stretched by each vehicle passing in front of it. Its ground point jumps onto the other's path and back within a second, and the two seem to be in one place at one time. So a track must have been going the same way, within 60 degrees, over the last 1.5 s as over the last 0.5 s.
- One vehicle is sometimes given two tracks. So two tracks that began within a second and 150 px of each other are never a pair, and a track's positions must lie within 0.5 m of a straight line.
- A track must have been followed for a second and have moved 3 m.

**A near miss** is a conflict with a gap under `pet_max_s`. The event waits until both passages have closed, so that it names both movements; two vehicles that came in on the same arm raise nothing. It records the gap, the angle, both speeds, both movements and the spot.

**The limit is 2 s between points, with a clip under 1.5 s.** A vehicle is one point here, the bottom centre of its box. The usual road-safety limit of 1.5 s is between bodies, and a body takes about half a second to pass a point at these speeds. The numbers first agreed, 1.5 s and 1 s, were chosen on rates that the first version had overstated; at those limits there would be about 4 events a day and no clips to check them by.

**An incident candidate** is one of two patterns, each with a confidence:

| Sign | Adds |
| --- | --- |
| Contact: a conflict with a gap under `contact_s` (1 s) | 0.3 |
| Either vehicle stops dead within 2 s | 0.1 |
| One of them then stands near the spot for `standstill_after_s` (20 s) | 0.3 |
| Both do | 0.5 in place of 0.3 |
| A lone standstill of `lone_standstill_s` (90 s) in the junction or an exit, with fewer than two others standing | 0.5 |

An event is raised at `min_confidence` (0.5), so a contact needs a standstill after it. Approach zones do not count for standing: that is where traffic queues. 90 s is a whole signal cycle. `notify_min_confidence` (0.7) is for the notifier, which does not exist yet.

**The build plan's deceleration threshold is gone.** At 6 m/s² over half a second it fired on 146 tracks an hour, from boxes changing shape. A stop is used only after a contact, and adds little.

**Both use shared parts.** The ground map moved to the top of `site.yaml` and every observation carries positions in metres. Velocity fitting is one module, used by the speed meter too; standstill tracking is one module, used by the box-junction detector too. The two detectors share one conflict finder.

## Consequences

- On the 17 hours: 39 near misses under 2 s, of which 3 are under 1.5 s and none under 1 s. 139 of all pairs under 3 s were a right-turner from the east arm against traffic from the west, 17 the same right-turner against traffic from the north. That is roughly 55 events and 4 clips a day.
- On the same 17 hours: no incident candidates. The budget agreed was about 5 a day. The first version raised 8, every one a tracking fault or a vehicle waiting at a red light just outside its approach zone.
- **The one real collision so far was caught by its aftermath and not as a contact.** It happened at about 11:34 local on 2026-10-07, thirteen minutes before these detectors were first deployed. Replaying that hour's tracks raises no contact and no near miss. But the first two live candidates, at 11:47 and 11:48, were lone standstills on the east exit, and their clips show two cars pulled over there with someone standing beside them. I had taken them for a queue and restricted the rule to the junction; that was undone when the collision came to light. On one case: the standstill rule is the one that works, and it fires minutes late.
- That collision was between two cars making the same turn from the same arm, one into the other. The conflict finder leaves such pairs out by design, as one following another, so the contact rule could not have caught it. Afterwards the cars sat on the edge of the box, where their points drifted in and out of the zone, and one track broke after 81 s; neither reached a 90 s standstill there.
- The video of it came from the box-junction detector: one of the cars made a flagged stop six seconds after the impact, and the clip of that stop began a moment too late to show it. Box-junction stops now keep 30 s before them (0017).
- How many real incidents would be caught is otherwise unknown and cannot be measured from what has been recorded. Two vehicles that collide and both drive on within 20 s raise nothing. So does a collision where the two tracked points never come within 1.5 m, which can happen when one vehicle hits the far end of another.
- A vehicle is a point, so the gap is not the distance between bodies, and a long vehicle is closer than its gap says.
- Streams that merge, at under 60 degrees, are not seen. Nor is anything outside the calibrated area, nor any pedestrian: people are not detected.
- The filters that remove tracking faults also remove real conflicts where one party is slow, has just appeared, or is badly tracked. Half of moving tracks wobble less than 0.2 m and one in eight more than 0.5 m.
- Standing is measured in pixels, 10 px as for box-junction stops, which is a longer distance on the far side of the junction.
- The first clips are the first check against video. Until some have been looked at, a near miss is a pair of tracks that passed close, not a judgement about anyone's driving.
