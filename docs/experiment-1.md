# Experiment 1: the frozen monocular baseline

Depth Anything V2 Metric VKITTI Small (24.8 M parameters, Apache-2.0), inference only, on the road camera:
the baseline every later model has to beat ([review decisions](review-decisions.md)). Code:
[`perception/models/depth_anything.py`](../perception/models/depth_anything.py) (pinned code and weights) and
[`perception/experiments/mono_baseline.py`](../perception/experiments/mono_baseline.py).

```bash
flock -n /run/lock/v100-gpu.lock uv run python -m perception.experiments.mono_baseline data/<segment> [...] \
    --out report.json --pictures outputs/perception/experiment-1
```

## Setup

- **Model**: code at commit `a561b849`, weights at revision `c725b858` (SHA-256 checked).
  - The official preprocessing: BGR uint8, the shorter side resized to 518 px with the aspect ratio kept,
    multiples of 14. A 1344×760 frame becomes 910×518, so the network sees a focal of 773 px (VKITTI ≈ 725).
  - `max_depth` 80 m; raw float metres, never a normalised picture.
- **Hardware**: a V100 under the shared GPU lock, 67 ms per frame, 405 MB.
- **Reference: a proxy, since no rlog has radar yet.**
  - Static points of frame a are triangulated from a and a + 10 frames (0.5 s) with the localizer poses.
  - A point is kept only with parallax ≥ 1.5°, reprojection < 1 px in the second frame, and triangulation
    uncertainty ≤ 5 % (σZ/Z ≈ Z·0.5 px / (f·baseline across the ray)).
  - Its depth must be within 2–80 m, and the car must be moving at ≥ 5 m/s.
  - The model's depth is the median of a 5×5 window around the keypoint.
- **Data**: the 29 segments of Experiment 0, one pair every 1.5 s, from 7 drives (routes).
- **Split**: 4 routes for development (B1's scale only), 3 held out.

## Results (23 October 2026)

| | points | AbsRel | MAE | median model / reference | δ < 1.25 |
| --- | ---: | ---: | ---: | ---: | ---: |
| B0, all routes | 11 455 | 0.304 | 12.7 m | 1.072 | 0.465 |
| B0, held-out routes | 6 494 | 0.309 | 13.0 m | 1.066 | 0.468 |
| **B1, held-out routes** (scale 0.924 from development) | 6 494 | **0.281** | 12.7 m | 0.985 | 0.497 |

By band, on the held-out routes:

| band | points | B0 AbsRel | B0 model / ref | B1 AbsRel | B1 model / ref | B1 δ < 1.25 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2–10 m | 0 | – | – | – | – | – |
| 10–30 m | 1 078 | 0.606 | 1.56 | 0.491 | 1.44 | 0.27 |
| 30–60 m | 3 891 | 0.227 | 1.10 | 0.196 | 1.01 | 0.66 |
| 60–80 m | 1 525 | 0.309 | 0.67 | 0.351 | 0.62 | 0.23 |

## Reading

- **Zero-shot, the baseline is weak on this camera.** The error is about 30 % of the distance, and it has a
  shape: near points come out too far (×1.5 at 10–30 m), far points too near (×0.67 at 60–80 m). The depth
  is compressed towards the middle.
- **One scale factor (B1) helps only a little**: AbsRel 0.309 → 0.281. It centres the 30–60 m band (×1.01)
  but cannot undo the compression, so the bands move apart. What is wrong is the shape, not the scale.
- **Pictures** (kept out of git: frames of real drives):
  - the sky comes out at a middle distance;
  - the end of the road and distant hills saturate at 80 m;
  - the road surface has a plausible gradient;
  - the car's hood is in the bottom of the frame and must be masked in any later loss or metric.
- **What the proxy cannot tell, and how it may bias the result**:
  - **Scale**: it rests on the localizer's metric scale, not checked yet (Experiment 0).
  - **Selection**: few near points pass the filters; the road has little texture and moves a lot in 0.5 s. No
    point is under 10 m.
  - **Edges**: keypoints sit on corners, where depth jumps, so a 5×5 median can still take the background;
    this may push the near band up.
  - **Motion**: RANSAC drops most moving points, not necessarily all of them.
  - **No lead**: the radar check of the review is still to come.

## Gate

| Check | State |
| --- | --- |
| B0 (zero-shot) and B1 (one scale from development drives) measured, preprocessing fixed | **done**, on the proxy |
| Radar metrics on a frozen manifest | **open**: no radar data yet; the manifest gate is still closed |
| Experiment 2 (fine-tuning) | still **no**: a reproducible baseline exists now; the development / validation / test split by drive, the output contract and the label checks come first |

What this changes: a single-frame model taken as it is does not give reliable metres on the comma's camera.
Training on these drives is needed (photometric and temporal on static pixels, radar on the lead), and it now
has a measured starting point.
