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

## Fetched at pinned versions (not vendored)

| Component | Used for | License | Pin |
| --- | --- | --- | --- |
| [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2) `metric_depth` code | the frozen monocular baseline (`perception/models/depth_anything.py`) | Apache-2.0 | commit `a561b849ebae10a6f5ef49e26c83cbbcd36c71bf` |
| [Depth Anything V2 Metric VKITTI Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-VKITTI-Small) weights | same | Apache-2.0 (Small only: Base/Large/Giant are CC-BY-NC-4.0 for the project) | revision `c725b8589bdf6ab04072cab74c0467830db80d6d`, SHA-256 `9203e538d35255c90dda4b7fedb47ff33fe725497bcca3b1e53b3a65ee63f0cb` |

Both are downloaded on first use into `~/.cache/openpilot-perception`. The weights were trained on Virtual
KITTI 2, whose scenes are cloned from KITTI tracking sequences (KITTI is CC-BY-NC-SA 3.0): a KITTI-based
benchmark is not independent of this checkpoint.

## Excluded

ManyDepth and Monodepth2 (Niantic): non-commercial licenses, "Patent Pending". No code or weights from them.
An independent implementation of a published method is not, by itself, free of patents.

## Models and weights

Only the Depth Anything V2 Small checkpoint above. Any pretrained model added later (backbone, depth or detection network) must be listed
here with its license and source before use; weights with a non-commercial or unclear license
must not be published from this repository.

## Data

No recording is stored in this repository. The tests build synthetic messages only.
