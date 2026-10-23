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
| R1-C4 / R2-C3: what the radar distance refers to | **Open.** Output named `lead_surface_x_m` (the visible surface of the lead, with its bias), separate from a future `object_center_x_m`; the radar-to-camera offset measured on the car (±5 cm), then checked at known separations (5/10/20/30 m). Never a dense loss at the box bottom before that. |
| R2-C4: the audit could not see a wrong scale | **Accepted** in the Experiment 0 gate above. |
| R1-C5 / R2-I7: licenses and patents | **Open.** `docs/licenses.md` to list code, checkpoints, datasets and their terms separately; ManyDepth/Monodepth2 excluded; no claim of freedom from patents. |
| R2-I1: a calibration stayed in force after an uncalibrated message | **Fixed** (`comma_log.CommaLog.calibration_at`, tests in `tests/test_comma_log.py`). |
| R2-I2: metrics dropped missing predictions | **Fixed** (`metrics.DistanceErrors`: eligible / predicted / missing, shares within 1/2/5 m over all eligible labels, MAE, MedAE, RMSE, AbsRel, disjoint bands). Coverage of the radar targets comes with the manifest. |
| R2-I3: tuning on the test split | **Accepted:** development for association and calibration, validation for model choice, test for the final comparison only; whole sessions isolated; no test images in photometric fine-tuning. |
| R2-I4: Depth Anything preprocessing | **Accepted** in the Experiment 1 gate; scale variants B0 (zero-shot), B1 (one scale factor fitted on development drives only), B2 (focal normalisation, chosen on validation). |
| R2-I5, I6: sparse far planes, photometric and uncertainty checks | **Accepted** for Experiment 3 and 2. |
| R2 minor: quantisation of the signals (0.25 m, 1 m, 0.02 m/s), COCO has no van | **Accepted:** continuity tests tolerate quantisation; van is not a supervised class. |

## Data needed before the radar gates

- The car runs an openpilot build with `cristianku/opendbc@6810f033` or later (the `stockRadar*` fields).
- Drives recorded with openpilot longitudinal off.
- The test set: about 15–30 sessions on different days and roads, 100–400 distinct episodes per main band.
  This is a planning figure, to be checked against a pilot. Metrics use bootstrap by session.
