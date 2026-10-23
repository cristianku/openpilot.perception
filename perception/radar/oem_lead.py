"""The lead measurement of the car's own (OEM) radar, as the car port logs it in carState.

Contract agreed with the producer (cristianku/opendbc, psa-torque-sunny-testing, opendbc/car/car.capnp):
  stockRadarLeadDistance         m, longitudinal gap to the lead, measured by the radar
  stockRadarLeadRelativeSpeed    m/s
  stockRadarLeadValid            the distance is a fresh measurement of a target
  stockRadarMeasurementTimeNanos monotonic time of the original measurement (logMonoTime clock)
  stockRadarRelativeSpeedValid   the relative speed is valid too (in the schema, beyond the four above)

This module knows nothing about any car maker or CAN message: it reads carState only. The reading
is off, and perception runs vision-only, when the loaded schema has no such fields (openpilot built
with an opendbc without them). An rlog recorded before the fields existed reads as "never valid".

A radar with one target is not a 3D map: a sample is one longitudinal distance to the one object the
radar follows, with no lateral position and no class. Pairing it with a detection is
perception.association's job, and it is conservative.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from perception.provenance import Provenance

DISTANCE = 'stockRadarLeadDistance'
RELATIVE_SPEED = 'stockRadarLeadRelativeSpeed'
VALID = 'stockRadarLeadValid'
MEASUREMENT_TIME = 'stockRadarMeasurementTimeNanos'
RELATIVE_SPEED_VALID = 'stockRadarRelativeSpeedValid'
REQUIRED_FIELDS = (DISTANCE, RELATIVE_SPEED, VALID, MEASUREMENT_TIME)

# Consumer-side bounds, the same for any car. Not sensor specifications.
MAX_AGE_NS = 300_000_000            # carState time - measurement time
DISTANCE_RANGE_M = (0.5, 250.0)
MAX_RELATIVE_SPEED_MPS = 70.0
MAX_FRAME_SKEW_NS = 25_000_000      # |frame time - measurement time| for a label (half a 20 Hz frame)


@dataclass(frozen=True)
class OemLeadSample:
    t_ns: int                         # time of the measurement, not of the carState message
    distance_m: float                 # from the radar, longitudinal
    relative_speed_mps: float | None  # None when not valid
    provenance: Provenance = Provenance.OEM_RADAR


@dataclass
class OemLeadTrack:
    available: bool                    # the schema has the fields: the radar can be read
    samples: list[OemLeadSample] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)  # why messages gave no sample
    repeated: int = 0                  # carState messages carrying an already-read measurement
    unavailable_reason: str = ''
    _times: list[int] = field(default_factory=list, repr=False)

    @property
    def reason(self) -> str:
        if not self.available:
            return self.unavailable_reason + ': vision-only'
        if not self.samples:
            return 'no valid radar measurement in the log: vision-only'
        return f'{len(self.samples)} radar measurements'

    def nearest(self, t_ns: int, max_skew_ns: int = MAX_FRAME_SKEW_NS) -> tuple[OemLeadSample | None, int | None]:
        """The measurement closest in time to t_ns and its skew (t_ns - measurement time), when within
        max_skew_ns; on a tie the earlier one. Nothing is interpolated."""
        if not self.samples:
            return None, None
        if len(self._times) != len(self.samples):
            self._times = [s.t_ns for s in self.samples]
        times = self._times
        position = bisect.bisect_left(times, t_ns)
        candidates = [i for i in (position - 1, position) if 0 <= i < len(times)]
        best = min(candidates, key=lambda i: abs(times[i] - t_ns))
        skew = t_ns - times[best]
        if abs(skew) > max_skew_ns:
            return None, None
        return self.samples[best], skew


def field_names(car_state) -> set[str]:
    """Field names of a carState reader or builder (pycapnp)."""
    return set(car_state.schema.fieldnames)


def schema_supports_oem_lead(car_state) -> bool:
    return set(REQUIRED_FIELDS) <= field_names(car_state)


def _relative_speed(car_state, has_speed_validity: bool) -> float | None:
    # Without its own validity flag the relative speed is not trusted.
    if not has_speed_validity or not getattr(car_state, RELATIVE_SPEED_VALID):
        return None
    speed = float(getattr(car_state, RELATIVE_SPEED))
    if not math.isfinite(speed) or abs(speed) > MAX_RELATIVE_SPEED_MPS:
        return None
    return speed


def read_oem_leads(car_states: Iterable[tuple[int, object]]) -> OemLeadTrack:
    """Radar samples from (logMonoTime, carState) pairs in log order."""
    track: OemLeadTrack | None = None
    has_speed_validity = False
    last_t = 0
    for log_ns, car_state in car_states:
        if track is None:
            names = field_names(car_state)
            if not set(REQUIRED_FIELDS) <= names:
                return OemLeadTrack(available=False, unavailable_reason='schema without the stockRadar fields')
            track = OemLeadTrack(available=True)
            has_speed_validity = RELATIVE_SPEED_VALID in names
        if not getattr(car_state, VALID):
            track.rejected['no valid target'] += 1
            continue
        t_ns = int(getattr(car_state, MEASUREMENT_TIME))
        distance = float(getattr(car_state, DISTANCE))
        if t_ns <= 0:
            track.rejected['no measurement time'] += 1
        elif t_ns > log_ns:
            track.rejected['measurement after its message'] += 1
        elif log_ns - t_ns > MAX_AGE_NS:
            track.rejected['stale'] += 1
        elif not (math.isfinite(distance) and DISTANCE_RANGE_M[0] <= distance <= DISTANCE_RANGE_M[1]):
            track.rejected['distance out of range'] += 1
        elif t_ns == last_t:
            track.repeated += 1
        elif t_ns < last_t:
            track.rejected['measurement time going back'] += 1
        else:
            track.samples.append(OemLeadSample(t_ns=t_ns, distance_m=distance,
                                               relative_speed_mps=_relative_speed(car_state, has_speed_validity)))
            last_t = t_ns
    return track if track is not None else OemLeadTrack(available=False, unavailable_reason='no carState in the log')
