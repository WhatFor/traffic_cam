# 0018. The clips site: a read-only web app of its own

Date: 2026-10-06. Status: Accepted. No longer read-only since 0021: it keeps marks on clips and asks vision for things.

## Context

Clips are files on the Pi, each with a row in `clips` saying what it was recorded for (0017). Watching one meant copying it off the Pi. The build plan had ingest serve the files, for links in notification emails; it had no pages for browsing them.

## Decision

**A separate service, `web`.** `TrafficCam.Web` is a third project in the .NET solution under `ingest/`, published and run exactly as ingest is (0006): built on the PC into `deploy/web/`, run from the stock ASP.NET image as a Compose service. It is not part of ingest because the two have nothing in common but the database: ingest must not lose messages and stays on loopback, while this is pages for people, open to the network, and changes for different reasons.

**Razor Pages, rendered on the server.** No JavaScript build and no front-end packages. One stylesheet and one script of a few lines, which puts times into the reader's time zone and makes the "jump to" buttons work.

**The pages.**

- `/`: one column per trigger type that has clips, each with its ten newest entries. A new event type gets a column without a code change.
- `/types/{type}`: the whole of one type's list, fifty to a page, with older pages reached by time.
- `/clips/{id}`: the player, with everything the clip was recorded for: type, time, the event's detail and a button to jump to that moment.

An entry is one trigger of one clip, so a clip recorded for two things is in two lists. Each link opens the clip two seconds before its own moment.

**Playing is the browser's video element over HTTP range requests.** Clips are MP4 with the index first, so a browser plays one as it arrives and fetches only the part it seeks to. Nothing is transcoded and there is no streaming server.

**Files are found by clip id only.** The path is the one in the clip's row, and it is served only if it resolves to a file inside the clips folder. Nothing in a request is ever used as a path. A deleted clip's files are not served and its page says when it went.

**Read-only twice over.** The database connection is opened with `default_transaction_read_only=on`, and the clips folder is mounted read-only, at the same path as on the host so the paths in the database need no translation. Since 0021 this holds for the pages' reading and for the folder; the marks on a clip are written through a second connection used by one class.

**No login.** The site is on port 8081 of the Pi, reachable from the local network and the tailnet, and from nowhere else: nothing is forwarded on the router.

## Consequences

- Anyone on the local network can watch every clip. That is acceptable only while the network is a private one. Clips show identifiable vehicles and people, so this port must never be forwarded or proxied to the internet as it is.
- It connects to the database as its owner, as ingest and Grafana do; the read-only setting is a guard against mistakes in this code, not against an attacker who has the password.
- Lists use the full-size still (about 200 kB) as each thumbnail, loaded as it scrolls into view. A page of fifty is about 10 MB on first view. Small thumbnails would need either an image library here or a second file from vision.
- Paging by time can skip an entry that shares its exact time with the last one on a page.
- It starts after ingest is healthy, because ingest creates the tables. It has no metrics and no alert: Compose restarts it, and its health check asks the database a question once a minute.
- The health-check mode and the database options are copies of ingest's, a dozen lines each, rather than a shared library for two services.
- Notification emails (build plan, Phase 7) can link to `/clips/{id}`.
- The solution's folder is still called `ingest`, though it now holds two services.
