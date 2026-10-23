# Review request: temporal 3D perception from video

A critical technical review of the plan below. Look for mistakes, risks and better alternatives; do not just
confirm it.

## What to read

Public repositories on GitHub:

- `cristianku/openpilot.perception` (branch `main`): the project under review.
  - [`docs/oem-radar-labels.md`](oem-radar-labels.md): contract and rules of the radar labels.
  - [`perception/radar/oem_lead.py`](../perception/radar/oem_lead.py): the radar read from `carState`, its
    filters, alignment to the frames.
  - [`perception/association.py`](../perception/association.py): pairing the radar target with a detection.
  - [`perception/sources/comma_log.py`](../perception/sources/comma_log.py): frames, calibration and motion
    from the comma logs.
  - [`perception/dataset.py`](../perception/dataset.py), [`perception/losses.py`](../perception/losses.py),
    [`perception/metrics.py`](../perception/metrics.py): dataset adapter, loss, metrics.
- `cristianku/openpilot.distill` (branch `master`), which mounts this repository as a submodule:
  - `localizer/logs.py`: frame times and calibration.
  - `localizer/README.md`: what the localizer produces (a pose per frame).
  - `helpers/README.md`, `helpers/video_helpers.py`: video decoding with NVDEC.

## Context

- **Device**: a comma four (openpilot/sunnypilot) on a Peugeot 3008.
  - A main road camera and a wide one, 20 Hz; segments of 60 s (1200 frames).
  - Camera intrinsics known; camera-to-car calibration (roll/pitch/yaw) known for every frame; camera height
    about 1.24 m.
- **Poses**: for every frame an offline localizer gives the metric pose of the camera.
  - Position from corrected GNSS; orientation and velocity from fusion with the IMU (`localizer.safetensors`,
    `frame_states` 1200×43).
  - Frame time = end of exposure - 8 ms, the clock of the logs.
- **Data**: about 145 segments (2.4 h) without radar. New drives will carry the lead distance measured by the
  car's own radar.
  - One target only: longitudinal distance and relative speed.
  - Read passively from `carState`.
- **Compute**: one Tesla V100 32 GB, shared with another training.
- **Constraints**:
  - no lidar and no manual annotations;
  - permissive licenses only (no AGPL, no non-commercial weights for anything published);
  - the code knows no car maker;
  - no output connected to vehicle control;
  - reproducible installation.

## Goal

A network that recognises objects (cars, trucks, buses, pedestrians, bicycles, motorcycles) and estimates
their 3D position in metres, plus a depth for every pixel.

Principle: 3D space is rebuilt from a sequence of images (motion parallax), not from a single image, so the
network must be temporal.

## Proposed architecture

1. **Input**: frame t and 2-3 previous frames (640×384). Each comes with its localizer pose, the intrinsics
   and the calibration.
2. **Encoder**: one CNN shared by all frames (ResNet-18/34 pretrained on ImageNet), features at 1/4 of the
   resolution.
3. **ManyDepth-style cost volume**:
   - 64-96 depth hypotheses between 2 and 150 m, spaced in inverse depth;
   - the features of the previous frames are warped into view t with the known poses;
   - the metric scale comes from the external pose, so there is no PoseNet.
4. **Decoder**: a U-Net giving dense metric depth plus a per-pixel uncertainty.
5. **Temporal state**: a ConvGRU, for moving objects, occlusions and the car at standstill.
6. **Object head** (anchor-free, CenterNet-style):
   - per-class centres, size, orientation, velocity;
   - the distance of an object comes from the dense depth.

## Training signals

- **Photometric reprojection** between neighbouring frames, with the localizer poses: SSIM + L1, minimum
  reprojection, Monodepth2-style auto-masking, smoothness.
- **A single-frame branch as teacher** where the cost volume is unreliable (objects moving with us, the car
  at standstill), as in ManyDepth.
- **Geometric pseudo-labels**: the distance of the point where an object touches the road, from the known
  camera height and pitch.
- **Radar**: a metric label for the lead vehicle in the ego lane only. It is paired only when:
  - the lane follows the curvature from yaw rate and speed;
  - the road ahead does not curve away;
  - no vehicle in the next lanes, and no pedestrian or bicycle, is near the radar distance;
  - the vision estimate is within a gate.

  The depth loss uses a validity mask. Absolute and relative errors are logged on these labels only.
- **Classes and boxes** from an existing detector used as a teacher (torchvision Faster R-CNN, or RT-DETR).

## Staged plan

1. Multi-frame depth only, self-supervised, on all the segments.
2. Then the object head, with the teacher detector and the radar.
3. Then the temporal memory and the velocities.

## Questions

1. **The architecture.** Is a ManyDepth-style cost volume with external metric poses the right choice?
   Compare it with any alternative you think better, including:
   - a single-frame model (Depth Anything V2, Metric3D) fine-tuned in metres;
   - video transformers;
   - bird's-eye-view representations;
   - pseudo-labels from offline reconstructions (NeRF/3DGS).
2. **Objects moving at our speed** (the worst case): are masks, the single-frame teacher, the radar and
   "looming" enough? What is missing?
3. **Pose and timing errors.** How much do errors of the localizer poses and of the time alignment hurt the
   cost volume, including the rolling shutter and the dynamic pitch under braking or over bumps? What
   accuracy is needed?
4. **Low speed and standstill**: how should the reference frames be chosen (a minimum baseline,
   non-consecutive frames)?
5. **Road-contact pseudo-labels**: how reliable are they on slopes and at long distance?
6. **Radar pairing.** Are the pairing rules sound? The gate on the vision estimate biases the labels: how can
   this be avoided?
7. **Evaluation protocol.** Which metrics (AbsRel, RMSE, δ<1.25, error by distance band)? Validation on unseen
   segments; radar only, or something else too?
8. **Teacher detector.** Which one, under which licenses of weights and datasets, and how should its errors be
   handled?
9. **Budget.** Is the resolution, memory and time budget on the V100 realistic? Where should it be cut?
10. **Baseline.** Which simpler baseline should come first, to know whether the complexity is needed?

## Answer format

- Problems by severity (critical / important / minor), each with its reason and a concrete fix.
- Alternatives, with pros and cons against the proposal.
- References (papers, code), with their licenses.
- The first minimal experiment you recommend: what to train, on which data, with which metric, and which
  result to expect in order to decide whether to go on.
