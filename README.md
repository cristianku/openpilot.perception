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

## Site

perception-web is the control site of this repository: the state of the plan, the experiments
started as guided wizards, their results in plain words and numbers, the depth pictures. It runs on
the server next to openpilot.distill and reads the drives that openpilot.distill downloaded and
localized there.

```bash
git clone https://github.com/cristianku/openpilot.perception.git
cd openpilot.perception
sudo ./install.sh        # --dry-run first to see what it does
```

The installer sets up everything (system packages, uv, the CUDA 12 build of torch on a Tesla
V100, the Python environment, the depth model of Experiment 1 and the systemd service
`perception-web` on port 8767). It prints the address and, until the administrator exists, the
setup code of the first visit. Everything else is done from the page: Settings (the gear at the
bottom) chooses the drives, the code update pulls the latest version and restarts the site. To start
it by hand instead: `web/run.sh`.

| Where | What |
| --- | --- |
| [`web/server.py`](web/server.py) | The HTTP server (standard library): sign-in wall, same-origin JSON POSTs, strict CSP. |
| [`web/actions.py`](web/actions.py), [`web/jobs.py`](web/jobs.py) | What the site can start, and the background jobs (they survive a restart of the site). |
| [`web/status.py`](web/status.py), [`web/settings.py`](web/settings.py) | Machine, GPU lock, drives found on the server, results. |
| [`tasks/update.py`](tasks/update.py) | The code update of the site. |
| [`install.sh`](install.sh), [`deploy/`](deploy/) | The all-in-one installer and the systemd service. |

Results stay on the server, in `outputs/experiments/<job id>/` (not in git).

| Where | What |
| --- | --- |
| [`perception/sources/comma_log.py`](perception/sources/comma_log.py) | Frames of `fcamera.hevc` / `ecamera.hevc` with monotonic times, calibration over time, car motion (`deviceMotion`), `carState`. |
| [`perception/radar/oem_lead.py`](perception/radar/oem_lead.py) | The car's radar lead from `carState` (`stockRadar*` fields); off, vision-only, when the schema has none. |
| [`perception/association.py`](perception/association.py) | Pairs the single radar target with at most the vehicle straight ahead in the ego lane (path from yaw rate), never with objects beside the car or in other lanes. |
| [`perception/dataset.py`](perception/dataset.py) | Dataset adapter: one sample per frame, radar aligned in time, depth targets with validity. |
| [`perception/losses.py`](perception/losses.py), [`perception/metrics.py`](perception/metrics.py) | Depth loss with a validity mask; distance errors on genuine, associated labels only. |
| [`perception/viz.py`](perception/viz.py) | Per object: vision distance, radar measurement, difference, validity, source. |
| [`docs/oem-radar-labels.md`](docs/oem-radar-labels.md) | The contract with the car port and every rule above. |

## Use

Python 3.12 (`>=3.12.3,<3.13`, as openpilot.distill). `uv sync` (with `--extra v100` on a Tesla
V100) gives the environment of the site and the experiments. Reading an rlog needs openpilot's
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
.venv/bin/python -m unittest discover -s tests
```

(Not `uv run`: without `--extra v100` it would put the default CUDA 13 torch back, which has no V100
kernels.)

## Data

No vehicle recording is stored here. Logs stay where openpilot.distill keeps them (`data/`), out
of git.

## License

The original code of this repository is under the [MIT license](LICENSE). Dependencies and any
future model weights keep their own licenses: see [THIRD_PARTY.md](THIRD_PARTY.md).
