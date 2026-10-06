# Spike: reading signal lamps from the main stream

Phase 5, first item. Status on 2026-10-06: dusk and night measured; day still to do.

## Question

The build plan reads signal lamps from the 2028x1520 main stream, the same one that feeds the video encoder. Are the lamps large and clean enough in that stream to tell the signal states apart, in all light? If not, the fallback is a separate full-resolution read of the lamp regions.

## Method

- A three-minute clip of the live stream, recorded with `ffmpeg -c copy`, so it is the encoder's output at 6 Mbit/s. The observer will read frames before the encoder, so this is a worst case.
- `tools/lamp_spike.py find` locates each head's three lamps from which pixels change in which colour over the clip. `measure` then samples a 3x3 or 5x5 px square at each lamp in every frame, decides lit or unlit per lamp, takes a five-frame vote, and prints the sequence of states.
- A lit red is scored by how red it is, the others mostly by brightness. Scoring red by brightness was tried first and failed: a lit red LED is dim (brightness about 65 of 255) and a pale vehicle behind the head read as a lit red.
- Lamp positions are in `calibration/lamp_spike/lamps.json`, which is not in the repo. Heads are lettered A to G by position.

## What each head controls

As told by the site's owner on 2026-10-06. Positions are in the camera pose of that date.

| Head | At | Controls |
| --- | --- | --- |
| A, B | (594, 748), (795, 703) | West arm, the slip lane turning north (`stopline_west_northbound`) |
| C, E, G | (817, 766), (1045, 814), (1429, 484) | West arm, ahead to the east and turning south (`stopline_west_eastbound`) |
| D | (972, 641) | South arm, the one the camera cannot see |
| F | (1092, 887) | Pedestrian crossing over the west exit |

F shows green while D does. Traffic from the south arm may not turn left towards the west exit, because pedestrians are crossing it at that moment.

## Dusk, 2026-10-06 18:41 (2,693 frames, 179 s)

| Head | Kind | Lamp spacing | Weakest separation | Sequence | Misreads |
| --- | --- | --- | --- | --- | --- |
| A (594, 748) | vehicle | 9 to 11 px | 16 sd | full UK sequence, in order | none |
| B (795, 703) | vehicle | 8 to 9 px | 22 sd | same timing as A, in order | none |
| C (817, 766) | vehicle | 9 to 10 px | 21 sd | full UK sequence, in order | none |
| E (1045, 814) | vehicle, with backing board | 9 px | 10 sd | same timing as C | 0.7 s with all three lit, as a vehicle passed |
| G (1429, 484) | vehicle, far side | 7 px | 3 sd (amber) | same timing as C | 1.3 s of false amber during red |
| F (1092, 887) | pedestrian | 9 px | 9 sd | red, with green for 4.8 s once a cycle | 0.5 s dark as a pedestrian walked past |
| D (972, 641) | vehicle, seen from the side | one lamp read | 16 sd | green for 10 s once a cycle | 1.5 s of false green as something green passed |

"Separation" is the gap between a lamp's lit and unlit scores, in standard deviations of the scores.

What the clip shows:

- **The lamps are big enough.** The five vehicle heads are about 13 px wide and 28 px tall; each lamp is 6 to 8 px across. All four states (red, red and amber, green, amber) were read on all five, with the right durations: amber 2.9 s, red and amber 1.9 s.
- **Flicker is not a problem at this light level.** Out of 150 to 1,700 lit frames per lamp, at most 6 read dark. A five-frame vote removes them.
- **Heads run in two groups.** A and B change together; C, E and G change together. The cycle is 91 s.
- **The south arm's green and the pedestrian green sit inside the period when both west-arm groups are red.** D is green for 10 s; F is green for the last 4.8 s of that.
- **Every misread was something passing in front of or behind a head.** They show up as impossible combinations or as changes out of sequence, which is how the observer should recognise them and report `unknown`.
- **E's red is dim** (10 to 15 sd where A's is 43): the camera looks down on it and the hood hides most of the lamp. It still read correctly throughout.
- **G is the smallest** and needs a 3x3 sample; with 5x5 its neighbours bleed into each other.
- **D is read by its green alone.** It faces the south arm, so the camera sees it from the side. Two washed-out lamps on it stay lit through the whole cycle; what changes is a pair of green patches below them, which look like arrows. Its state is therefore green or not green, with no amber and no red to confirm it.

## Night, 2026-10-06 19:18 (2,697 frames, 180 s)

Mean image brightness 22 of 255, against 90 in the dusk clip. Same lamp positions as at dusk.

| Head | Weakest separation | Sequence | Misreads |
| --- | --- | --- | --- |
| A | 17 sd | full UK sequence, in order | none |
| B | 24 sd | same timing as A, in order | none |
| C | 27 sd | full UK sequence, in order | none |
| E | 19 sd | same timing as C, in order | none |
| G | 6 sd (amber) | same timing as C, in order | none |
| D | 19 sd | green for 11.0 s and 10.0 s | none |
| F | 10 sd | red, with green for 5.8 s and 4.8 s | none |

- **Night is easier than dusk.** Every head read correctly for the whole clip, with no misreads at all. Against a dark background the lit and unlit scores sit further apart, and passing vehicles no longer disturb the reading.
- **The lamps do not bloom.** Lit colours are about what they were at dusk (A's red is rgb(135, 13, 35), against rgb(170, 19, 41)), and unlit lamps next to lit ones stay near zero. The feared bleed between neighbouring lamps did not happen.
- **G's amber is still the tightest margin**, because the red above it is 7 px away, but it read correctly.
- **At most 4 dark frames per lamp** out of up to 2,500 lit.
- **Timing held**: A and B together, C, E and G together, F green for the last 5 s or so of D's green. The cycle was 93 s.

It was not the darkest the scene gets: late at night the image brightness falls to about 10. Whether the camera had already reached its longest exposure at 19:18 was not checked; if it had, the lamps will look the same later.

## Conclusion so far

At dusk and at night the main stream is enough for the five west-arm heads, for the south arm's green on D, and for pedestrian signal F. No full-resolution path is needed on this evidence.

Not yet known:

- **Day.** This is now the case most likely to fail, for two reasons. Low sun from behind the camera can light unlit lamps. And in bright light the exposure is short, which is when LED flicker shows as dark frames; at dusk and at night the exposure was long enough to hide it. There is no daytime clip in the current camera pose.
- **The darkest hours**, as noted above.
- **Uncompressed frames.** Not measured; expected to be better than this.

## For the observer

- Score red by colour, amber and green mostly by brightness.
- Compare lamps within a head, as the build plan says, and treat an impossible combination or an out-of-sequence change as `unknown`.
- Sample no more than 5x5 px, and 3x3 px on heads under about 8 px lamp spacing.

## Running it on another clip

```sh
cd vision
uv run python ../tools/lamp_spike.py measure ../calibration/<clip>.mp4 \
    ../calibration/lamp_spike/lamps.json ../calibration/lamp_spike
```

It writes `<clip>_report.txt` and `<clip>_timeline.png` (each head's lamps as coloured bands against time). The lamp positions hold for the current camera pose only.
