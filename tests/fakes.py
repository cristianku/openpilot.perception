"""Synthetic log events for the tests. Nothing here comes from a real drive."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

FIXTURES = Path(__file__).parent / 'fixtures'
MS = 1_000_000


def schemas():
    """The carState versions of fixtures/carstate_versions.capnp (needs pycapnp)."""
    import capnp

    return capnp.load(str(FIXTURES / 'carstate_versions.capnp'))


class Event:
    """What the readers use of an openpilot log event: which(), logMonoTime, valid, the payload."""

    def __init__(self, which: str, log_ns: int, payload, valid: bool = True):
        self._which = which
        self.logMonoTime = log_ns
        self.valid = valid
        setattr(self, which, payload)

    def which(self) -> str:
        return self._which


def encode_idx(stream: str, index: int, eof_ns: int, valid: bool = True, kind: str = 'fullHEVC') -> Event:
    payload = SimpleNamespace(segmentId=index, frameId=1000 + index, type=kind, timestampEof=eof_ns)
    return Event(stream, eof_ns + 2 * MS, payload, valid)


def calibration(log_ns: int, rpy=(0.0, 0.0, 0.0), wide=(0.0, 0.0, 0.0), status: str = 'calibrated',
                height=(1.22,), valid: bool = True) -> Event:
    payload = SimpleNamespace(rpyCalib=list(rpy), wideFromDeviceEuler=list(wide), calStatus=status,
                              height=list(height))
    return Event('extrinsicsCalibration', log_ns, payload, valid)


def car_state(struct, log_ns: int, **fields) -> Event:
    return Event('carState', log_ns, struct.new_message(vEgo=10.0, **fields))


def motion(t_ns: int, speed: float = 10.0, yaw_rate: float = 0.0, valid: bool = True, timestamp: int | None = None):
    payload = SimpleNamespace(velocityDevice=SimpleNamespace(x=speed, valid=valid),
                              angularVelocityDevice=SimpleNamespace(z=yaw_rate, valid=True),
                              timestamp=t_ns if timestamp is None else timestamp)
    return Event('deviceMotion', t_ns + 3 * MS, payload)
