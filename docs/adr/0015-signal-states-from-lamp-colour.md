# 0015. Signal states from lamp colour

Date: 2026-10-06, revised three times on 2026-10-07: after the first daylight, after the first direct sun, and when a fault in the level learning was found at dusk. Status: Accepted, with changing light an open problem. Low sun on a clear afternoon is not yet checked.

## Context

Red-light detection, and judging the forbidden left turn (0014), need the state of each signal head at a given moment. There is no feed from the controller; the camera is the only source. The spike (`docs/spikes/lamp-readability.md`) found that seven heads can be read from the full-resolution main stream at dusk and at night, with lamps 6 to 11 px apart.

The south arm's head faces away from the camera: only its green can be seen. The north arm's head cannot be seen at all.

## Decision

**Lamp regions are sampled in the frame source.** `site.yaml` gives each head a small rectangle per lamp: 3x3 px on the lamp's centre as measured in daylight, when a lamp shows without glow and is only 3 or 4 px across. The source returns each rectangle's mean colour with the frame. On the Pi that is read straight from the main stream's YUV420 buffer, so the full frame is never converted. A video replay samples the decoded frame. Nothing after the source knows which it was.

**A lamp is scored by colour, not brightness**: red as `r - max(g, b)`, amber as `min(r, 2g) - b/2`, green as `g - r/2`. Brightness alone read a pale vehicle behind a head as a lit red, and the red lamp's glow as amber. Amber takes twice the green because by day a lit amber is a dull red with little green in it; with the plain `min(r, g)` it barely rose above unlit.

**Each lamp learns its own lit and unlit levels** from the last 300 s of its scores, and is lit above the midpoint. The scores are split into two groups and each group's median is a level. Percentiles were used at first and failed by day: a dark vehicle behind a lamp reads below unlit, a pale one above lit, and those became the levels. The levels follow the light through the day with no settings per time of day.

**A lamp is trusted** once its two groups are far enough apart, each holds a second of readings and 2% of the history, and at most a tenth of the readings lie in the middle third between them. Far enough is 6 on a head with three lamps and 15 on a head with fewer. The limit was 30 until the first daylight, when a lit lamp is only 25 to 135 above unlit, and 15 until the first direct sun, when a red or an amber is only 6 to 13 above. Two groups a few points apart also come from noise and from things passing in front. A three-lamp head is protected from those by needing all three lamps trusted and a combination that means something; a head with one or two lamps is not, so it keeps the higher limit.

A lamp must also swing at least a fifth as much as the strongest lamp on its head: the glow of the lamp beside it moves an unlit lamp by up to 0.14 of that, and must not pass for the lamp itself. Until every lamp of a head is trusted, the head is `unknown`.

**A head's state** is the meaning of its set of lit lamps: red, red and amber, green or amber. Any other combination is `unknown`. The state is then steadied twice:

- a majority vote over 5 frames (a third of a second);
- a change is believed at once only if it is the next step of the UK sequence. A step out of sequence, a change to `unknown`, must last 1 s. A head with fewer than three lamps has no sequence to check against, so every change on it must last 2 s. A change that is believed is dated from when it was first seen.

**A head with no red lamp is red when it is not green**, and that red is marked `inferred`. Everything else is `observed`.

**A line's state is the agreement of its heads.** `controls` in `site.yaml` lists the stop lines or movements a head governs. Heads that are `unknown` are left out; if the rest disagree, or none is left, the line is `unknown`. States are kept for 15 minutes, so the state at a past moment can be asked for.

**Changes are published and stored.** Each change is a `SignalChange` on `trafficcam/v1/signals/<head>`, retained, so a new subscriber has every head's current state. Ingest stores them (0011). A passage records the state of its stop line when it crossed.

## A fault that ran from the first version to the evening of 2026-10-07

The levels were meant to be learned again every second from the last 300 s. They were learned again only while that history was still filling: the check for "a second has passed" went by the history's length, which stops growing at 300 s. So five minutes after every start, each lamp's levels froze.

- Overnight it did no harm: lamps look the same all night.
- It is why the reading collapsed at first light, came back when the service was restarted with that morning's changes, and went again half an hour later as the sun came out.
- The two daylight investigations in the spike note stand as far as they go, because they were done on clips of three to eight minutes read from a cold start. But the live failures they set out to explain were this fault first and the light second. What the reading does live in sunshine with the fault gone has not yet been seen.
- It was found at dusk on 2026-10-07 from the lamp colours by then being kept in the track log (0008): at 18:31 the camera's exposure dropped, every lit lamp's score halved, and the slip lane's heads stayed `unknown` for the ten minutes until someone looked.

Three things changed with the fix:

- **The levels are learned again every second**, counted and not inferred from the history's length. A test runs a lamp for twelve minutes and then changes the light.
- **If the whole 300 s does not fall into two groups, the last 110 s is tried.** After a change of exposure the history holds two lit levels for five minutes. Replaying that dusk, the west heads are then unknown for 1 to 11% of the ten minutes around the change, where the whole history alone gives 12 to 49%.
- **A head with a lamp that swings less than 25 is marked faint.** It is still read. What is done with that is in 0022.

## Consequences

- After a restart every head is `unknown` until each of its lamps has been seen lit and unlit: 25 s to 2 minutes on 2026-10-06. Crossings in that time have an unknown signal and raise no red-light events.
- Over two hours after dark on 2026-10-06 (92 cycles) the heads were `unknown` for 0 to 0.2% of the time. Heads that should change together showed the same state in 99.8 to 99.9% of seconds. Amber lasted 3.0 s and red-and-amber 2.0 s on every head, as the UK timings say they should.
- The mapping of heads to stop lines was checked against the same two hours. At the lagged crossing time (0016), the slip lane's heads were green for 94.1% of 459 crossings of its line, where the ahead heads were green for 63%. The ahead heads were green for 91.3% of 979 crossings of the ahead line, where the slip lane's heads were green for 75%.
- The north arm has no readable head, so its crossings have no signal state. 98.5% of 339 crossings of its stop line fell while the slip lane's heads were green and the ahead heads red. That pattern could stand in for the north arm's green. Since 0022 it does: the north arm's state is inferred from the plan of the signals, and so is a west line's when its heads cannot be read.
- On the first overcast morning the west heads were unknown for a third to a half of the time and the south head missed half its greens. The causes and the changes are in the spike note's daylight section. On a daytime clip the observer now reads six heads of seven with no wrong state, and the dusk and night clips read as before.
- The head `west_ahead_right` is read under cloud only just: its red lamp, hooded and seen from the side, swings a fifth of what its green does. In direct sun it is not read. Its stop line has two other heads.
- When the sun came out later that morning the west heads were unknown for 54 to 100% of the time. The camera exposes for the sunlit road, and a lit red or amber on a shaded head falls to 6 to 13 above unlit. With the lower limit, a sunny clip reads four heads of seven where it read two, with at least one on each stop line. `west_north_near`, `west_ahead_right` and `south` cannot be read in that light: their lamps are within the noise. Which heads those are changes as the sun moves.
- **Changing light is not handled by the lamp reading itself.** Since 0022 the plan of the signals covers for it: a head that keeps contradicting the plan is overruled where it does, and changes are found from the steps in the lamps' scores instead of from their levels. What follows is what the reading alone does. With cloud crossing the sun, a head's brightness changes by half within half a minute and its lamps' unlit levels move by as much as a lit red or amber adds. Levels learned over 300 s are then wrong for part of the time: live, two heads showed red-and-amber in place of red for up to 53 s at a time, and a third stayed `unknown`. That loses red-light events and does not invent them. The measurements and what was tried are in the spike note.
- A vehicle passing in front of a head can look like the next step of the sequence, which is believed at once: one amber of 1.1 s during a green in the sunny clip.
- None of this has been compared with hand labels. Low sun behind the camera on a clear afternoon has not been seen yet. The share of time each head is `unknown` is on the Signals dashboard.
- Lamp positions are in pixels for one camera pose, and by day a lamp is 3 or 4 px across. The view moved 1.3 px overnight on 2026-10-06, which was part of the daylight failure, and most of the way back by noon. A drift of that size is harmless at night, when lamps glow wider than they are. If the camera moves further, heads go `unknown` or, worse, read something else's colour. The camera-moved check does not exist yet.
- A lamp that fails, or a head that goes dark, reads as `unknown`, not as a fault.
- The sampling cost on the Pi is too small to see in the frame time: vision still runs at 15 fps.
