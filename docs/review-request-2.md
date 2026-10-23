# Review request 2: the revised plan after the first review

A second critical review. The [first request](review-request.md) proposed a temporal multi-frame network. Its
review (23 October 2026) answered:
- **GO** for a monocular metric baseline, a radar validated independently, and pose diagnostics;
- **CONDITIONAL GO** for a small cost volume, after a reproducible A/B test;
- **NO-GO** for now for the ConvGRU, dense depth to 150 m, full velocities and an end-to-end multi-head model.

This document says what was accepted and checked, and gives the revised plan in enough detail to review the
designs before they are written. Please look for mistakes, missing cases and hidden biases; do not just
confirm.

## What to read

- [`review-request.md`](review-request.md): the first request (context, constraints, original architecture).
- [`docs/oem-radar-labels.md`](oem-radar-labels.md) and the code it describes: today's radar labels, whose
  pairing rule the first review found circular (finding C1 below).
- `cristianku/openpilot.distill`: `localizer/README.md` (localizer accuracy), `localizer/logs.py` (frame
  times, calibration), `helpers/video_helpers.py` (decoding).

## The findings of the first review, and the answers

| # | Finding | Answer |
| --- | --- | --- |
| C1 | The radar is paired with a detection through the distance the model itself predicts (`gate_abs_m`, `gate_rel` in `association.py`): training and metrics favour the cases where the model was already right. | **Accepted.** The lateral test uses the model's estimate too. Replaced by the depth-independent pairing below; the vision gate becomes a diagnostic only. |
| C2 | A cost volume assumes a static scene: a car at our speed shows little or no parallax. | **Accepted.** Static depth and dynamic objects are separated; multi-frame only after the A/B test. |
| C3 | Relative pose accuracy and timing are not measured; the 0.449 m of the localizer is an absolute-trajectory error. | **Checked and accepted.** `localizer/README.md` reports 0.449 m median 3D position, 0.396 m longitudinal, 0.071 m lateral, 0.117 m/s velocity, all against a comma1M reference trajectory, so nothing frame to frame. Experiment 0 measures the relative pose. |
| C4 | The radar gives the longitudinal distance of one target, not a depth map; the 1.52 m offset is generic. | **Accepted.** The label is renamed `range_x_m` (longitudinal, car frame), never spread over a box; the radar-to-camera offset will be measured on the car. |
| C5 | ManyDepth and Monodepth2 are non-commercial. | **Checked, and worse than stated:** both LICENSE files say "All rights reserved. Patent Pending". No code from them; whether an independent implementation of the method is free is a legal question (see question 9). |
| I1–I7 | Simplified lane model; radar–frame skew; fragile road-contact labels; COCO classes vs project classes; radar-only metrics; velocities; small data and a shared V100. | **Accepted.** Each is addressed in the plan below. |

Licenses checked on the model cards: Depth Anything V2 Metric VKITTI Small is **Apache-2.0**. The project
README says Base/Large are CC-BY-NC-4.0 even where the Hugging Face card says otherwise; only Small is used.

## Facts checked on the data since

- `modelV2` is in every rlog, also in dashcam mode, at 20 Hz.
  - It carries 4 lane lines (33 points, 0–192 m ahead) with their probabilities, road edges and `leadsV3`.
  - On a sample urban segment the two ego-lane lines have median probability 0.99 and 0.97.
  - This is openpilot's model, not the model under test: it can serve as an independent lane source.
- Per segment: 1200 frames per camera, calibrated after the first few frames; `deviceMotion` (speed, yaw rate)
  at 20 Hz.
  - The yaw-rate sign was checked against heading change (+0.998 correlation) and steering angle (−0.981).
- No rlog has radar yet. The car port logs the `stockRadar*` fields from `cristianku/opendbc@6810f033`, but the
  openpilot build installed on the car does not include that commit yet.
  - New drives with openpilot longitudinal off will be the first radar data.
  - The 145 existing segments have no radar.

## The revised plan

### A. Depth-independent radar pairing (fixes C1, I1, I2)

For each radar sample `(range_x_m, v_rel, t_meas)` and the camera frame nearest in time, a pair is made only
when **every** step below holds. None of the steps uses the depth of the model under test.

1. **Candidates**: vehicle boxes (car, truck, bus, motorcycle) from a frozen 2D detector. The detector
   (torchvision, BSD-3) and its weights are pinned by checksum. Each box gets a track id from an IoU/appearance
   tracker over the previous frames.
2. **In the ego lane, from the image**: the bottom-centre of the box (its footpoint) must lie between the two
   ego-lane lines of `modelV2`.
   - The lines are projected into the image with the logged calibration and intrinsics.
   - The test is made at the footpoint's image row, so it needs no depth at all.
   - When the lane-line probabilities are low (below 0.5), the corridor from yaw rate and speed is used
     instead, and only on a straight road.
3. **The nearest**: among the vehicles in the lane, the one whose footpoint is lowest in the image. Perspective
   orders the vehicles on the road without any distance.
4. **Unambiguous**: no label in any of these cases:
   - another vehicle's footpoint within a few rows of the chosen one;
   - a vehicle box crossing a lane line near the chosen row (a cut-in);
   - a pedestrian or bicycle box between the chosen vehicle and us.
5. **Stable in time**: the same track stays chosen for at least N consecutive frames, and the radar range stays
   continuous. A jump larger than what `v_rel × Δt` allows means a target switch, and the window around it
   is dropped.
6. **Time**: the radar range is moved to the frame time with `range + v_rel × (t_frame − t_meas)` when `v_rel`
   is valid, after its sign is checked on data; otherwise only |skew| ≤ 10 ms. The skew is kept with its sign.

Quality checks, logged but never used to include or drop a pair:
- the ground-plane distance of the footpoint (`height / tan(angle below the horizon)`) against the radar range;
- the box height in pixels against the radar range (perspective);
- later, the distance from the model under test.

Each pair goes into a **frozen manifest** (one line per pair, JSON). It records:
- segment, frame index and frame time;
- `measurement_id`, `range_x_m`, `v_rel`, skew;
- box, track id, number of candidates;
- detector name and checksum, `association_version`, `association_reason`.

Training and evaluation read the manifest only. **Coverage** (the share of frames with a radar target that get
a label) is published with every result, by speed, curvature and distance band.

### B. Label definition (fixes C4)

- `range_x_m` = radar range + radar-to-camera longitudinal offset, in the calibrated car frame.
- The offset is measured on the Peugeot 3008: the radar is behind the front grille, the camera behind the
  windshield. Meanwhile it is a declared parameter, never the generic 1.52 m.
- The label belongs to the object (its `x`), never copied to the pixels of its box. A dense loss may use it
  only at the footpoint, through the projection geometry.

### C. Metrics and splits (fixes I5)

- Lead range from the frozen manifest:
  - MAE, MedAE, AbsRel and RMSE;
  - the share within 1, 2 and 5 m;
  - all of them by band (0–10, 10–30, 30–60, 60–80, > 80 m), by speed, by curvature and by skew;
  - always with the coverage.
- Static geometry: reprojection error on held-out frames, multi-view consistency. These are proxies, reported
  as such.
- Dense metric depth: a small external benchmark with lidar ground truth whose license allows evaluation.
  This is for evaluation only, never training.
- Splits by drive (route and session), never by frame or by neighbouring segments of the same drive. The test
  split and its manifest are frozen before any model is compared.

### D. Experiment 0: geometry and timing audit, no training (fixes C3)

1. **Unit tests**:
   - synthetic SE(3) poses, with source→target order and composition;
   - non-symmetric K, and K after resize/crop;
   - narrow and wide camera;
   - the sign of yaw rate and lateral position.
2. **Relative pose accuracy**.
   - **Method**: match features between frames t and t−k (ORB or SIFT with OpenCV, Apache-2.0); estimate the
     essential matrix with RANSAC; compare its rotation, and the direction of its translation, with the
     localizer's relative pose.
   - **Statistics**: the residual of static matches, using the localizer pose and depth from triangulation.
   - **Breakdowns**: speed, Δt, braking, turns, bumps, GNSS quality, narrow and wide camera.
3. **Timing**.
   - Estimate the frame-time offset that minimises the epipolar residual, searching ±30 ms around
     `timestampEof − 8 ms`.
   - Estimate the rolling-shutter line time from residuals that grow with image row during rotation.
4. **Dynamic pitch**: the gap between the per-frame pitch from the IMU and the averaged calibration, under
   braking and over bumps.

Gate: the localizer poses must reduce the residuals clearly against no motion compensation. If they do not,
the cost volume is not built.

### E. Experiment 1: frozen monocular baseline

- Depth Anything V2 Metric VKITTI Small (24.8 M parameters, Apache-2.0), inference only, at a fixed resolution.
- The intrinsics are handled explicitly: see question 4.
- The same frozen detector as in A.
- Metrics as in C, on the frozen radar test split as soon as the first radar drives exist. Until then:
  qualitative checks and static reprojection consistency on the existing segments.

### F. Then

- **Experiment 2**: monocular fine-tuning at a low learning rate.
  - Losses: photometric and temporal consistency on static, geometrically valid pixels only; sparse radar
    labels on the object range; masks and uncertainty.
  - Uncertainty with a calibrated NLL loss, not a free head.
- **Experiment 3**: A/B of a small cost volume (2 frames, 32 planes, reduced resolution, no GRU) against the
  frozen and fine-tuned baselines, on the same split.
  - Promoted only if it improves the independent radar error or the static geometry on unseen drives,
    without making standstill, dynamic objects, latency or VRAM clearly worse.
  - Reference frames chosen by measured baseline, overlap and pose quality, not fixed at t−1, t−2.
- **Range**: 2–80 m first; 150 m is not a target for now.
- **Compute**: FP16 mixed precision, small effective batch, the GPU lock shared with openpilot.distill, VRAM and
  throughput measured before choosing sizes.
- **Licenses**: a `docs/licenses.md` inventory of code, checkpoints, datasets and redistribution rights.

### G. Radar data collection

The openpilot build of the car will move to the opendbc commit with the `stockRadar*` fields, and drives will
be recorded with openpilot longitudinal off. The test split is set aside from the first drives and never used
to tune thresholds.

## Questions

1. **Pairing.** Is the depth-independent pairing in A sound?
   - Is ordering by footpoint row safe on hills, at crests and with occlusions?
   - Is `modelV2`'s lane geometry independent enough of the model under test? It is another vision model.
   - What is missing for target switches and cut-ins?
2. **Diagnostics.** Is the ground-plane check acceptable as a logged diagnostic, or does any use of it bias the
   manifest? Is box height a better check?
3. **Relative-pose audit.** Is the method in D right? Which acceptance criteria would you derive from the
   residuals?
4. **Metric depth on another camera.** Depth Anything V2 Metric was trained on VKITTI intrinsics. How should
   its metric output be adapted to the comma camera's focal length: rescaling by focal ratio, a
   canonical-camera transform like Metric3D, or a fit on a calibration subset? What changes for the wide
   camera?
5. **Time.** Is the time model in A6 (extrapolation with `v_rel`, else |skew| ≤ 10 ms) correct? Which latency
   of the OEM radar should be assumed until it is measured?
6. **Data volume.** How many radar-labelled drives, or hours, does a test set need to give useful confidence
   intervals on MAE per distance band? How should a split by drive be made with only a few hours?
7. **The radar offset.** How should the radar-to-camera offset be measured on the car, and what accuracy is
   needed for metrics within 1 m?
8. **Teacher detector.** Class mapping COCO → project (van, motorcycle), thresholds per class, ignore regions:
   what would you change? Is a tracker needed before Experiment 1?
9. **Multi-frame without the patents.** Given "Patent Pending" on ManyDepth/Monodepth2:
   - which multi-frame or self-supervised depth methods with permissive licenses and no patent claims would you
     consider for Experiment 3?
   - or is a plane-sweep cost volume with known poses generic enough (classic multi-view stereo) to be free of
     them?
10. **Gates.** Is anything in the order or the gates of A–G wrong, or missing before the first training?

## Answer format

- Problems by severity (critical / important / minor), each with its reason and a concrete fix.
- For questions 1, 4 and 6, a concrete recommendation with numbers where possible.
- References (papers, code), with their licenses.
- A yes/no on each gate: Experiment 0, the frozen manifest, Experiment 1.
