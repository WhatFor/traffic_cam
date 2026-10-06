# 0015. Signal states from lamp colour

Date: 2026-10-06. Status: Accepted. Daytime is not yet checked.

## Context

Red-light detection, and judging the forbidden left turn (0014), need the state of each signal head at a given moment. There is no feed from the controller; the camera is the only source. The spike (`docs/spikes/lamp-readability.md`) found that seven heads can be read from the full-resolution main stream at dusk and at night, with lamps 6 to 11 px apart.

The south arm's head faces away from the camera: only its green can be seen. The north arm's head cannot be seen at all.

## Decision

**Lamp regions are sampled in the frame source.** `site.yaml` gives each head a small rectangle per lamp (5x5 px, or 3x3 px on the small heads). The source returns each rectangle's mean colour with the frame. On the Pi that is read straight from the main stream's YUV420 buffer, so the full frame is never converted. A video replay samples the decoded frame. Nothing after the source knows which it was.

**A lamp is scored by colour, not brightness**: red as `r - max(g, b)`, amber as `min(r, g) - b/2`, green as `g - r/2`. Brightness alone read a pale vehicle behind a head as a lit red, and the red lamp's glow as amber.

**Each lamp learns its own lit and unlit levels** from the last 300 s of its scores (2nd and 99th percentiles), and is lit above the midpoint. The levels follow the light through the day with no settings per time of day. They are trusted only once the scores span at least 30 and fall into two groups, with at most a tenth of them in the middle third. Until every lamp of a head is trusted, the head is `unknown`.

**A head's state** is the meaning of its set of lit lamps: red, red and amber, green or amber. Any other combination is `unknown`. The state is then steadied twice:

- a majority vote over 5 frames (a third of a second);
- a change is believed at once only if it is the next step of the UK sequence. A step out of sequence, a change to `unknown`, must last 1 s. A head with fewer than three lamps has no sequence to check against, so every change on it must last 2 s. A change that is believed is dated from when it was first seen.

**A head with no red lamp is red when it is not green**, and that red is marked `inferred`. Everything else is `observed`.

**A line's state is the agreement of its heads.** `controls` in `site.yaml` lists the stop lines or movements a head governs. Heads that are `unknown` are left out; if the rest disagree, or none is left, the line is `unknown`. States are kept for 15 minutes, so the state at a past moment can be asked for.

**Changes are published and stored.** Each change is a `SignalChange` on `trafficcam/v1/signals/<head>`, retained, so a new subscriber has every head's current state. Ingest stores them (0011). A passage records the state of its stop line when it crossed.

## Consequences

- After a restart every head is `unknown` until each of its lamps has been seen lit and unlit: 25 s to 2 minutes on 2026-10-06. Crossings in that time have an unknown signal and raise no red-light events.
- Over two hours after dark on 2026-10-06 (92 cycles) the heads were `unknown` for 0 to 0.2% of the time. Heads that should change together showed the same state in 99.8 to 99.9% of seconds. Amber lasted 3.0 s and red-and-amber 2.0 s on every head, as the UK timings say they should.
- The mapping of heads to stop lines was checked against the same two hours. At the lagged crossing time (0016), the slip lane's heads were green for 94.1% of 459 crossings of its line, where the ahead heads were green for 63%. The ahead heads were green for 91.3% of 979 crossings of the ahead line, where the slip lane's heads were green for 75%.
- The north arm has no readable head, so its crossings have no signal state. 98.5% of 339 crossings of its stop line fell while the slip lane's heads were green and the ahead heads red. That pattern could stand in for the north arm's green. It is not used.
- None of this has been compared with hand labels, and nothing has been measured in daylight. The risks there are LED flicker at short exposures and low sun lighting unlit lamps; the spike note lists what to record. The share of time each head is `unknown` is on the Signals dashboard.
- Lamp positions are in pixels for one camera pose. If the camera moves, every head goes `unknown` or, worse, reads something else's colour. The camera-moved check does not exist yet.
- A lamp that fails, or a head that goes dark, reads as `unknown`, not as a fault.
- The sampling cost on the Pi is too small to see in the frame time: vision still runs at 15 fps.
