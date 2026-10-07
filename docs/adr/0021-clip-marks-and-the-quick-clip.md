# 0021. Marks on clips, kept clips, and the quick clip

Date: 2026-10-07. Status: Accepted.

## Context

The clips site (0018) listed and played clips and could change nothing. Going through a day's clips needs somewhere to say what has been looked at and what it was: seen or not, done with, a description, and that a clip shows nothing real. A clip marked as a false positive is evidence about a detector and should outlast the 30 days the rest get. And there was no way to save what had just happened without a terminal.

Two parts of the system own what this touches. The database rows are ingest's, written from what vision publishes. The files, and the retention that deletes them, are vision's (0017), and vision does not read the database.

## Decision

**Marks are columns on `clips`, written by the site.** `viewed_at`, `archived_at`, `false_positive_at` and `description`. Ingest inserts a clip once and afterwards only sets `deleted_at`, so the two never write the same column. The site writes directly: sending marks round through the broker and ingest would leave a page showing the old state after a click.

**One class writes.** `ClipMarks` has a connection of its own. Everything that lists and plays still reads through the connection the server holds read-only.

**What each mark does.**

- *Viewed*: set when a clip's page is opened. In a list a viewed clip's still is grey and faded; one not yet viewed has a red dot by its time.
- *Archived*: an archived clip is left out of every list and count unless "Include archived clips" is ticked. That choice is a cookie, so it holds from page to page.
- *Description*: one per clip, up to 500 characters. In a list it takes the place of what the event recorded; the clip's page keeps both.
- *False positive*: for the record, and it keeps the files (below).

All four are per clip. A clip recorded for two events carries the same marks in both lists.

**The buttons are on the clip's page.** A clip is opened, watched and then dealt with. Lists stay plain links.

**Vision is told over the broker, as it already was for a manual clip.** The site publishes; it has no other way to reach vision and still mounts the clips folder read-only.

- A false positive publishes `ClipKeep` to `trafficcam/v1/cmd/keep/<clip id>`, retained, before the column is written. If the broker does not take it the page says so and nothing changes, so the site never shows a clip as kept that vision was not told about. Retained, so vision hears it at its next start if it is down.
- Vision writes an empty `<clip id>.keep` beside the clip, or removes it. Retention reads that file and nothing else: a kept clip is deleted only when older than `kept_days` (183) and is passed over when the folder is over its size. Its bytes still count, so the folder stays within `max_gb` at the cost of clips that are not kept.
- When a kept clip is finally deleted vision clears the retained message.

**The quick clip is a manual clip with its own lengths.** `ClipCommand` gained optional `pre_s` and `post_s`. The button asks for 90 s before and none after: what the ring holds, ending now. On the Pi a 90 s clip of 74 MB was complete, with its still and its record, 0.8 s after the press. The browser is sent to the manual clips, which look again every two seconds for half a minute until it is listed.

**A manual clip's link opens it at the start.** An event's link opens its clip two seconds before the event. A manual clip's moment is when it was asked for, which for a quick clip is its last frame.

**Still no login.** The forms carry the framework's anti-forgery token, so another site open in the same browser cannot post them.

## Consequences

- Anyone on the local network can now archive, describe and flag clips and ask for new ones, as well as watch them. The port must still never be forwarded.
- Opening a clip's page changes the database on a GET. A browser that fetches links ahead of a click would mark clips viewed.
- There is no way to mark a clip unviewed again.
- A flag set while vision is down reaches it when it next connects, which is after that start's first retention pass. A clip due for deletion at exactly that pass would go.
- The column and the retained message are written one after the other, not together. If the database fails after the broker has accepted the message, the files are kept and the page does not say so.
- Every clip ever flagged and not yet deleted is told to vision again on each connection. At a few a day that is nothing.
- If kept clips alone came to more than `max_gb`, every other clip would be deleted as soon as it was written. At 15 MB a clip that is 13,000 of them.
- The site needs the camera's id to address vision. It is set in `compose.yaml` and must match `camera.id` in `site.yaml`; if it does not, vision ignores what the site sends and a quick clip never arrives.
- False positives are recorded and not yet counted anywhere.
- The first quick clip took 16 s to appear. The still frame's seek had always landed on the start of the clip, through a division where a multiplication was meant, and the frame was reached by decoding everything before it. That went unnoticed while the moment was 5 s into a clip; here it is the last frame. Fixed with this change.
