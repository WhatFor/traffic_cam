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
