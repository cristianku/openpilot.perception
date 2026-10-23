"""Pairing the single radar target with at most one vision detection, only when it is unambiguous.

The radar is an extra check, only for the object straight ahead in the ego lane. An ACC radar follows
one target, the nearest vehicle in the ego path; its distance becomes the depth label of a detection
only when, in a calibrated frame:
  - the car's path is known and the road ahead is not curving away: the lane centre at the radar
    distance, from yaw rate and speed, stays within `max_path_offset_m` of straight ahead;
  - the detection is a vehicle (car, truck, bus, van), confident enough, inside the ego lane (within
    `max_lateral_m` of the lane centre at its distance);
  - it is the nearest vehicle in the lane, and the next one is clearly farther;
  - no vehicle in the next lanes is near the radar distance (a car cutting in, or a neighbour the
    radar might be following);
  - no pedestrian, bicycle or other non-vehicle in the lane or the next lanes is near the radar
    distance, nor in the lane before it;
  - its vision distance is within a gate of the radar distance.
Otherwise there is no label, and the reason says why. The radar distance is never given to a
pedestrian, a bicycle, a car in another lane or any object beside the car.

Positions are in openpilot's calibrated frame (perception.geometry.to_calibrated): x forward,
y right, z down, origin at the device. The radar measures from its own position:
`radar_to_reference_m` (radar distance + offset = x in the calibrated frame) depends on the car and
has no default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample

VEHICLE_CATEGORIES = frozenset({'car', 'truck', 'bus', 'van'})
MIN_CURVATURE_SPEED_MPS = 3.0   # below it the yaw rate is divided by this speed, not by a near-zero one


@dataclass(frozen=True)
class Detection:
    object_id: int
    category: str
    score: float
    position_m: tuple[float, float, float]                 # calibrated frame: x forward, y right, z down
    box: tuple[float, float, float, float] | None = None   # image pixels x0, y0, x1, y1
    camera: str = 'fcamera'
    provenance: Provenance = Provenance.VISION

    @property
    def x_m(self) -> float:
        return self.position_m[0]


@dataclass(frozen=True)
class AssociationConfig:
    radar_to_reference_m: float           # car-specific, see the module docstring
    max_lateral_m: float = 1.5            # |y - lane centre| of a detection inside the ego lane
    next_lanes_m: float = 4.0             # beyond the ego lane, the band of the next lanes on both sides
    max_path_offset_m: float = 1.0        # lane centre at the radar distance, away from straight ahead
    min_score: float = 0.5
    gate_abs_m: float = 5.0               # |vision x - radar x| allowed: max(gate_abs_m, gate_rel * radar x)
    gate_rel: float = 0.25
    min_separation_m: float = 5.0         # the second vehicle in the lane must be this much farther
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


def path_curvature(speed_mps: float, yaw_rate: float) -> float:
    """Signed curvature of the car's path (1/m, positive turning right) from speed and yaw rate."""
    return yaw_rate / max(abs(speed_mps), MIN_CURVATURE_SPEED_MPS)


def lane_centre_y(curvature: float, x_m: float) -> float:
    """y of the ego lane centre at distance x, following the current path (positive right)."""
    return curvature * x_m * x_m / 2.0


def associate(detections: list[Detection], radar: OemLeadSample | None, calibrated: bool,
              curvature: float | None, config: AssociationConfig) -> Association:
    if radar is None:
        return Association('no radar measurement')
    if not calibrated:
        return Association('no calibration', radar=radar)
    label_x = radar.distance_m + config.radar_to_reference_m
    if curvature is None or not math.isfinite(curvature):
        return Association('no car motion', radar=radar, label_x_m=label_x)
    if abs(lane_centre_y(curvature, label_x)) > config.max_path_offset_m:
        return Association('road ahead curving', radar=radar, label_x_m=label_x)
    gate = max(config.gate_abs_m, config.gate_rel * label_x)

    lane, next_lanes = [], []
    for d in detections:
        x, y, z = d.position_m
        if not (all(math.isfinite(v) for v in (x, y, z)) and x > 0.0 and d.score >= config.min_score):
            continue
        offset = abs(y - lane_centre_y(curvature, x))
        if offset <= config.max_lateral_m:
            lane.append(d)
        elif offset <= config.max_lateral_m + config.next_lanes_m:
            next_lanes.append(d)

    def is_vehicle(d: Detection) -> bool:
        return d.category in config.vehicle_categories

    def near_radar(d: Detection) -> bool:
        return abs(d.x_m - label_x) <= gate

    if any(not is_vehicle(d) and d.x_m <= label_x + gate for d in lane) or \
            any(not is_vehicle(d) and near_radar(d) for d in next_lanes):
        return Association('non-vehicle near the radar distance', radar=radar, label_x_m=label_x)
    if any(is_vehicle(d) and near_radar(d) for d in next_lanes):
        return Association('vehicle in the next lane near the radar distance', radar=radar, label_x_m=label_x)
    vehicles = sorted((d for d in lane if is_vehicle(d)), key=lambda d: d.x_m)
    if not vehicles:
        return Association('no vehicle in the lane', radar=radar, label_x_m=label_x)
    nearest = vehicles[0]
    if len(vehicles) > 1 and vehicles[1].x_m - nearest.x_m < config.min_separation_m:
        return Association('two vehicles in the lane too close to tell apart', radar=radar, label_x_m=label_x)
    if not near_radar(nearest):
        return Association('nearest vehicle outside the radar gate', radar=radar, label_x_m=label_x,
                           vision_x_m=nearest.x_m)
    return Association('associated', object_id=nearest.object_id, radar=radar, label_x_m=label_x,
                       vision_x_m=nearest.x_m)
