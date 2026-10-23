# openpilot.perception

Spatial (3D) perception from the road video of a comma device, trained independently of the
openpilot driving model, with metric supervision from real sensors where the car has them.

It is developed alongside [openpilot.distill](https://github.com/cristianku/openpilot.distill),
which distills the openpilot driving model and mounts this repository as a git submodule
(`perception_repo/`, imported as `perception`). The two lines stay separate: the distillation from
the openpilot model keeps working unchanged, and this repository adds a second, independent
source of supervision.

**Status: early.** What exists today:

| Where | What |
| --- | --- |
| [`perception/radar/psa.py`](perception/radar/psa.py) | Genuine lead distance / relative speed of the PSA stock radar, read passively from a comma `rlog.zst`. Frames openpilot sent itself are counted and dropped. |
| [`docs/psa-radar.md`](docs/psa-radar.md) | What the PSA radar gives (one lead target, no lateral position), who else writes its CAN messages, and why recording needs openpilot longitudinal off. |

No model, no weights and no training code yet.

## Use

Python 3.12 (`>=3.12.3,<3.13`, as openpilot.distill). Reading an rlog needs openpilot's
`LogReader`, which the openpilot.distill environment provides; the decoding itself needs only
numpy.

From openpilot.distill (submodule checked out):

```bash
uv run python -m perception.radar.psa data/<segment>/rlog.zst
```

```python
from perception.radar import psa

log = psa.read_rlog('data/<segment>/rlog.zst')
arrays = log.arrays()   # 2f6_t_ns, 2f6_valid, 2f6_distance_m, 4f6_relative_speed_mps, ...
```

Every frame is returned with a `valid` flag; nothing is interpolated or invented. Only frames
received from the bus count as measurements.

Tests, from the root of this repository:

```bash
python -m unittest discover -s tests
```

## Data

No vehicle recording is stored here. Logs stay where openpilot.distill keeps them (`data/`), out
of git.

## License

The original code of this repository is under the [MIT license](LICENSE). Dependencies and any
future model weights keep their own licenses: see [THIRD_PARTY.md](THIRD_PARTY.md).
