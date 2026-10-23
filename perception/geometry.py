"""From the frame of a comma camera to the calibrated (car-aligned) frame, with openpilot's own
conventions: axes of openpilot's device frame (x forward), rpyCalib and wideFromDeviceEuler of the
logged calibration, rotations from openpilot.common.transformations."""
from __future__ import annotations

import numpy as np

# The longitudinal offset openpilot's radard applies between a vision distance and a radar distance
# for every car (selfdrive/controls/radard.py, RADAR_TO_CAMERA). It is a generic value, not a
# measurement of a given car: pass it knowingly, or measure the car.
OPENPILOT_RADAR_TO_CAMERA_M = 1.52


def calibrated_from_camera(camera: str, rpy_calib, wide_from_device_euler) -> np.ndarray:
    """3x3 rotation from the axes of `camera` ('fcamera' or 'ecamera', device-frame axes) to the
    calibrated frame."""
    from openpilot.common.transformations.orientation import rot_from_euler

    calibrated_from_device = np.asarray(rot_from_euler(list(rpy_calib))).T
    if camera == 'fcamera':
        return calibrated_from_device
    if camera == 'ecamera':
        wide_from_device = np.asarray(rot_from_euler(list(wide_from_device_euler)))
        return calibrated_from_device @ wide_from_device.T
    raise ValueError(f'unknown camera {camera!r}: fcamera or ecamera')


def to_calibrated(points, camera: str, rpy_calib, wide_from_device_euler) -> np.ndarray:
    """Points (..., 3) in the device-frame axes of `camera`, rotated into the calibrated frame."""
    rotation = calibrated_from_camera(camera, rpy_calib, wide_from_device_euler)
    return np.asarray(points, dtype=np.float64) @ rotation.T
