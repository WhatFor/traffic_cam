# 0019. Speed: from a map of the road fitted to measured points

Date: 2026-10-06. Status: Accepted. Not yet checked against a vehicle of known speed.

## Context

Vision follows each vehicle's ground point, the bottom centre of its box, in pixels. A speed needs metres. The camera looks down the junction at an angle, so a pixel is worth 3 cm across the image near the camera and 14 cm up it on the far side; no single scale will do.

The build plan stores a 3x3 matrix in `site.yaml` and names one detector, speeding, on the median speed over a segment.

## Decision

**`site.yaml` holds measured points; the mapping is fitted from them at start-up.** `detectors.speed.ground_points` lists points on the road surface with their pixel in the frame and their position in metres east and north of the first. A plane-to-plane mapping is fitted to them when the config loads. Points can be read, checked and corrected; a matrix cannot. Loading fails with fewer than four points, with points nearly in a line, or if any point is more than 1 m from where the rest put it.

**Latitude and longitude never go in the repo.** The points were read off satellite imagery. Coordinates would say where the camera is, so they stay in `calibration/ground_points.yaml`, which is git-ignored. `just calibrate-speed` turns them into metres, prints the block for `site.yaml`, reports each point's error, and writes two images to check by eye: the frame laid flat with a 5 m grid, and the grid drawn back on the camera's view.

**Speed is measured only among the points.** Inside the outline that joins the outermost points the mapping is interpolating between measurements. Outside it, it is extrapolating, and nothing is measured. To measure somewhere, calibrate there.

**A passage's speed is the fastest it held for a second** (`sustained_s`). For every frame, a straight line is fitted to the vehicle's positions over the last second, and its slope is the speed; the highest is kept, with the time of the middle of that second. The fit uses all fifteen positions, so the wobble of a detection box mostly averages out, which the distance between the first and last alone would not do. A second is not used if the track left the area in it, was unseen for more than 0.25 s, or moved faster than 60 m/s between two frames, which is the tracker moving an id to another vehicle.

**A stretch average is kept beside it.** `stretches` names polygons. For a track that passes through one, the average is the straight distance between its first and last positions inside, over the time between, kept if the distance is at least `min_m`. This is what an average-speed camera measures, and it reads low for a vehicle that queued. There is one stretch, across the box junction.

**On the passage:** `speed_kmh` is the sustained speed. `flags.speed` holds `sustained_at` and `stretch_kmh`. The contract and the table already had both fields, so neither changed.

**Speeding** is a detector on the closed passage: a sustained speed above `flag_above_mph` raises `speeding`, dated from `sustained_at`. The limit is 30 mph and the flag is at 35: the limit plus 10% plus 2 mph, which also covers the measurement's own error. Speeds are stored in km/h; the config and the dashboards speak mph.

**A clip rule can have a threshold.** `clips.events.<type>.min` maps an event attribute to the least value that earns a clip (0017). Speeding events get a clip from 45 mph.

## Consequences

- The nine points fit with errors of 0.1 to 0.5 m, 0.3 m root mean square, which is what the imagery allows. The south-east corner of the box, which the camera cannot see, is put by the fit behind the building where it should be. Leaving each point out in turn, the others place it within 0.3 m when it lies among them and 0.7 to 1.5 m when it lies outside: the reason for measuring only among the points.
- Replaying three hours of recorded tracks (2026-10-06, 17:00 to 20:00 UTC): 4,242 of 8,250 passages got a speed. Median 18 mph, 85th percentile 24, 99th 32. Seven were over 35 mph and none over 45; all seven drove straight across from the west arm, and the two looked at frame by frame held their speed steadily before, through and after the measured area.
- Half of the passages have no speed. Most of those are traffic from the east arm's hidden lanes, which crosses only a corner of the measured area. Points on the south side of the junction would bring them in; the two tried there were not in the imagery.
- Taking the highest of many one-second readings picks up some of their noise, so a passage's speed reads perhaps 1 mph high.
- The road is treated as flat and the lens as free of distortion. The tracked point is the bottom of a box, not a fixed point on the vehicle; it moves as the vehicle turns or is partly hidden. Turning traffic is slow, so this matters least where speeds are highest.
- None of this has been checked against a known speed. The build plan's test is a car driven through at a steady, GPS-logged speed, to read within 10%.
- If the camera moves, every pixel in `ground_points` is wrong and so is every speed, with no error raised. The camera-moved check does not exist yet.
- An event at 35 mph says a vehicle was probably speeding. It is not evidence of an offence.
