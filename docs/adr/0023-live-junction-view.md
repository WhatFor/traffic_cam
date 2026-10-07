# 0023. The live view, and group states published twice

Date: 2026-10-07. Status: Accepted.

## Context

Everything the system knows about the junction was in tables and dashboards. The wish was for a picture of it: the road layout drawn simply, a dot for each vehicle moving as the vehicle did, and the signals showing what they showed. Close to live, minimal to look at, on the Pi for now and possibly on a public server later.

Three things stood in the way.

- **A vehicle is only known once it has gone.** A passage is published when its track ends (0005), with a start, an end and no path. A vehicle that queued for a minute is first heard of a minute after it arrived.
- **A signal's state is not always known as it happens.** A change found from the lamps' steps is known five seconds later; one placed by a later change, up to three quarters of a minute later (0022). And what was published was each head's raw reading, which in poor light is wrong, not what the system had concluded.
- **Where the camera is must not be given away** by the repo or, later, by a public page.

## Decision

**A drawing, not a map.** The junction is a schematic in abstract units, drawn by hand in `junction.json`: road edges, the box, stop lines and one path for each movement. No coordinates, imagery or names. It is the junction's arrangement, not its shape: the west arm has its three lanes in, the slip lane, one for ahead and one for the turn south, and two out, the east arm two lanes each way, the south arm two lanes in (the left for the north, the right for the east), and the north arm three lanes in (for the east, the south and the west, from the kerb) and one out, which the slip lane goes round an island to join, giving way. Lanes going the same way are parted by a dashed line, and the two directions of each road by a narrow strip of kerb, as they are on the ground. The boxes beside the roads are invented buildings, there for the look of it and all set the same way back to leave pavements; none stands for a real one. The pedestrian crossing's signal is not drawn: no pedestrians are, and it means nothing to the vehicles. The roads run off every edge of the page, and each way in and out is run on to the edge of whatever page it is shown on, so vehicles drive on and off it.

**It runs 90 seconds behind.** By then every vehicle that took under 85 s to get through is known whole, and every signal state has settled. A vehicle that took longer fades in where it should by then be.

**Only whole trips are drawn**: those with a known entry and exit. That was about four in ten overnight and half (242 of 478) in ten minutes of the first evening, unevenly: traffic from the east arm's hidden lanes is mostly seen only as it leaves. The picture is right in pattern and light in volume.

**A passage says when it reached the box and its exit.** `flags.path` holds `junction_at` and `exit_at`, the first frames the track was in the junction zone and in the zone it left by. A dot is at the box's edge and at its exit at those times. A trip that goes round the box and not through it, as the slip lane's does, has no time for the box; its stop-line crossing is used. (The first version left those out, so the slip lane showed no traffic at all.) The rest is simulated: it drives in, waits behind whoever went on to enter the box before it, and sets off in time to arrive when it really did. One that spends long in the box waits in it, as a right-turner does.

**Each group of heads is published twice.** A group is the heads that change together, as in the signal plan, with `north` for the head that cannot be seen. A new record, `GroupState`, goes out on a change of either of:

- its *live* state: what is known at once. When its heads are read that is immediate. Otherwise it is `unknown` for the moments that cannot be known yet. Retained on `trafficcam/v1/groups/<group>`.
- its *settled* state: what is known 45 s on, dated when it happened. This is the record: in order, and never revised. On `.../<group>/settled`.

Two plain streams were chosen over one that could be corrected after the fact, which every reader would have had to reassemble. Ingest stores both in `group_states`.

**The drawing uses the settled states and shows them with the vehicles of the same moment.** A stop line takes the colour of its group's state; that is the lamp. The page first had a row marked "now" showing the live states as well; it was taken off as clutter, so the live stream is published and stored but not yet drawn anywhere.

**A service of its own, `live`, on port 8082**, built and run as the clips site is (0018): ASP.NET serving static files and one endpoint, reading the database read-only, no login, no JavaScript build. It is separate from the clips site because it may one day be public and the clips never will be.

**One endpoint is the whole interface.** `GET /api/feed?since=` gives the server's time, each group's settled states from the one in force at `since`, each group's live state, and the whole trips that ended after `since`. The page asks every five seconds. Sending the same payload outward to another server is all that publishing it would take. `until` bounds the answer, and the page's `?at=<time>` uses it to play from a past moment.

**The Signals dashboard shows the groups above the heads.** The groups' settled states are what the system takes the signals to have shown; the heads below are what was read.

## Consequences

- Live lights are not drawn against live vehicles, and cannot be: the vehicles are not known yet.
- A dot's position between its two real times is a guess. Vehicles are spaced and ordered as they were at the box's edge, not necessarily as they were in the queue.
- The drawing is not to scale and its lanes are not the junction's lanes. Nothing should be measured from it.
- The drawing has to be kept in step with the site config by hand: a movement with no path in `junction.json` is not drawn, and a group with no stop line is not shown. A test lists the movements and groups it must have.
- The north arm's line is grey for a few seconds either side of each of its changes, because its links are known only to two seconds (0022). The east arm has no signal to show.
- Sending the feed to a public server would publish every whole trip, anonymous but individual, which is more than the build plan's "aggregates only". That is a decision still to be taken.
- 45 s is judged from the plan of the signals as it is. If the plan's links change, a state could settle later than that and the settled stream would have it as `unknown`.
- `group_states` got 232 rows in ten minutes on its first evening, which is some 33,000 a day.
- The page keeps the server's time from each answer, so a browser whose clock is wrong still draws the right moment.
