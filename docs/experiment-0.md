# Experiment 0: geometry and timing audit

How well the poses of openpilot.distill's localizer explain the motion the road camera sees: the gate before a
multi-frame network relies on them ([review decisions](review-decisions.md)). No training, no GPU.
Code: [`perception/audit/geometry_audit.py`](../perception/audit/geometry_audit.py); synthetic tests in
`tests/test_geometry_audit.py`.

```bash
uv run python -m perception.audit.geometry_audit data/<segment> [...] --jobs 5 --out report.json
```

## Method

- **Camera**: road camera at 1344×760 (os04c10), focal 1141.5 px, principal point at the centre
  (openpilot's `DEVICE_CAMERAS`).
- **Poses**: the localizer's 100 Hz states (position ECEF, quaternion `ecef_from_device`), interpolated at the
  frame times (`timestampEof − 8 ms`). The device frame is turned to the optical one (x right, y down,
  z forward).
- **Triplets**: frames a, b = a + 2, c = a + 4 (100 ms apart), one triplet every second.
- **Matching**: SIFT matches a↔b with a ratio test, static ones kept by RANSAC on the essential matrix
  (OpenCV, 1 px).
  - **Strict** pairs: at least 100 inliers in at least 8 cells of a 4×4 grid.
  - **Usable** pairs: at least 50 inliers.
- **Rotation and direction of translation**: from the essential matrix, compared with the localizer's relative
  pose. The essential matrix has no scale.
- **Third view**: a↔b inliers triangulated with the localizer poses, projected into c, compared with the
  observed points in c (tracked b↔c). The same points are also predicted with no motion (identity) and with
  rotation only (infinite depth).
- **Timing**: the median rotation error against a shift of the frame times from −60 to +60 ms, on the strict
  pairs, segment by segment.

## Results (23 October 2026)

29 segments, one in five of the 145 local segments, spread over all the drives: 1740 triplets.

| | strict (839 pairs) | usable (1422 pairs) |
| --- | --- | --- |
| rotation error, 100 ms | 0.071° median, 0.161° P90 | 0.075°, 0.183° |
| localizer rotation over the same 100 ms | 0.147° median, 0.508° P90 | 0.136°, 0.484° |
| direction of translation error | 1.78° median, 4.09° P90 | 1.90°, 5.42° |
| third view, identity | 14.8 px median, 27.5 px P90 | 14.5, 29.2 |
| third view, rotation only | 13.8 px, 24.7 px | 13.9, 26.1 |
| **third view, localizer pose** | **1.33 px, 2.74 px** | 1.36, 3.01 |
| gain over rotation only (median) | 90 % | 90 % |

Third view with the localizer pose, by speed (strict):
- 3–10 m/s: 1.14 px median, 2.74 P90;
- 10–20 m/s: 1.28 px, 2.56;
- above 20 m/s: 1.58 px, 3.09.

Pixels are at 1344×760; at a 640-pixel-wide working resolution they are about half (median ≈ 0.63 px, P90
≈ 1.3 px), and a quarter of that on a 1/4-resolution feature map.

## Reading

- **Relative pose: passes** the initial criteria of review 2 at the working resolution: median ≤ 1 px and
  P90 ≤ 2 px, and at least 30 % gain over the best of identity and rotation only. At the full 1344-pixel
  resolution the P90 (2.7 px) would not.
  - The criteria were starting points. They are kept as they were, not moved to fit the result.
- **Rotation**: 0.07° in 100 ms is about 1.4 px at full resolution. It is the larger part of the third-view
  error at low speed.
- **Timing: not observable with this method.**
  - The best shift moves from −60 to +60 ms between segments (median +15 ms, three at the border).
  - The curves are almost flat (median spread 0.026°).
  - A constant offset changes the relative pose only when the rotation rate changes, which these drives do
    little. A dedicated test is needed: rotation-rich manoeuvres, or a known event seen by the camera and the
    sensors.
- **Scale: not checked.** Projections do not change when the translation and the points are both scaled.
  The metric scale needs an external measurement (a known distance driven or seen), still to do.
- **Limits**:
  - Only the road camera; the wide one needs its distortion model first.
  - Static matches come from RANSAC: slow traffic moving with us may remain among the inliers.
  - The frame times and the poses come from the same logs: this checks that they agree with the images, not
    that the log clock is right in absolute terms.

## Gate

| Check | State |
| --- | --- |
| Relative pose against identity and rotation only, third view | **passed** (strict and usable alike) |
| Timing offset | **not observable** here; open |
| Metric scale | **open**: needs an external measurement |
| Cost volume (Experiment 3) | still **no**: after the scale check, the frozen baseline and an A/B on validation |
