"""Pairing the single radar target with at most one vision detection, only when it is unambiguous.

The radar of an ACC follows one target: the nearest vehicle in the ego path. Its distance becomes the
depth label of a detection only when, in a calibrated frame:
  - the detection is a vehicle (car, truck, bus, van), confident enough, inside the ego path;
  - it is the nearest such vehicle, and the next one is clearly farther;
  - no pedestrian, bicycle or other non-vehicle in the path is as near as, or nearer than, the radar
    distance (the radar might be following it);
  - its vision distance is within a gate of the radar distance.
Otherwise there is no label, and the reason says why. The radar distance is never given to a
pedestrian, a bicycle or any other detection.

Positions are in the calibrated frame (perception.geometry.to_calibrated), x forward from the device.
The radar measures from its own position: `radar_to_reference_m` (radar distance + offset = x in the
calibrated frame) depends on the car and has no default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample

VEHICLE_CATEGORIES = frozenset({'car', 'truck', 'bus', 'van'})


@dataclass(frozen=True)
class Detection:
    object_id: int
    category: str
    score: float
    position_m: tuple[float, float, float]                 # calibrated frame, x forward
    box: tuple[float, float, float, float] | None = None   # image pixels x0, y0, x1, y1
    camera: str = 'fcamera'
    provenance: Provenance = Provenance.VISION

    @property
    def x_m(self) -> float:
        return self.position_m[0]


@dataclass(frozen=True)
class AssociationConfig:
    radar_to_reference_m: float           # car-specific, see the module docstring
    max_lateral_m: float = 1.8            # |y| of a detection inside the ego path
    min_score: float = 0.5
    gate_abs_m: float = 5.0               # |vision x - radar x| allowed: max(gate_abs_m, gate_rel * radar x)
    gate_rel: float = 0.25
    min_separation_m: float = 5.0         # the second in-path vehicle must be this much farther
    vehicle_categories: frozenset[str] = field(default=VEHICLE_CATEGORIES)


@dataclass(frozen=True)
class Association:
    reason: str                         # 'associated' or why not
    object_id: int | None = None
    radar: OemLeadSample | None = None
    label_x_m: float | None = None      # radar distance in the calibrated frame
    vision_x_m: float | None = None

    @property
    def associated(self) -> bool:
        return self.object_id is not None

    @property
    def provenance(self) -> Provenance | None:
        return self.radar.provenance if self.radar is not None else None


def _in_path(detection: Detection, config: AssociationConfig) -> bool:
    x, y, z = detection.position_m
    return (all(math.isfinite(v) for v in (x, y, z)) and x > 0.0 and abs(y) <= config.max_lateral_m
            and detection.score >= config.min_score)


def associate(detections: list[Detection], radar: OemLeadSample | None, calibrated: bool,
              config: AssociationConfig) -> Association:
    if radar is None:
        return Association('no radar measurement')
    if not calibrated:
        return Association('no calibration', radar=radar)
    label_x = radar.distance_m + config.radar_to_reference_m
    gate = max(config.gate_abs_m, config.gate_rel * label_x)
    in_path = [d for d in detections if _in_path(d, config)]
    vehicles = sorted((d for d in in_path if d.category in config.vehicle_categories), key=lambda d: d.x_m)
    others = [d for d in in_path if d.category not in config.vehicle_categories]

    if any(d.x_m <= label_x + gate for d in others):
        return Association('non-vehicle in the path near the radar distance', radar=radar, label_x_m=label_x)
    if not vehicles:
        return Association('no vehicle in the path', radar=radar, label_x_m=label_x)
    nearest = vehicles[0]
    if len(vehicles) > 1 and vehicles[1].x_m - nearest.x_m < config.min_separation_m:
        return Association('two vehicles in the path too close to tell apart', radar=radar, label_x_m=label_x)
    if abs(nearest.x_m - label_x) > gate:
        return Association('nearest vehicle outside the radar gate', radar=radar, label_x_m=label_x,
                           vision_x_m=nearest.x_m)
    return Association('associated', object_id=nearest.object_id, radar=radar, label_x_m=label_x,
                       vision_x_m=nearest.x_m)
