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

// A page waiting for a clip that was just asked for looks again until it is there.
const waiting = document.querySelector("[data-reload-after]");
if (waiting) {
    setTimeout(() => location.reload(), Number(waiting.dataset.reloadAfter) * 1000);
}
