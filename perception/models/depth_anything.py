"""Depth Anything V2 Metric (outdoor, ViT-S): the frozen monocular baseline of Experiment 1.

Nothing of it is copied into this repository. On first use the code is downloaded at a pinned commit of
github.com/DepthAnything/Depth-Anything-V2 (Apache-2.0) and the weights at a pinned revision of
huggingface.co/depth-anything/Depth-Anything-V2-Metric-VKITTI-Small (Apache-2.0), checked against their
SHA-256, into ~/.cache/openpilot-perception (or OPENPILOT_PERCEPTION_CACHE). Only the Small model: the
larger ones are CC-BY-NC-4.0.

Preprocessing is the official one (`infer_image`): a BGR uint8 image, resized keeping the aspect ratio so
that the shorter side is 518 px and both sides are multiples of 14, then normalised as ImageNet. The output
is the depth along the optical axis, in metres, at the input size, float32 (the raw numbers, never a
normalised picture). `max_depth` is 80 m, as trained: a larger value would only rescale the output.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

CODE_COMMIT = 'a561b849ebae10a6f5ef49e26c83cbbcd36c71bf'
CODE_URL = f'https://github.com/DepthAnything/Depth-Anything-V2/archive/{CODE_COMMIT}.tar.gz'
WEIGHTS_REPO = 'depth-anything/Depth-Anything-V2-Metric-VKITTI-Small'
WEIGHTS_REVISION = 'c725b8589bdf6ab04072cab74c0467830db80d6d'
WEIGHTS_FILE = 'depth_anything_v2_metric_vkitti_vits.pth'
WEIGHTS_SHA256 = '9203e538d35255c90dda4b7fedb47ff33fe725497bcca3b1e53b3a65ee63f0cb'
MAX_DEPTH_M = 80.0
INPUT_SIZE = 518
VITS = {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]}


def cache_dir() -> Path:
    return Path(os.environ.get('OPENPILOT_PERCEPTION_CACHE', Path.home() / '.cache' / 'openpilot-perception'))


def ensure_code() -> Path:
    """The metric_depth folder of the pinned commit."""
    target = cache_dir() / f'Depth-Anything-V2-{CODE_COMMIT[:12]}'
    if not (target / 'metric_depth' / 'depth_anything_v2' / 'dpt.py').is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(CODE_URL, timeout=60).read()
        part = target.with_suffix('.part')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            root = archive.getmembers()[0].name.split('/')[0]
            members = [m for m in archive.getmembers() if m.name.startswith(root + '/metric_depth/')
                       or m.name == root + '/LICENSE']
            archive.extractall(part, members=members, filter='data')
        (part / root).rename(target)
        part.rmdir()
    return target / 'metric_depth'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def ensure_weights() -> Path:
    from huggingface_hub import hf_hub_download

    path = Path(hf_hub_download(WEIGHTS_REPO, WEIGHTS_FILE, revision=WEIGHTS_REVISION,
                                cache_dir=str(cache_dir() / 'huggingface')))
    if sha256(path) != WEIGHTS_SHA256:
        raise ValueError(f'{path}: SHA-256 does not match the pinned weights')
    return path


def load(device: str = 'cuda'):
    """The model in eval mode on `device`."""
    import torch

    code = ensure_code()
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))
    from depth_anything_v2.dpt import DepthAnythingV2

    model = DepthAnythingV2(**VITS, max_depth=MAX_DEPTH_M)
    model.load_state_dict(torch.load(ensure_weights(), map_location='cpu'))
    return model.to(device).eval()


def predict(model, bgr: np.ndarray) -> np.ndarray:
    """Depth in metres (H, W, float32) of a BGR uint8 image, with the official preprocessing."""
    import torch

    with torch.no_grad():
        depth = model.infer_image(bgr, INPUT_SIZE)
    return np.asarray(depth, dtype=np.float32)


def network_input_size(model, bgr: np.ndarray) -> tuple[int, int]:
    """(width, height) the network really sees for this image, from the official preprocessing; the focal
    length it sees is the image's one times width_seen / width."""
    tensor, _ = model.image2tensor(bgr, INPUT_SIZE)
    return int(tensor.shape[-1]), int(tensor.shape[-2])
