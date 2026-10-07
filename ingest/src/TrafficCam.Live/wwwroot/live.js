// Draws the junction as it was a minute and a half ago: a dot for each vehicle whose whole trip
// is known, moving along the path of its movement at its own times, and each signal group's
// stop line in the colour of its state. With ?at=<time> it plays from that moment instead.

const DELAY_S = 90;        // how far behind the present the drawing runs
const POLL_MS = 5000;
const KEEP_S = 240;        // how long what has been drawn is kept
const LOOK_BACK_S = 60;    // how much before the moment drawn is asked for each time
const CRUISE = 11;         // units a second for a vehicle that is not held up
const GAP = 5;             // between vehicles waiting one behind another
const FASTEST = 2;         // times the cruising speed, through the junction
const LEAST_THROUGH_S = 2.5; // taken to get through, for a trip that says nothing of when it began to
const FADE_S = 0.8;
const BEYOND = 6;          // how far past the edge of the page a vehicle's path runs
const SVG = "http://www.w3.org/2000/svg";
const STATES = ["red", "red_amber", "amber", "green", "unknown"];
const SIZES = { bus: 2.3, truck: 2.2, car: 1.6, motorcycle: 1.1, bicycle: 1.0 };

// Road signs, drawn in a circle of radius 1 for traffic heading up the page.
const SIGNS = {
    no_left_turn: [
        ["circle", { class: "plate", r: 1 }],
        ["path", { class: "arrow", d: "M 0.32 0.58 L 0.32 -0.02 Q 0.32 -0.24 0.1 -0.24 L -0.42 -0.24 M -0.2 -0.46 L -0.44 -0.24 L -0.2 -0.02" }],
        ["path", { class: "bar", d: "M -0.7 -0.7 L 0.7 0.7" }],
    ],
};

const svg = document.getElementById("junction");
const clock = document.getElementById("clock");
const replayFrom = Date.parse(new URLSearchParams(location.search).get("at") ?? "");
const started = performance.now();
let ahead = 0;             // the server's clock less this one's, in ms
const passages = new Map();
const groups = new Map();  // name -> { settled: [{ts, state, source}], live }
const lamps = new Map();
const lanes = [];          // the straight ways in and out, each run on to the edge of the page
let paths;

function make(name, attributes, parent = svg) {
    const element = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    parent.append(element);
    return element;
}

/** The moment being drawn, in ms. */
function shown() {
    return Number.isNaN(replayFrom)
        ? Date.now() + ahead - DELAY_S * 1000
        : replayFrom + (performance.now() - started);
}

function draw(junction) {
    svg.setAttribute("viewBox", junction.view.join(" "));
    const hatch = make("pattern", { id: "hatch", width: 2.4, height: 2.4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, make("defs", {}));
    make("line", { class: "hatch", x1: 0, y1: 0, x2: 0, y2: 2.4 }, hatch);
    make("path", { class: "road", d: junction.road });
    for (const d of junction.marks) make("path", { class: "mark", d });
    for (const d of junction.medians ?? []) make("path", { class: "median", d });
    make("path", { class: "box", d: junction.box });
    for (const d of junction.edges) make("path", { class: "edge", d });
    for (const d of junction.stops) make("path", { class: "stop", d });
    for (const d of junction.gives ?? []) make("path", { class: "give", d });
    for (const sign of junction.signs ?? []) {
        const [x, y] = sign.at;
        const group = make("g", { class: "sign", transform: `translate(${x} ${y}) rotate(${sign.heading}) scale(${sign.size})` });
        for (const [name, attributes] of SIGNS[sign.kind] ?? []) make(name, attributes, group);
    }
    for (const [group, d] of Object.entries(junction.lamps)) lamps.set(group, make("path", { class: "lamp", d }));
    // The paths vehicles move along are measured, not shown.
    const measured = (d) => make("path", { d, fill: "none", stroke: "none" });
    const lane = ([outer, inner], inward) => {
        const made = { element: measured(""), outer, inner, inward };
        lanes.push(made);
        return made.element;
    };
    const approaches = Object.fromEntries(Object.entries(junction.approaches).map(([name, ends]) => [name, lane(ends, true)]));
    const exits = Object.fromEntries(Object.entries(junction.exits).map(([name, [inner, outer]]) => [name, lane([outer, inner], false)]));
    paths = new Map(Object.entries(junction.movements).map(([movement, parts]) => [movement, {
        lane: parts.approach,
        parts: [approaches[parts.approach], measured(parts.through), exits[parts.exit]],
    }]));
    fit();
    addEventListener("resize", fit);
}

/** Runs every way in and out on to just past the edge of the page, however the page is shaped. */
function fit() {
    const toDrawing = svg.getScreenCTM().inverse();
    const corner = (x, y) => new DOMPoint(x, y).matrixTransform(toDrawing);
    const [low, high] = [corner(0, 0), corner(innerWidth, innerHeight)];
    for (const { element, outer, inner, inward } of lanes) {
        const [x, y] = [outer[0] - inner[0], outer[1] - inner[1]];
        const length = Math.hypot(x, y);
        // How far from its inner end the lane leaves the page, going outward.
        const leaves = Math.min(
            x > 0 ? (high.x - inner[0]) / (x / length) : x < 0 ? (low.x - inner[0]) / (x / length) : Infinity,
            y > 0 ? (high.y - inner[1]) / (y / length) : y < 0 ? (low.y - inner[1]) / (y / length) : Infinity,
        ) + BEYOND;
        const edge = [inner[0] + (x / length) * leaves, inner[1] + (y / length) * leaves];
        const [from, to] = inward ? [edge, inner] : [inner, edge];
        element.setAttribute("d", `M ${from.join(" ")} L ${to.join(" ")}`);
    }
    for (const path of paths.values()) path.lengths = path.parts.map((part) => part.getTotalLength());
}

function take(feed) {
    for (const [name, group] of Object.entries(feed.groups)) {
        const known = groups.get(name) ?? { settled: [], live: null };
        const changes = group.settled.map((change) => ({ ...change, ts: Date.parse(change.ts) }));
        // What has just come replaces what was held from its first moment on.
        const from = changes.length ? changes[0].ts : Infinity;
        known.settled = known.settled.filter((change) => change.ts < from).concat(changes);
        known.live = group.live;
        groups.set(name, known);
    }
    for (const passage of feed.passages) {
        const path = paths.get(passage.movement);
        if (passages.has(passage.id) || !path) continue;
        const [, box] = path.lengths;
        const seconds = (key) => (passage[key] ? Date.parse(passage[key]) / 1000 : null);
        const firstSeen = seconds("first_seen");
        const reached = seconds("exit_at");
        // The end of the approach is the edge of the box. A trip that goes round the box, as
        // the slip lane does, never enters it: for that one it is its stop line.
        const entered = Math.max(firstSeen, seconds("junction_at") ?? seconds("line_at") ?? reached - LEAST_THROUGH_S);
        if (!(entered < reached)) continue;
        passages.set(passage.id, {
            path,
            firstSeen,
            junctionAt: entered,
            // Drawn paths are longer than some real ones: none is taken faster than this.
            exitAt: Math.max(reached, entered + box / (FASTEST * CRUISE)),
            waitAt: null,
            dot: make("circle", { class: "dot", r: SIZES[passage.class] ?? SIZES.car, opacity: 0 }),
        });
    }
}

const ease = (u) => u * u * (3 - 2 * u);

/** How far through the box a vehicle is. One that takes long is waiting in it to turn. */
function through(seconds, whole) {
    if (whole <= 6) return seconds / whole;
    if (seconds < 2) return 0.4 * ease(seconds / 2);
    if (seconds > whole - 2.5) return 0.4 + 0.6 * ease((seconds - (whole - 2.5)) / 2.5);
    return 0.4;
}

/** How far along its path a vehicle is at a time, in units from the start of its approach. */
function along(passage, time, elapsed) {
    const [approach, box, exit] = passage.path.lengths;
    if (time >= passage.exitAt) return approach + box + Math.min(exit, (time - passage.exitAt) * CRUISE);
    if (time >= passage.junctionAt) {
        return approach + box * through(time - passage.junctionAt, passage.exitAt - passage.junctionAt);
    }
    // On the approach it drives in, waits behind whoever will enter the box before it, and
    // sets off in time to reach the box when it really did.
    let before = 0;
    for (const other of passages.values()) {
        if (other !== passage && other.path.lane === passage.path.lane
            && other.junctionAt < passage.junctionAt && other.firstSeen <= time && time < other.junctionAt) before += 1;
    }
    const place = approach - 1.5 - before * GAP;
    passage.waitAt = passage.waitAt === null ? place : Math.min(place, passage.waitAt + CRUISE * 0.6 * elapsed);
    // Seen only a moment before the box, it was already well along the approach.
    const startAt = Math.max(0, approach - (passage.junctionAt - passage.firstSeen) * CRUISE);
    const drivingIn = startAt + (time - passage.firstSeen) * CRUISE;
    const settingOff = approach - (passage.junctionAt - time) * CRUISE;
    return Math.max(Math.min(drivingIn, passage.waitAt), settingOff);
}

function place(passage, time, elapsed) {
    const [approach, box, exit] = passage.path.lengths;
    const ends = passage.exitAt + exit / CRUISE;
    if (time < passage.firstSeen || time > ends) {
        passage.dot.setAttribute("opacity", 0);
        return time <= ends;
    }
    let distance = along(passage, time, elapsed);
    let part = 0;
    while (part < 2 && distance > passage.path.lengths[part]) distance -= passage.path.lengths[part++];
    const point = passage.path.parts[part].getPointAtLength(distance);
    const opacity = Math.min(1, (time - passage.firstSeen) / FADE_S);
    passage.dot.setAttribute("cx", point.x.toFixed(2));
    passage.dot.setAttribute("cy", point.y.toFixed(2));
    passage.dot.setAttribute("opacity", Math.max(0, opacity * 0.85).toFixed(2));
    return true;
}

function show(element, state, source) {
    element.classList.remove(...STATES, "inferred");
    element.classList.add(state);
    if (source === "inferred") element.classList.add("inferred");
}

let last = performance.now();
function frame() {
    const at = shown();
    const elapsed = Math.min(0.25, (performance.now() - last) / 1000);
    last = performance.now();
    for (const [id, passage] of passages) {
        if (!place(passage, at / 1000, elapsed)) {
            passage.dot.remove();
            passages.delete(id);
        }
    }
    for (const [name, group] of groups) {
        const lamp = lamps.get(name);
        if (!lamp) continue;
        let current = null;
        for (const change of group.settled) {
            if (change.ts > at) break;
            current = change;
        }
        show(lamp, current?.state ?? "unknown", current?.source);
        while (group.settled.length > 1 && group.settled[1].ts < at - KEEP_S * 1000) group.settled.shift();
    }
    const time = new Date(at).toLocaleTimeString([], { hour12: false });
    clock.innerHTML = Number.isNaN(replayFrom)
        ? `${time} <span>&middot; ${DELAY_S} seconds ago</span>`
        : `${time} <span>&middot; ${new Date(at).toLocaleDateString()}</span>`;
    requestAnimationFrame(frame);
}

let polledTo = null;
async function poll() {
    try {
        const at = shown();
        // Live, it asks again for the last while: a settled state is stored some time after the
        // moment it is dated. Looking back, it asks for what is coming up.
        const since = new Date(Number.isNaN(replayFrom) ? (polledTo ?? Date.now()) - LOOK_BACK_S * 1000 - DELAY_S * 1000 : at - 20_000);
        const until = Number.isNaN(replayFrom) ? "" : `&until=${new Date(at + 150_000).toISOString()}`;
        const sent = Date.now();
        const response = await fetch(`api/feed?since=${since.toISOString()}${until}`);
        if (response.ok) {
            const feed = await response.json();
            // The server's clock, allowing for half the time the answer took.
            ahead = Date.parse(feed.now) - (sent + Date.now()) / 2;
            polledTo = Date.parse(feed.now);
            take(feed);
        }
    } catch {
        // The next poll will try again.
    }
    setTimeout(poll, POLL_MS);
}

draw(await (await fetch("junction.json")).json());
await poll();
requestAnimationFrame(frame);
