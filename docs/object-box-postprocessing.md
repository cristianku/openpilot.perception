# Objects: 2D box post-processing (first slice of "Find the cars")

**Status:** CPU-tested foundation, NOT an object detector or a vehicle-control feature.
See [design-radar-objects.md](design-radar-objects.md) §5, step 3, and
[detector-choice.md](detector-choice.md) for the planned D-FINE-X model.

`perception.objects.boxes` provides a model-independent contract between a
future detector and the human-review / radar association steps:

- `centre_crop(width, height, side=640, centre=None)` selects a square,
  native-pixel second pass. Pass the calibrated vanishing point when available.
  Frame centre is a fallback, **not** a road-geometry estimate.
- `project_boxes(detections, frame_size, crop=None)` converts detector boxes
  already scaled back to their input frame/crop into **original full-frame pixels**.
  Unknown classes, invalid scores and degenerate boxes are discarded. Cropped
  boxes are clipped **before** adding the crop offset.
- `merge_boxes(full, crop)` runs greedy class-wise IoU suppression, preferring
  a cropped detection only in a score tie. A person and a bicycle may remain
  separate overlapping boxes; no claim of unique object identity is made.

The accepted class names come from COCO **by name**, not by class index:
person → pedestrian; bicycle, car, bus and truck unchanged;
motorbike/motorcycle → motorcycle. COCO does not define a `van` class.
Other classes are currently outside this experiment.

## Intended next integration

1. Pin the D-FINE-X implementation, checkpoint and dependencies as specified in
   `detector-choice.md`, then update `THIRD_PARTY.md` **before first use**.
2. Post-process full-frame detector output to the **full original image size**;
   post-process the crop output to the **native crop size** (not 640 if smaller).
3. Project both outputs into the original camera frame and merge them.
4. Preserve class, score, box and source in the detection record; only then
   compute review candidates. Show no model-derived depth or suggested target
   before a blind human review.
5. Independently validate recall and false positives on held-out road sessions.

**No metric depth is inferred here.** Pixel boxes are not radar labels.
The current `perception.association.Detection` requires a calibrated **3D**
position; do not fabricate one from a 2D bounding box. In particular, the
car's single OEM radar target must **never** be assigned to a pedestrian,
bicycle or neighbouring-lane vehicle.

This module runs offline on recorded images. It neither reads live vehicle
state nor commands braking or acceleration.

## CPU-only tests

```bash
python -m unittest tests.test_object_boxes -v
```

No GPU, checkpoint download, CAN stream or vehicle connection required.
