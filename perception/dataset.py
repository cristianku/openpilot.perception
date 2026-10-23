"""Dataset adapter: one sample per camera frame of a comma segment, with what is known about it.

    uv run python -m perception.dataset data/<segment>/rlog.zst [--camera fcamera|ecamera]

prints what a segment offers: frames, calibration, and whether the car's radar can be read.

A sample says which frame (camera, index in the .hevc, monotonic time), the calibration in force,
the curvature of the car's path (deviceMotion), and the radar measurement aligned to it, if any,
with the time skew. Decoding the image is left to
the caller (`load_image`), e.g. openpilot.distill's NVDEC helpers: this package stays independent of
them. Without radar the adapter still returns every frame: training continues vision-only.

Depth targets come from `depth_targets`: one per detection, valid only for the detection that
perception.association pairs with a genuine radar measurement.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from perception.association import Association, AssociationConfig, Detection, associate, path_curvature
from perception.provenance import Provenance
from perception.radar.oem_lead import MAX_FRAME_SKEW_NS, OemLeadSample, OemLeadTrack, read_oem_leads
from perception.sources.comma_log import CAMERA_STREAMS, Calibration, CommaLog, FrameRef, read_rlog


MS = 1_000_000


@dataclass(frozen=True)
class FrameSample:
    frame: FrameRef
    calibration: Calibration | None
    curvature: float | None         # 1/m, positive turning right; None without recent motion
    radar: OemLeadSample | None
    radar_skew_ns: int | None       # frame time - measurement time
    radar_status: str               # 'aligned', or why there is no radar for this frame


class SegmentAdapter:
    """The frames of one camera of a segment, each with calibration and aligned radar."""

    def __init__(self, log: CommaLog, camera: str = 'fcamera', max_skew_ns: int = MAX_FRAME_SKEW_NS,
                 load_image: Callable[[FrameRef], np.ndarray] | None = None):
        if camera not in CAMERA_STREAMS:
            raise ValueError(f'unknown camera {camera!r}: {", ".join(CAMERA_STREAMS)}')
        self.camera = camera
        self.radar: OemLeadTrack = read_oem_leads(log.car_states)
        self.load_image = load_image
        self.samples: list[FrameSample] = []
        for frame in log.frames[camera]:
            calibration = log.calibration_at(frame.t_ns)
            motion = log.motion_at(frame.t_ns)
            curvature = path_curvature(motion.speed_mps, motion.yaw_rate) if motion is not None else None
            if not self.radar.available or not self.radar.samples:
                radar, skew, status = None, None, self.radar.reason
            else:
                radar, skew = self.radar.nearest(frame.t_ns, max_skew_ns)
                status = 'aligned' if radar is not None else f'no radar measurement within {max_skew_ns // MS} ms'
            self.samples.append(FrameSample(frame, calibration, curvature, radar, skew, status))

    @classmethod
    def from_rlog(cls, path: str, **kwargs) -> SegmentAdapter:
        return cls(read_rlog(path), **kwargs)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int) -> dict:
        sample = self.samples[i]
        item = {'sample': sample}
        if self.load_image is not None:
            item['image'] = self.load_image(sample.frame)
        return item


def depth_targets(sample: FrameSample, detections: Sequence[Detection], config: AssociationConfig,
                  allow_synthetic: bool = False) -> tuple[np.ndarray, np.ndarray, Association]:
    """x targets (m, calibrated frame) and validity, one per detection, for one frame. Only a genuine
    radar measurement makes a target; synthetic ones too with allow_synthetic (pipeline tests)."""
    association = associate(list(detections), sample.radar, sample.calibration is not None, sample.curvature, config)
    target = np.full(len(detections), np.nan, dtype=np.float32)
    valid = np.zeros(len(detections), dtype=bool)
    usable = association.associated and (association.radar.provenance.measured
                                         or (allow_synthetic and association.radar.provenance is Provenance.SYNTHETIC))
    if usable:
        for i, detection in enumerate(detections):
            if detection.object_id == association.object_id:
                target[i] = association.label_x_m
                valid[i] = True
    return target, valid, association


def describe(adapter: SegmentAdapter) -> str:
    calibrated = sum(s.calibration is not None for s in adapter.samples)
    with_motion = sum(s.curvature is not None for s in adapter.samples)
    aligned = sum(s.radar is not None for s in adapter.samples)
    lines = [f'{adapter.camera}: {len(adapter)} frames, {calibrated} calibrated, {with_motion} with car motion',
             f'radar: {adapter.radar.reason}']
    if adapter.radar.available:
        lines.append(f'radar: {aligned} frames with a measurement aligned, repeated {adapter.radar.repeated}, '
                     f'rejected {dict(adapter.radar.rejected) or 0}')
    return '\n'.join(lines)


def main(arguments: list[str]) -> int:
    if not arguments or arguments[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if arguments else 2
    camera = 'fcamera'
    if '--camera' in arguments:
        position = arguments.index('--camera')
        if position + 1 >= len(arguments):
            print('--camera needs fcamera or ecamera', file=sys.stderr)
            return 2
        camera = arguments[position + 1]
        arguments = arguments[:position] + arguments[position + 2:]
    for path in arguments:
        print(path)
        print(describe(SegmentAdapter.from_rlog(path, camera=camera)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
