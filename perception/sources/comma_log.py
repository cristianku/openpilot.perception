"""What a comma rlog gives to perception, read once with openpilot's Cap'n Proto tools: the frames of
fcamera.hevc and ecamera.hevc with their monotonic times, the calibration over time, the motion of
the car (deviceMotion: speed and yaw rate, from the comma device itself) and the carState messages
(for the car's radar, see perception.radar.oem_lead).

Same conventions as openpilot.distill's localizer: fcamera = narrowRoadEncodeIdx,
ecamera = wideRoadEncodeIdx, frame index in the .hevc = encodeIdx.segmentId, frame time =
timestampEof - 8 ms on the logMonoTime clock, calibration from extrinsicsCalibration when
calStatus is "calibrated", motion from deviceMotion (its timestamp, else logMonoTime). Nothing
missing is filled in: a frame without timestamps, an uncalibrated period or a gap in the motion is
simply absent.
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
MOTION_STREAM = 'deviceMotion'
MAX_MOTION_AGE_NS = 100_000_000   # deviceMotion is 20 Hz: two periods
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


@dataclass(frozen=True)
class Motion:
    t_ns: int
    speed_mps: float     # forward, device frame
    yaw_rate: float      # rad/s about the device z axis (down): positive turning right


@dataclass
class CommaLog:
    frames: dict[str, list[FrameRef]] = field(default_factory=lambda: {camera: [] for camera in CAMERA_STREAMS})
    calibrations: list[Calibration] = field(default_factory=list)
    # logMonoTime of every calibration message that is not a usable calibration (uncalibrated, invalid,
    # not finite): from there no calibration is in force until the next calibrated one.
    calibration_resets: list[int] = field(default_factory=list)
    car_states: list = field(default_factory=list)  # (logMonoTime, carState reader)
    motion: list[Motion] = field(default_factory=list)
    _calibration_times: list[int] = field(default_factory=list, repr=False)
    _motion_times: list[int] = field(default_factory=list, repr=False)

    def calibration_at(self, t_ns: int) -> Calibration | None:
        """The last calibrated values logged at or before t_ns; None before the first one, and None after
        a calibration message that invalidates it (until the next calibrated one)."""
        if len(self._calibration_times) != len(self.calibrations):
            self._calibration_times = [c.t_ns for c in self.calibrations]
        position = bisect.bisect_right(self._calibration_times, t_ns)
        if not position:
            return None
        calibration = self.calibrations[position - 1]
        reset = bisect.bisect_right(self.calibration_resets, t_ns)
        if reset and self.calibration_resets[reset - 1] > calibration.t_ns:
            return None
        return calibration

    def motion_at(self, t_ns: int, max_age_ns: int = MAX_MOTION_AGE_NS) -> Motion | None:
        """The last motion sample at or before t_ns, if not older than max_age_ns."""
        if len(self._motion_times) != len(self.motion):
            self._motion_times = [m.t_ns for m in self.motion]
        position = bisect.bisect_right(self._motion_times, t_ns)
        if not position or t_ns - self.motion[position - 1].t_ns > max_age_ns:
            return None
        return self.motion[position - 1]


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


def _motion(event, motion) -> Motion | None:
    speed, yaw = motion.velocityDevice, motion.angularVelocityDevice
    if not event.valid or not speed.valid or not yaw.valid or not (math.isfinite(speed.x) and math.isfinite(yaw.z)):
        return None
    t_ns = int(motion.timestamp) or int(event.logMonoTime)
    return Motion(t_ns=t_ns, speed_mps=float(speed.x), yaw_rate=float(yaw.z))


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
            else:
                log.calibration_resets.append(int(event.logMonoTime))
        elif which == CAR_STATE_STREAM and event.valid:
            log.car_states.append((int(event.logMonoTime), event.carState))
        elif which == MOTION_STREAM:
            motion = _motion(event, event.deviceMotion)
            if motion is not None:
                log.motion.append(motion)
    for frames in log.frames.values():
        frames.sort(key=lambda frame: frame.index)
    log.calibrations.sort(key=lambda calibration: calibration.t_ns)
    log.calibration_resets.sort()
    log.motion.sort(key=lambda motion: motion.t_ns)
    return log


def read_rlog(path: str) -> CommaLog:
    from openpilot.tools.lib.logreader import LogReader

    return read_events(LogReader(path))
