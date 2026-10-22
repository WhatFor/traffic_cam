// Times arrive in UTC; show them in the reader's own time zone.
const format = new Intl.DateTimeFormat(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
});
for (const time of document.querySelectorAll("time[datetime]")) {
    time.textContent = format.format(new Date(time.dateTime));
}

for (const button of document.querySelectorAll("button[data-seek]")) {
    button.addEventListener("click", () => {
        const video = document.querySelector("video");
        video.currentTime = Number(button.dataset.seek);
        video.play();
    });
}

// How fast the clip plays. The one chosen stays marked.
const rates = document.querySelectorAll("button[data-rate]");
for (const button of rates) {
    button.addEventListener("click", () => {
        document.querySelector("video").playbackRate = Number(button.dataset.rate);
        for (const each of rates) {
            each.setAttribute("aria-pressed", each === button);
        }
    });
}

// The "include archived clips" box applies as soon as it is changed.
for (const box of document.querySelectorAll("input[data-submit]")) {
    box.addEventListener("change", () => box.form.submit());
}

// The live camera, from MediaMTX over WebRTC (WHEP). Each second's keyframe is about half a
// megabyte and comes in a burst; over Wi-Fi some of it is sent again, and with the browser's
// usual buffer of a tenth of a second the picture stops at every keyframe and then jumps.
// Half a second of buffer covers that.
const BUFFER_MS = 500;
const camera = document.querySelector("video[data-stream-port]");
if (camera) {
    const status = document.querySelector("[data-stream-status]");
    const url = `${location.protocol}//${location.hostname}:${camera.dataset.streamPort}/${camera.dataset.streamPath}/whep`;
    let retry = null;
    const connect = async () => {
        const pc = new RTCPeerConnection();
        const again = () => {
            pc.close();
            status.hidden = false;
            retry ??= setTimeout(() => { retry = null; connect(); }, 3000);
        };
        pc.addTransceiver("video", { direction: "recvonly" });
        pc.addEventListener("track", (event) => {
            event.receiver.jitterBufferTarget = BUFFER_MS;
            camera.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        });
        pc.addEventListener("connectionstatechange", () => {
            if (pc.connectionState === "connected") status.hidden = true;
            if (pc.connectionState === "failed" || pc.connectionState === "disconnected") again();
        });
        try {
            await pc.setLocalDescription(await pc.createOffer());
            // MediaMTX takes the whole offer at once, so wait for the candidates to be gathered.
            await new Promise((resolve) => {
                pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && resolve());
                setTimeout(resolve, 2000);
            });
            const answer = await fetch(url, {
                method: "POST", headers: { "Content-Type": "application/sdp" }, body: pc.localDescription.sdp,
            });
            if (!answer.ok) throw new Error(`${answer.status}`);
            await pc.setRemoteDescription({ type: "answer", sdp: await answer.text() });
        } catch {
            again();
        }
    };
    connect();
}

// A page waiting for a clip that was just asked for looks again until it is there.
const waiting = document.querySelector("[data-reload-after]");
if (waiting) {
    setTimeout(() => location.reload(), Number(waiting.dataset.reloadAfter) * 1000);
}
