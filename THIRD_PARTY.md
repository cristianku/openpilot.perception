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
| [OpenCV](https://opencv.org) (optional) | drawing the objects on a frame; the geometry audit (SIFT, essential matrix, RANSAC) | Apache-2.0 (4.5 and later) |
| [FFmpeg](https://ffmpeg.org) (the `ffmpeg` program, not linked) | decoding the HEVC frames for the geometry audit | LGPL/GPL, by build; called as a separate program |
| [safetensors](https://github.com/huggingface/safetensors) (optional) | reading openpilot.distill's `localizer.safetensors` | Apache-2.0 |
| [pycapnp](https://github.com/capnproto/pycapnp) (tests) | the synthetic `carState` versions | BSD-2-Clause |

## Excluded

ManyDepth and Monodepth2 (Niantic): non-commercial licenses, "Patent Pending". No code or weights from them.
An independent implementation of a published method is not, by itself, free of patents.

## Models and weights

None yet. Any pretrained model added later (backbone, depth or detection network) must be listed
here with its license and source before use; weights with a non-commercial or unclear license
must not be published from this repository.

## Data

No recording is stored in this repository. The tests build synthetic messages only.
