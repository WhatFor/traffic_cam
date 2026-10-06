# 0014. Hidden approaches are told apart by heading; the banned left turn

Date: 2026-10-06. Status: Accepted.

## Context

The building on the right of the east arm hides the right-hand side of the junction. Two streams of traffic first become visible in the same patch of road beside it:

- the south arm's traffic, which comes out heading across the image to the left;
- the east arm's westbound traffic in the lanes behind the structure, which comes out heading down the image like the rest of that arm.

A passage's entry was the first approach zone a track was seen in, so both streams were recorded as coming from the south arm. In 2.8 hours of track logs that made 1,077 passages `south->west`, almost as many as the 1,140 `east->west`. A five-minute recording on 2026-10-06 showed what they are: of 57 tracks first seen in the patch, 49 were heading 227 to 247 degrees and went west, and 8 were heading 158 to 178 degrees and went north or east. Nothing fell between.

Turning left out of the south arm, towards the west exit, is forbidden; a pedestrian crossing runs at that point in the signal cycle.

## Decision

**An approach zone can require a heading.** `entry_heading` on a zone gives a range in degrees, anticlockwise from image-right. Such a zone becomes a track's entry only if the track's heading falls in the range, measured from where it was first seen in the zone to where it is half a second later, provided it has moved at least 20 px. The patch is now two zones with the same polygon: `approach_south` (120 to 215 degrees) and `approach_east_hidden` (220 to 265 degrees).

**A heading in neither range gives no entry.** The passage then has no movement and raises no events, as with any other unknown movement (0013).

**The banned turn is a detector on closed passages.** `detectors.banned_turns` lists movements; a passage whose entry and exit zones match one raises a `banned_turn` event, timed at the vehicle's first sighting. The site lists one, `left_from_south`.

**A track seen in another arm's exit is not believed.** The one false candidate in the three hours was a track id that passed from a vehicle leaving north to another leaving west.

## Consequences

- On the three recorded hours, `east->west` goes from 1,140 to 2,203 and `south->west` from 1,077 to 9. Eight of the nine are raised as banned turns, about three an hour; the ninth is the track that changed vehicles.
- The eight look alike: first seen within a few pixels of (1435, 808), heading 190 to 211 degrees, then round to the left and out by the west exit. Six stopped for 4 to 11 seconds near (1250, 910), where the pedestrian crossing is; two drove through in about 4 seconds. None has been seen on video; the five-minute recording had no such turn.
- The ranges were set from the same three hours. South-arm traffic bound north or east is first seen heading 140 to 190 degrees, the turners 190 to 215, and the hidden lanes 225 to 250, with only four tracks between 215 and 225. A turner first seen heading more than 215 degrees is taken for east-arm traffic or gets no entry, and is missed.
- The right turn from the hidden lanes needed its own entry in the box junction's exemptions (`right_from_east_hidden`).
- Passages stored before this change keep their old labels, so the database shows `south->west` at around 380 an hour until the evening of 2026-10-06.
- When signals are monitored, the event can carry the state of the pedestrian crossing.
