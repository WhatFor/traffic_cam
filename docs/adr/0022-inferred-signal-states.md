# 0022. Signal states inferred from the plan of the signals

Date: 2026-10-07, extended the same evening: the plan now checks the heads, a closely placed inferred state can raise an event, and loosely placed changes are looked for in the lamps' steps. Status: Accepted, on one day's data.

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

**It fills gaps, and it overrules a head only once that head has shown itself unreliable.** `Signals.line_state` asks the estimator when none of a line's heads is known, or it has none. Heads that disagree with each other still make the line `unknown`.

Every second, each head's reading of three seconds earlier is compared with what the plan says it should have shown, going only by the heads of other groups. A head is *doubted* once more than 2% of ten minutes' comparisons disagree. In the dark a head disagrees in under one in a thousand; a sunlit head that misreads does in one in ten or more. While a head is doubted:

- where the plan rules out what it shows, its reading is left out, and the line takes the inferred state;
- where the plan is silent or agrees, it is still what the line goes by;
- what it is seen to do is not passed to the estimator, and what it had passed is forgotten.

A head whose lamps are faint (0015: a lamp that swings less than 25, as in direct sun) is treated as doubted from the start, without waiting for it to be caught out. A head that is neither, and agrees with the plan, is believed over it. The plan is wrong for a few seconds in ten hours, when the controller does something unusual, and a good head should win then. `trafficcam_vision_signal_head_doubted` says which heads are doubted.

**An inferred state goes on the passage**, with `signal_source = inferred`. Inferred states are not published as signal changes: some are known only seconds after the fact, and the dashboard would need records that arrive out of order.

**A closely placed one can raise an event.** `detectors.red_light.inferred_within_s` (1 s) lets a red or an amber count when the change that began it is placed that closely (0016). In practice that is the slip lane's line, whose green end the pedestrian crossing's head places to 0.6 s. The ahead line's green end is placed to 16 s and the north arm's links are guesses 2 s wide, so neither qualifies. The state is dated from the latest it can have begun, so an event's time into red is the least it can be, and the half second of grace comes on top. The event carries `signal_source: inferred`.

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

## Changes found in the lamps' steps

Reading a lamp against levels fails when the light keeps changing (0015). But a lamp switches within a frame or two and the light drifts over seconds, so the second after a change still differs from the second before it. The plan says which changes are due and roughly when; `StepFinder` looks in the lamps' own scores for exactly when.

- **What it looks for.** A green ending makes four steps on a head: green down and amber up together, and three seconds later amber down and red up. A green starting makes four as well, with the red-and-amber two seconds before it. Each lamp's step, the middle of the second after a frame less the middle of the second before, is measured in units of how much that lamp usually steps, and held between -2 and 6 so that one lamp cannot carry or cancel the rest. The steps of every three-lamp head of the group are added up for each frame.
- **When it is believed.** The best frame in the window must score 9 for each head of the group and twice what any frame more than 2.5 s from it scores. Otherwise nothing is found.
- **When it is asked.** Every second, for each change the estimator places no better than 2 s, over as much of its window as has by then been seen in full, 4.5 s behind the camera. So a change is found about five seconds after it happens. Where the heads are being read there is nothing loose and nothing is looked for.
- **What is done with it.** The change goes to the estimator as placed to 0.6 s either side of the frame found. That is 1.2 s, wider than the 1 s `inferred_within_s` allows, so a change found this way fills in recorded states and does not yet raise events.

Two places for one change that cannot both be right are no longer both dropped: the change is taken to be at one of them or between, which is wider but still says when it was certainly over. On the sun-and-cloud clip that took the slip lane's line from wrong 0.9% of the time to 0.2%.

Two things in the estimator changed to make this work. Each pair of changes now keeps a span for every occasion links lead to, the one before and the one after, where it kept only the narrowest; without that the ahead green's end was not placed at all until the slip green ended half a minute later. And a sighting can be taken back when the head it came from is doubted.

On the five recorded clips, with the two west lines' states asked 30 s on and scored against the truth:

| Clip | Ahead line: heads only | With the plan | With the plan and steps | Slip lane's line, the same three |
| --- | --- | --- | --- | --- |
| Sun and cloud, 8 min | never known | right 60%, wrong 0.6% | right 97.7%, wrong 0.2% | 10%; 64%, wrong 2.0%; 95.8%, wrong 0.1% |
| Steady sun, 4 min | right 91% | 91% | 91% | 90%; 90%; 91%, each wrong 0.3% |
| Dusk, 3 min | right 93% | 93% | 97% | 79%; 81%; 97% |
| Night, 3 min | right 90% | 90% | 90% | 87%; 87%; 89% |

What is not right is not known, not wrong, apart from the figures given; the clips start cold, which is most of the unknown. In the sun-and-cloud clip it found all seven starts and ends of the ahead green, each within half a second of the truth. Tried on each true change in the five clips that a 28 s window placed at random fitted round, 48 in all, it found every one within 0.3 s. Asked of the 20 s before each of those changes, where there is nothing to find, it found nothing.

That is fifteen minutes of sunshine in all. It is why what it finds does not yet raise events.

## What the checks change

Replaying the stored readings of 2026-10-07 through the checks, asked 30 s on:

| | Ahead line, 09:30 to 17:30 | Slip lane's line | Overnight, both |
| --- | --- | --- | --- |
| Seconds in a red-and-amber of over 4 s, before | 5,945 | 2,181 | 6 |
| ... after | 542 | 204 | 6 |
| Seconds in an amber of over 5 s, before and after | 450, 40 | 20, 20 | none |
| State unknown, before and after | 34%, 19% | 46%, 10% | no change |

- By day the west heads were doubted for a third to two thirds of the time, and the south head for 68%.
- Overnight the only head ever doubted was `west_ahead_right`, for 2% of the time, and no line's state changed by a tenth of a percent.
- In those eight daytime hours 23 crossings of the slip lane's line fell on a closely placed inferred red and 28 on amber. How many become events depends on the rest of 0016's rule. None has been checked against video.

## Consequences

- The step finder has been tested on five clips, two of them sunny. Lamp colours are now kept in the track log (0008), so it can be tested on every sunny hour from here on; until it has been, treat the states it fills in as probable.
- A step is measured over a second either side, so two changes less than a second apart would blur. None are: the shortest state is two seconds.
- This is one day. The controller may run another plan at a weekend or a peak. Fixed gaps held across every period measured; the three that vary ran longer in the morning than overnight, which is why they are given room.
- The ahead green's end was the weak place: nothing fixed it but its own head or, 17 to 33 s later, the slip green's end. The step finder is what now places it when the heads cannot be read, and the north arm's start with it. How much of the third of north-arm daytime crossings without a state that recovers is not yet measured.
- The north arm's links are a guess from its traffic, good to a second or two. A driver who jumps its red looks like its green starting early.
- The estimator believes the observer. A misread change that nothing contradicts places others wrongly. Two places for one change that cannot both be right are both dropped, and a change read outside a fixed gap is counted, but a lone wrong reading is not caught.
- Nothing on the Signals dashboard shows inferred states or which heads are doubted. The heads' own published states are still what the observer read, wrong or not.
- An event from an inferred red has not been seen by the camera as a red lamp. The clip shows the crossing, and the head if it can be made out; the event says it was inferred.
- A head is given the benefit of the doubt for its first minute of comparisons, and after a restart.
- The east arm is not estimated: none of its stop lines is in view.
- It remembers 15 minutes of changes, a few hundred numbers.
