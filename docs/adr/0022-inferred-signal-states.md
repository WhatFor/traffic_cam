# 0022. Signal states inferred from the plan of the signals

Date: 2026-10-07. Status: Accepted, on one day's data.

## Context

A crossing has a signal state only if a head controlling its line can be read (0015). Two kinds have none:

- the north arm, whose head is not in view: about 1,000 crossings of `stopline_north` a night;
- the west lines whenever their heads cannot be read, which in sunshine is most of the time. In the hour before this went live the ahead line's signal was known for 7% of its crossings.

The build plan expected a hard problem: signals that respond to demand, phases that vary and are skipped, timers that drift within a cycle or two. It asked for a state machine over the stage sequence, fed by the visible heads, by which movements exclude each other and by traffic flow, and for it to be dropped if its accuracy stayed poor.

## What the junction does

From the stored signal changes of 12 hours (2026-10-06 19:30 to 2026-10-07 08:00, about 510 cycles):

- The order never varies in practice: ahead green on, slip green on, ahead off, slip off, south on, crossing on, south and crossing off. It was this in 425 of 428 cycles overnight.
- Eight of the eleven gaps between those changes are fixed by the controller, several to a tenth of a second and none looser than four seconds: slip green ends, 4.0 s later south begins, 5.1 s later the crossing, and so on. They were the same in the evening, overnight, in the morning peak and in daylight.
- Three vary with the traffic: how long the ahead green lasts (12 to 40 s), how long the slip green goes on after it (17 to 33 s), and so the slip green's own length.
- The north arm's traffic starts crossing its line 9 s after the ahead green ends and stops as the slip green does: 97% of 1,012 overnight crossings fall there.

So it is not a hard problem here. One head that can be read places most of the cycle.

## Decision

**The plan of the signals is written down in `site.yaml`**, as `signal_plan`:

- *groups*: heads that change together. `west_ahead`, `west_slip`, `south`, `crossing`, and `north`, which has no head and is given the line it controls.
- *links*: the least and most seconds from one group's green starting or ending to another's. A fixed gap is a link a fraction of a second wide.

`just learn-signal-plan` prints the links from stored signal changes, as `just calibrate-speed` prints the ground map: numbers that can be read and reviewed, not a model inside the service. A link's range is the narrowest that holds 97% of the times it was seen; what falls outside is mostly a misread change pairing one cycle with the next. A gap that varies is given a fifth of its range again at each end. Links that others already imply are left out. The north arm's three links were set by hand from `just signal-flow`, which shows when a line's traffic starts and stops against each change.

**The estimator does arithmetic on bounds.** Each change that is read gives every change linked to it an earliest and a latest time, forwards and backwards, through as many links as stay within 30 s of doubt. A state is given only where the bounds leave none: green from the latest its start can have been to the earliest its end can have been, amber and red-and-amber from the fixed 3 s and 2 s, red between. Anything else is `unknown`. There are no probabilities and nothing to tune.

**Only a clean step of the sequence is evidence**: red-and-amber to green, green to amber. A head going to or from `unknown` is not, which keeps a sunlit head's dropping in and out from placing anything. An amber that goes back to green was something passing in front, and the green's end is taken back.

**It can be asked about the past**, and usually is. A passage's signal is looked up when the passage closes (0016), by which time a change that only a later one places has been placed.

**Nothing runs free.** An inferred change needs a read one within the links' reach. With no head readable, everything is `unknown`.

**It only fills gaps.** `Signals.line_state` asks the estimator when none of a line's heads is known, or it has none. A head that can be read is never overruled, and heads that disagree still make the line `unknown`.

**Inferred states are recorded and nothing more.** They go on the passage, with `signal_source = inferred`. The red-light detector takes only observed states (0016), so no event comes from them. They are not published as signal changes: some are known only seconds after the fact, and the dashboard would need records that arrive out of order.

**A change read outside a fixed gap is counted**, in `trafficcam_vision_signal_plan_violations_total`, and what was read is believed over what was worked out. A steady rise would mean the controller's plan has changed and the links need learning again.

## Accuracy

The test the build plan asked for: hide one group's heads from the estimator, and score what it says of that group against what the observer read, second by second. `just eval-signal-plan` does it. Over the 12 hours the links were learned from:

| Group hidden | State given, asked at once | Asked 30 s on | Right, of what was given |
| --- | --- | --- | --- |
| `west_ahead` | 51% of the time | 71% | 99.90% and 99.95% |
| `west_slip` | 85% | 92% | 99.98% and 100.00% |
| `south` | 93% | 94% | 99.98% and 99.97% |
| `crossing` | 95% | 95% | 99.96% |

- A green's start was placed to within 0.7 to 0.9 s and its end to within 0.4 to 0.6 s, and each fell where it was placed 98.0 to 99.6% of the time. The exception is the ahead green's end, placed only to within 16 s.
- With the links learned from the first six and a half hours and the scoring done on the last six: right 99.81 to 100.00%, given 72 to 94%.
- The north arm has nothing to score against. Of 2,006 crossings of its line, 82.7% fell in its inferred green, 0.5% in amber, 0.5% in red, and 16.3% where its state is not known, mostly the first vehicles away.
- 97 changes came outside a fixed gap, of about 4,000.

In daylight the same test cannot be read as accuracy, because the readings it scores against are wrong (0015). Over nine and a half hours of 2026-10-07 the largest disagreement by far was 5,170 s on `west_ahead` that the observer called red-and-amber and the estimator called red: the observer's known fault in changing light. The pedestrian crossing's head reads well in every light, and hidden through those same hours it was given right 99.4% of the time.

What it adds where the heads fail, on the same daytime hours: the slip lane's line had a state for 62% of its crossings from its heads and has one for 92% with the estimator; the ahead line 58% and 72%; the north arm none and 65%.

## Consequences

- This is one day. The controller may run another plan at a weekend or a peak. Fixed gaps held across every period measured; the three that vary ran longer in the morning than overnight, which is why they are given room.
- The ahead green's end is the weak place. Nothing fixes it but its own head or, 17 to 33 s later, the slip green's end. Amber on the ahead line is never inferred, and when the ahead heads cannot be read the north arm's start is in doubt too: a third of its daytime crossings have no state.
- The north arm's links are a guess from its traffic, good to a second or two. A driver who jumps its red looks like its green starting early.
- The estimator believes the observer. A misread change that nothing contradicts places others wrongly. Two places for one change that cannot both be right are both dropped, and a change read outside a fixed gap is counted, but a lone wrong reading is not caught.
- The estimator and the observer disagreeing is itself a sign of a misread lamp, as the daytime figures show. Nothing uses that yet.
- Inferred states are on passages only. Nothing on the Signals dashboard shows them.
- The east arm is not estimated: none of its stop lines is in view.
- It remembers 15 minutes of changes, a few hundred numbers.
