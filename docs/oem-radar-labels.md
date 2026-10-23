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

One radar target is not a 3D map. Its distance labels a detection
([`perception/association.py`](../perception/association.py)) only when, in a calibrated frame:
- the detection is a vehicle (car, truck, bus, van), with enough confidence, inside the ego path
  (|y| ≤ 1.8 m);
- it is the nearest vehicle in the path and the next one is at least 5 m farther;
- no pedestrian, bicycle or other non-vehicle in the path is nearer than the radar distance plus
  the gate (the radar might be following it);
- its vision distance is within max(5 m, 25 %) of the radar distance.

Otherwise there is no label, and the reason is kept. The radar origin is not the camera:
`radar_to_reference_m` (radar distance + offset = x in the calibrated frame) must be given for the
car. openpilot's radard uses 1.52 m for every car (`OPENPILOT_RADAR_TO_CAMERA_M`); it is a generic
value, not a measurement.

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
