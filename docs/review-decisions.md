# Review decisions

What the external reviews of the plan concluded, what was decided, and where each finding stands. The
requests are [review-request.md](review-request.md) and [review-request-2.md](review-request-2.md); the reviews
themselves were delivered outside the repository (23 October 2026).

## Gates (after review 2)

| Step | Decision | Condition |
| --- | --- | --- |
| Experiment 0, geometry and timing audit | **Go** | Compare against identity *and* rotation-only on the same points; check the metric scale with an external measurement (the essential matrix gives no scale); third-view reprojection, not triangulate-and-reproject on the same views. |
| Frozen manifest as a reference for evaluation or training | **No, not yet** | First the target identity (C1), the radar time model (C2), the label definition (C3), calibration validity (I1, done). A provisional manifest may exist, versioned and declared unvalidated. |
| Experiment 1, frozen monocular baseline (Depth Anything V2 Metric VKITTI Small) | **Go** | Narrow camera first, `max_depth=80`, raw metric floats saved (never the normalised picture), fixed preprocessing (RGB/BGR, size, crop, K after resize). Radar metrics only after the manifest gate. |
| Experiment 2, fine-tuning | **No, for now** | A reproducible baseline, development/validation/test separated, a defined output contract, label checks. |
| Experiment 3, cost volume | **No, for now** | Experiment 0 passed and an A/B win on validation with the test untouched. Plane sweep implemented here with standard operators (no ManyDepth/Monodepth2 code). |
| ConvGRU, dense depth to 150 m, full velocities, end-to-end multi-head | **No, for now** | Not before the steps above. |

## Findings

| Finding | Status |
| --- | --- |
| R1-C1 / R2-C1: radar pairing through the model's own distance; the OEM target has no identity or azimuth | **Open.** Plan: candidates from a frozen detector, ego lane from `modelV2` lane lines in image space, only one plausible candidate with a visible, untruncated bottom, stable ≥ 0.5 s, abstain otherwise and around transitions (±0.5 s). Call the result a *radar-associated pseudo-label*, not object ground truth, until short known-target drives check it. |
| R2-C2: the producer gives a CAN receive time; the radar's internal latency is unknown | **Open.** Keep the receive times of 0x2F6 and 0x4F6 apart, model `t_eff = t_rx − L` with L unknown (0 only as an uncalibrated baseline), sensitivity at 0/50/100/150/200 ms, extrapolate at most 25 ms of skew, never across target changes. Needs a change in the producer (cristianku/opendbc) to expose both times. |
| R1-C4 / R2-C3: what the radar distance refers to | **Open, offset measured.** Output named `lead_surface_x_m` (the visible surface of the lead, with its bias), separate from a future `object_center_x_m`. The radar-to-camera offset of the author's car was measured by its owner on 2026-10-23 with a tape from a wall, twice: comma 2.82 m and radar 1.32 m from the wall, then 2.40 m and 0.90 m, so the comma sits 1.50 m behind the radar. The radar sits a little behind the bumper skin: a provisional correction of −3 cm on its reading. Both are settings of the server (Settings › Your car's radar on perception-web, never in the code): distance from the comma = reading − 0.03 + 1.50. A first rough check of the radar's zero, from the slow creep to the wall at 1.3–2 m (CAN replayed through the producer, opendbc 6810f033, because that drive was recorded before the fields existed): the reading was about +0.2 to +0.4 m longer than tape + distance still to travel, with 0.27 m spread (0.25 m steps, near the radar's minimum range); stopped, the radar gives no target; at 0.90 m it saw nothing. First known-distance tests on the new software (1b6a472a, the fields logged), car standing still in front of a wall: radar 1.32 m from the wall by tape, the reading 2.25 m (18 s, steady); radar 0.90 m, the reading 1.75 m (the car moved a little at the end). The radar reads about 0.9 m long: correction −0.89 m (mean of −0.93 and −0.85, ±0.13 m from the 0.25 m steps), so distance from the comma = reading + 0.61 m (2.86 and 2.36 m against 2.82 and 2.40 m by tape). The relative speed was never valid in these tests. Still to check at 5–30 m, where the labels will be. Never a dense loss at the box bottom before that. |
| R2-C4: the audit could not see a wrong scale | **Accepted.** [Experiment 0](experiment-0.md) ran: relative pose passes at the working resolution (third view 1.33 px median at 1344 px, 90 % better than rotation only); timing not observable with this method; scale still to check with an external measurement. |
| R1-C5 / R2-I7: licenses and patents | **Open.** `docs/licenses.md` to list code, checkpoints, datasets and their terms separately; ManyDepth/Monodepth2 excluded; no claim of freedom from patents. |
| R2-I1: a calibration stayed in force after an uncalibrated message | **Fixed** (`comma_log.CommaLog.calibration_at`, tests in `tests/test_comma_log.py`). |
| R2-I2: metrics dropped missing predictions | **Fixed** (`metrics.DistanceErrors`: eligible / predicted / missing, shares within 1/2/5 m over all eligible labels, MAE, MedAE, RMSE, AbsRel, disjoint bands). Coverage of the radar targets comes with the manifest. |
| R2-I3: tuning on the test split | **Accepted:** development for association and calibration, validation for model choice, test for the final comparison only; whole sessions isolated; no test images in photometric fine-tuning. |
| R2-I4: Depth Anything preprocessing | **Done for B0/B1** ([Experiment 1](experiment-1.md)): fixed official preprocessing, pinned code and weights. On the proxy, held-out routes: B0 AbsRel 0.309, B1 0.281 (scale 0.924 from development routes); depth compressed (×1.56 at 10–30 m, ×0.67 at 60–80 m). B2 (focal normalisation) not run. |
| R2-I5, I6: sparse far planes, photometric and uncertainty checks | **Accepted** for Experiment 3 and 2. |
| R2 minor: quantisation of the signals (0.25 m, 1 m, 0.02 m/s), COCO has no van | **Accepted:** continuity tests tolerate quantisation; van is not a supervised class. |

## Data needed before the radar gates

- The car runs an openpilot build with `cristianku/opendbc@6810f033` or later (the `stockRadar*` fields).
- Drives recorded with openpilot longitudinal off.
- The test set: about 15–30 sessions on different days and roads, 100–400 distinct episodes per main band.
  This is a planning figure, to be checked against a pilot. Metrics use bootstrap by session.
