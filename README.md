# openpilot.perception

Spatial (3D) perception from the road video of a comma device: recognising objects and estimating
their position, trained independently of the openpilot driving model, with metric supervision from
real sensors where the car has them.

It is developed alongside [openpilot.distill](https://github.com/cristianku/openpilot.distill),
which distills the openpilot driving model and mounts this repository as a git submodule
(`perception_repo/`, imported as `perception`). The distillation keeps working unchanged; this
repository adds a second, independent source of supervision. It reads comma logs through
openpilot's Cap'n Proto tools and knows no car maker and no CAN message. No output of it is
connected to vehicle control.

**Status: early.** Data path, labels, loss, metrics and display exist; the vision model does not
yet.

| Where | What |
| --- | --- |
| [`perception/sources/comma_log.py`](perception/sources/comma_log.py) | Frames of `fcamera.hevc` / `ecamera.hevc` with monotonic times, calibration over time, `carState`. |
| [`perception/radar/oem_lead.py`](perception/radar/oem_lead.py) | The car's radar lead from `carState` (`stockRadar*` fields); off, vision-only, when the schema has none. |
| [`perception/association.py`](perception/association.py) | Pairs the single radar target with at most one vehicle detection, only when unambiguous. |
| [`perception/dataset.py`](perception/dataset.py) | Dataset adapter: one sample per frame, radar aligned in time, depth targets with validity. |
| [`perception/losses.py`](perception/losses.py), [`perception/metrics.py`](perception/metrics.py) | Depth loss with a validity mask; distance errors on genuine, associated labels only. |
| [`perception/viz.py`](perception/viz.py) | Per object: vision distance, radar measurement, difference, validity, source. |
| [`docs/oem-radar-labels.md`](docs/oem-radar-labels.md) | The contract with the car port and every rule above. |

## Use

Python 3.12 (`>=3.12.3,<3.13`, as openpilot.distill). Reading an rlog needs openpilot's
`LogReader`, which the openpilot.distill environment provides.

From openpilot.distill (submodule checked out):

```bash
uv run python -m perception.dataset data/<segment>/rlog.zst [--camera fcamera|ecamera]
```

prints the frames, the calibrated ones and whether the car's radar can be read.

```python
from perception.association import AssociationConfig
from perception.dataset import SegmentAdapter, depth_targets

adapter = SegmentAdapter.from_rlog('data/<segment>/rlog.zst', camera='fcamera')
sample = adapter.samples[0]      # frame, calibration, aligned radar (or None) and why
target, valid, association = depth_targets(sample, detections, AssociationConfig(radar_to_reference_m=...))
```

Tests, from the root of this repository (synthetic data only):

```bash
python -m unittest discover -s tests
```

## Data

No vehicle recording is stored here. Logs stay where openpilot.distill keeps them (`data/`), out
of git.

## License

The original code of this repository is under the [MIT license](LICENSE). Dependencies and any
future model weights keep their own licenses: see [THIRD_PARTY.md](THIRD_PARTY.md).
