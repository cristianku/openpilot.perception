# Third-party code, data and models

The MIT license of this repository covers only its original code. Everything below keeps its own
license; check it again before adding or redistributing anything.

## Used at run time (not vendored)

| Component | Used for | License |
| --- | --- | --- |
| [numpy](https://numpy.org) | arrays | BSD-3-Clause |
| [PyTorch](https://pytorch.org) | depth loss | BSD-3-Clause |
| [openpilot](https://github.com/commaai/openpilot) (`tools.lib.logreader`, `common.transformations`) | reading comma `rlog.zst`, rotations | MIT |
| [opendbc](https://github.com/commaai/opendbc) `car.capnp` (through openpilot's cereal) | the `carState` schema | MIT |
| [OpenCV](https://opencv.org) (optional) | drawing the objects on a frame | Apache-2.0 |
| [pycapnp](https://github.com/capnproto/pycapnp) (tests) | the synthetic `carState` versions | BSD-2-Clause |

## Models and weights

None yet. Any pretrained model added later (backbone, depth or detection network) must be listed
here with its license and source before use; weights with a non-commercial or unclear license
must not be published from this repository.

## Data

No recording is stored in this repository. The tests build synthetic messages only.
