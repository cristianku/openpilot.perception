"""What a comma rlog gives to perception, read once with openpilot's Cap'n Proto tools: the frames of
fcamera.hevc and ecamera.hevc with their monotonic times, the calibration over time, and the
carState messages (for the car's radar, see perception.radar.oem_lead).

Same conventions as openpilot.distill's localizer: fcamera = narrowRoadEncodeIdx,
ecamera = wideRoadEncodeIdx, frame index in the .hevc = encodeIdx.segmentId, frame time =
timestampEof - 8 ms on the logMonoTime clock, calibration from extrinsicsCalibration when
calStatus is "calibrated". Nothing missing is filled in: a frame without timestamps or an
uncalibrated period is simply absent.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Iterable
from dataclasses import dataclass, field

CAMERA_STREAMS = {
    'fcamera': 'narrowRoadEncodeIdx',
    'ecamera': 'wideRoadEncodeIdx',
}
CALIBRATION_STREAM = 'extrinsicsCalibration'
CAR_STATE_STREAM = 'carState'
FRAME_TIME_OFFSET_NS = 8_000_000  # frame time = timestampEof - 8 ms, as the localizer of openpilot.distill


@dataclass(frozen=True)
class FrameRef:
    camera: str    # 'fcamera' or 'ecamera'
    index: int     # frame index inside <camera>.hevc
    frame_id: int
    t_ns: int      # monotonic, same clock as logMonoTime


@dataclass(frozen=True)
class Calibration:
    t_ns: int                      # logMonoTime of the message
    rpy_calib: tuple[float, float, float]
    wide_from_device_euler: tuple[float, float, float]
    height_m: float | None


@dataclass
class CommaLog:
    frames: dict[str, list[FrameRef]] = field(default_factory=lambda: {camera: [] for camera in CAMERA_STREAMS})
    calibrations: list[Calibration] = field(default_factory=list)
    car_states: list = field(default_factory=list)  # (logMonoTime, carState reader)
    _calibration_times: list[int] = field(default_factory=list, repr=False)

    def calibration_at(self, t_ns: int) -> Calibration | None:
        """The last calibrated values logged at or before t_ns; None before the first one."""
        if len(self._calibration_times) != len(self.calibrations):
            self._calibration_times = [c.t_ns for c in self.calibrations]
        position = bisect.bisect_right(self._calibration_times, t_ns)
        return self.calibrations[position - 1] if position else None


def _frame(camera: str, event, index) -> FrameRef | None:
    if not event.valid or str(index.type) != 'fullHEVC' or not index.timestampEof:
        return None
    if index.timestampEof <= FRAME_TIME_OFFSET_NS:
        return None
    return FrameRef(camera=camera, index=int(index.segmentId), frame_id=int(index.frameId),
                    t_ns=int(index.timestampEof) - FRAME_TIME_OFFSET_NS)


def _calibration(event, calibration) -> Calibration | None:
    rpy, wide = list(calibration.rpyCalib), list(calibration.wideFromDeviceEuler)
    if not event.valid or str(calibration.calStatus) != 'calibrated' or len(rpy) != 3 or len(wide) != 3:
        return None
    if not all(math.isfinite(v) for v in rpy + wide):
        return None
    height = list(calibration.height)
    height_m = float(height[0]) if len(height) == 1 and math.isfinite(height[0]) else None
    return Calibration(t_ns=int(event.logMonoTime), rpy_calib=tuple(rpy), wide_from_device_euler=tuple(wide),
                       height_m=height_m)


def read_events(events: Iterable) -> CommaLog:
    """CommaLog from the events of a log, in log order."""
    log = CommaLog()
    streams = {stream: camera for camera, stream in CAMERA_STREAMS.items()}
    for event in events:
        which = event.which()
        if which in streams:
            frame = _frame(streams[which], event, getattr(event, which))
            if frame is not None:
                log.frames[frame.camera].append(frame)
        elif which == CALIBRATION_STREAM:
            calibration = _calibration(event, getattr(event, which))
            if calibration is not None:
                log.calibrations.append(calibration)
        elif which == CAR_STATE_STREAM and event.valid:
            log.car_states.append((int(event.logMonoTime), event.carState))
    for frames in log.frames.values():
        frames.sort(key=lambda frame: frame.index)
    log.calibrations.sort(key=lambda calibration: calibration.t_ns)
    return log


def read_rlog(path: str) -> CommaLog:
    from openpilot.tools.lib.logreader import LogReader

    return read_events(LogReader(path))
