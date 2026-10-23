# The car's radar as a depth label

How openpilot.perception uses the lead measurement of the car's own (OEM) radar, logged by the car
port in `carState`, as metric supervision for the distance of detected objects. Nothing here talks
to the car: no model output is connected to vehicle control.

## Contract with the producer

The car port (today `cristianku/opendbc`, branch `psa-torque-sunny-testing`, commit `6810f033`,
`opendbc/car/car.capnp` @62–66) fills these `carState` fields:

| Field | Meaning |
| --- | --- |
| `stockRadarLeadDistance` | m, longitudinal gap to the lead, from the radar |
| `stockRadarLeadRelativeSpeed` | m/s |
| `stockRadarLeadValid` | the distance is a fresh measurement of a target |
| `stockRadarMeasurementTimeNanos` | monotonic time of the original measurement (`logMonoTime` clock) |
| `stockRadarRelativeSpeedValid` | the relative speed is valid too (in the schema, not in the first contract) |

The producer only fills them from frames the radar itself sent, and not while openpilot
longitudinal may replace the radar with frames built from the vision model.

This repository never decodes CAN and knows no car maker: it reads `carState` only
([`perception/radar/oem_lead.py`](../perception/radar/oem_lead.py)).

## When the radar is read

| Log | Result |
| --- | --- |
| openpilot built with an opendbc whose `car.capnp` has no `stockRadar*` fields (openpilot.distill today) | radar off, vision-only |
| rlog recorded before the fields existed, read with the new schema | every message reads as not valid: vision-only |
| rlog of a car port that fills the fields | radar samples |

Checked on a real segment (`…a09`, recorded in dashcam mode before the fields existed): with the
current schema "schema without the stockRadar fields"; with the producer's `car.capnp`, 5974
`carState` messages, all "no valid target".

Replaying the CAN of the same segment through the producer's `CarState` (`6810f033`, dashcam
CarParams) and reading the result with this consumer: 2605 radar measurements, all identical in
time and distance to an independent decoding of the same frames, 2599 with a relative speed,
1042 of 1200 fcamera frames aligned (skew at most 23.9 ms, 5.1 ms on average; the others are
frames without a target).

## Rules for a radar sample

For each `carState`, a sample is kept only when:
- `stockRadarLeadValid` is true;
- the measurement time is set, not after the message itself, and at most **300 ms** older than it;
- the distance is finite and within **0.5–250 m**;
- the measurement time does not go back; `carState` at 100 Hz repeats a slower radar, so a repeated
  measurement time is read once.

The relative speed is kept only with `stockRadarRelativeSpeedValid`, finite and within ±70 m/s;
without that flag in the schema it is not trusted. These are consumer bounds, the same for any car.

## Alignment to the frames

Frames of `fcamera.hevc` (`narrowRoadEncodeIdx`) and `ecamera.hevc` (`wideRoadEncodeIdx`) have
time `timestampEof − 8 ms`, as the localizer of openpilot.distill. Each frame takes the radar
sample nearest in time, within **25 ms** (on a tie the earlier one); the skew is kept with the
sample. Nothing is interpolated. Calibration (`extrinsicsCalibration`, `calStatus = calibrated`) is
the last one logged before the frame; before the first one a frame has none and gets no label.

## Pairing the radar with a detection

> **Status: known bias, not yet replaced.** The rule below admits a pair only when the vision distance of
> the detection is within a gate of the radar, and takes the lateral position from the vision estimate too:
> training and metrics then favour the cases where the model was already right (review 1, C1). A pairing
> that uses no depth of the model under test is planned ([review decisions](review-decisions.md)); until
> then these pairs are not a reference for evaluation.


The radar is an extra check, used only for the object straight ahead in the ego lane: one target is
not a 3D map, and nothing beside the car or in another lane ever gets its distance. It labels a
detection ([`perception/association.py`](../perception/association.py)) only when, in openpilot's
calibrated frame (x forward, y right, z down):
- the car's path is known: speed and yaw rate from `deviceMotion` (the comma device's own sensors,
  at most 100 ms old), curvature = yaw rate / max(speed, 3 m/s), positive turning right (checked on
  a real segment: +0.998 correlation with the heading change, −0.981 with the steering angle);
- the road ahead is not curving away: the lane centre at the radar distance, `curvature · x² / 2`,
  is within 1 m of straight ahead;
- the detection is a vehicle (car, truck, bus, van), with enough confidence, within 1.5 m of the
  lane centre at its distance;
- it is the nearest vehicle in the lane and the next one is at least 5 m farther;
- no vehicle in the next lanes (up to 4 m beyond the ego lane, both sides) is near the radar
  distance: a car cutting in, or a neighbour the radar might be following;
- no pedestrian, bicycle or other non-vehicle is in the lane before the radar distance, or near the
  radar distance in the lane or the next lanes;
- its vision distance is within max(5 m, 25 %) of the radar distance.

Otherwise there is no label, and the reason is kept. On a real urban segment (`…a09`, 1198 frames
with motion) the curve rule leaves the label possible in 94 % of the frames for a target at 10 m,
61 % at 30 m and 18 % at 60 m.

The radar origin is not the camera: `radar_to_reference_m` (radar distance + offset = x in the
calibrated frame) must be given for the car. openpilot's radard uses 1.52 m for every car
(`OPENPILOT_RADAR_TO_CAMERA_M`); it is a generic value, not a measurement. The site keeps the measured
values of the car as settings of the server (Settings › Your car's radar): how far the comma sits behind
the radar along the car, and a correction of the radar's own reading (its zero, from tests at known
distances). Distance from the comma = reading + correction + distance radar → comma. On the author's car
the comma is 1.50 m behind the radar (measured from a wall) and the correction is −3 cm for now (the
radar sits behind the bumper skin): a reading of 20.00 m is 21.47 m from the comma. The correction is
checked at known separations before any radar distance becomes a label.

## Kept apart

[`perception/provenance.py`](../perception/provenance.py) tags every value: `oem_radar` (measured),
`vision` (estimated), `fused`, `ground_truth` and `synthetic` (tests). Only `oem_radar` makes a
training target (`dataset.depth_targets`; synthetic only with `allow_synthetic` for pipeline tests)
and only `oem_radar` errors are the headline metrics (`metrics.DistanceErrors.measured()`).

## Training and display

- `dataset.SegmentAdapter`: one sample per frame (frame, calibration, aligned radar, status); the
  image is decoded by the caller.
- `losses.masked_depth_loss`: log-L1 (or L1, smooth L1) over the valid targets only.
- `metrics.DistanceErrors`: absolute and relative error, associated pairs only, by provenance.
- `viz.object_rows` / `viz.table` / `viz.draw`: for each object the vision distance, the radar
  measurement if paired, the difference in metres, validity and the source of each value; synthetic
  data is marked "SYNTHETIC TEST DATA".
