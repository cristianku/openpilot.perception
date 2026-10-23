"""Genuine lead measurements of the PSA stock radar (ARTIV), read passively from a comma rlog.

    uv run python -m perception.radar.psa <rlog.zst> [<rlog.zst> ...]

prints, for each log, how many radar frames are genuine and how many carry a target.

The ARTIV publishes ONE lead target on the ADAS bus (bus 1), not an object list:
  0x2F6 HS2_DYN_MDD_ETAT_2F6  50 Hz  TARGET_DETECTED, INTER_VEHICLE_DISTANCE (0.25 m), TARGET_POSITION
  0x4F6 HS2_DAT_ARTIV_V2_4F6  10 Hz  DISTANCE_GAP (1 m), RELATIVE_SPEED (0.02 m/s), TIME_GAP,
                                     ARTIV_SENSOR_STATE, TARGET_DETECTED
There is no lateral distance. TARGET_POSITION is an enum POS1..POS7 that the cluster uses as a
distance bucket: it is kept raw, never converted to metres.

Only frames received from the bus are measurements. In the logged `can` events the panda marks the
frames openpilot itself sent with src = bus + 128 (TX receipt) and src = bus + 192 (TX blocked by
the safety). The PSA port of cristianku/opendbc sends 0x2F6/0x4F6 of its own, built from
radarState/modelV2, i.e. from the vision model: those frames are counted and dropped, never
returned. It also silences the radar with an ARTIV programming request (0x6B6 02 10 02); the
requests are returned so a caller knows from when a log has no genuine radar any more.

Nothing is invented: a frame without a target, with a sentinel value (255.5 m, 254 m, 93.8 m/s,
25.5 s), with a bad 0x2F6 checksum, or while the sensor is not Active, is returned with
valid = False. Times are the logMonoTime of the `can` event (ns), nothing else.

Signal layout: opendbc/dbc/psa_aee2010_r3.dbc (Motorola bit numbering, `start|size@0+`).
"""
from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

ADAS_BUS = 1
TX_RECEIPT = 128  # src = bus + 128: a frame this device sent
TX_BLOCKED = 192  # src = bus + 192: a frame this device tried to send, refused by the safety

LEAD_2F6 = 0x2F6
LEAD_4F6 = 0x4F6
ARTIV_DIAG_REQUEST = 0x6B6

# CHK_INI of 0x2F6. The DBC comment says 0x7 and upstream opendbc uses 0xB; 0x8 is what the radar
# of a Peugeot 3008 sends (all 434 975 frames of 145 local segments pass) and what
# cristianku/opendbc psa-torque-sunny uses.
CHECKSUM_INIT_2F6 = 0x8

DISTANCE_2F6_NO_TARGET = 1022  # raw 1022 * 0.25 = 255.5 m, the top of the DBC range
DISTANCE_4F6_MAX = 253         # m, DBC range; 254/255 mean no target
RELATIVE_SPEED_MAX = 70.0      # m/s, DBC range; the radar writes 93.8 without a target
TIME_GAP_MAX = 25.4            # s, DBC range; 25.5 means no target
SENSOR_ACTIVE = 2              # ARTIV_SENSOR_STATE: 0 init, 1 inactive, 2 active, 3 reduced visibility, ...


def motorola(data: bytes, start: int, size: int) -> int:
    """Raw unsigned value of a big-endian DBC signal (`start|size@0+`, start = most significant bit)."""
    msb = (start // 8) * 8 + (7 - start % 8)  # position counted from the first bit of byte 0
    total = len(data) * 8
    if msb + size > total:
        raise ValueError(f'signal {start}|{size} does not fit in {len(data)} bytes')
    return (int.from_bytes(data, 'big') >> (total - msb - size)) & ((1 << size) - 1)


def checksum_2f6(data: bytes) -> int:
    """PSA nibble checksum of 0x2F6 (CHECKSUM 51|4, low nibble of byte 6)."""
    cleared = bytearray(data)
    cleared[6] &= 0xF0
    return (CHECKSUM_INIT_2F6 - sum((b >> 4) + (b & 0xF) for b in cleared)) & 0xF


@dataclass
class Lead2F6:
    t_ns: int
    target: bool
    distance_m: float
    display_time_s: float
    target_position_raw: int
    checksum_ok: bool

    @property
    def valid(self) -> bool:
        return self.checksum_ok and self.target and 0.0 < self.distance_m < DISTANCE_2F6_NO_TARGET * 0.25


@dataclass
class Lead4F6:
    t_ns: int
    target: bool
    distance_m: float
    relative_speed_mps: float
    time_gap_s: float
    sensor_state: int
    traffic_direction: int

    @property
    def valid(self) -> bool:
        return (self.target and self.sensor_state == SENSOR_ACTIVE and 0.0 < self.distance_m <= DISTANCE_4F6_MAX
                        and abs(self.relative_speed_mps) <= RELATIVE_SPEED_MAX and self.time_gap_s <= TIME_GAP_MAX)


def decode_2f6(t_ns: int, data: bytes) -> Lead2F6:
    if len(data) != 8:
        raise ValueError(f'0x2F6 has 8 bytes, got {len(data)}')
    return Lead2F6(
        t_ns=t_ns,
        target=motorola(data, 0, 1) == 1,
        distance_m=motorola(data, 15, 10) * 0.25,
        display_time_s=motorola(data, 39, 6) * 0.1,
        target_position_raw=motorola(data, 63, 3),
        checksum_ok=motorola(data, 51, 4) == checksum_2f6(data),
    )


def decode_4f6(t_ns: int, data: bytes) -> Lead4F6:
    if len(data) != 5:
        raise ValueError(f'0x4F6 has 5 bytes, got {len(data)}')
    return Lead4F6(
        t_ns=t_ns,
        target=motorola(data, 36, 1) == 1,
        distance_m=float(motorola(data, 15, 8)),
        relative_speed_mps=round(motorola(data, 20, 13) * 0.02 - 70.0, 2),
        time_gap_s=round(motorola(data, 7, 8) * 0.1, 1),
        sensor_state=motorola(data, 23, 3),
        traffic_direction=motorola(data, 39, 2),
    )


def is_artiv_programming_request(data: bytes) -> bool:
    """0x6B6 02 10 02, unpadded or zero-padded: the request that silences the stock radar."""
    return data[:3] == b'\x02\x10\x02' and not any(data[3:])


@dataclass
class RadarLog:
    lead_2f6: list[Lead2F6] = field(default_factory=list)
    lead_4f6: list[Lead4F6] = field(default_factory=list)
    # Frames on bus 1 that openpilot sent (TX receipts) or tried to send (blocked): never measurements.
    injected_frames: int = 0
    blocked_frames: int = 0
    # logMonoTime of every ARTIV programming request openpilot sent: no genuine radar after it,
    # until 0x2F6/0x4F6 appear again on bus 1.
    artiv_requests_ns: list[int] = field(default_factory=list)

    def arrays(self) -> dict[str, np.ndarray]:
        """The measurements as flat arrays, one row per frame, valid or not."""
        return {
            '2f6_t_ns': np.array([x.t_ns for x in self.lead_2f6], dtype=np.int64),
            '2f6_valid': np.array([x.valid for x in self.lead_2f6], dtype=bool),
            '2f6_distance_m': np.array([x.distance_m for x in self.lead_2f6], dtype=np.float32),
            '2f6_target_position_raw': np.array([x.target_position_raw for x in self.lead_2f6], dtype=np.uint8),
            '4f6_t_ns': np.array([x.t_ns for x in self.lead_4f6], dtype=np.int64),
            '4f6_valid': np.array([x.valid for x in self.lead_4f6], dtype=bool),
            '4f6_distance_m': np.array([x.distance_m for x in self.lead_4f6], dtype=np.float32),
            '4f6_relative_speed_mps': np.array([x.relative_speed_mps for x in self.lead_4f6], dtype=np.float32),
            '4f6_time_gap_s': np.array([x.time_gap_s for x in self.lead_4f6], dtype=np.float32),
        }


def read_frames(events: Iterable[tuple[int, Iterable[tuple[int, bytes, int]]]]) -> RadarLog:
    """Radar measurements from (logMonoTime, [(address, data, src), ...]) of the `can` events."""
    log = RadarLog()
    for t_ns, frames in events:
        for address, data, src in frames:
            if address == ARTIV_DIAG_REQUEST and src == ADAS_BUS + TX_RECEIPT and is_artiv_programming_request(data):
                log.artiv_requests_ns.append(t_ns)
            if address not in (LEAD_2F6, LEAD_4F6):
                continue
            if src == ADAS_BUS + TX_RECEIPT:
                log.injected_frames += 1
            elif src == ADAS_BUS + TX_BLOCKED:
                log.blocked_frames += 1
            elif src == ADAS_BUS:
                if address == LEAD_2F6 and len(data) == 8:
                    log.lead_2f6.append(decode_2f6(t_ns, data))
                elif address == LEAD_4F6 and len(data) == 5:
                    log.lead_4f6.append(decode_4f6(t_ns, data))
    return log


def can_events(path: str) -> Iterable[tuple[int, list[tuple[int, bytes, int]]]]:
    """The `can` events of an rlog, as read_frames wants them. Needs openpilot's LogReader."""
    from openpilot.tools.lib.logreader import LogReader

    for event in LogReader(path):
        if event.which() == 'can':
            yield event.logMonoTime, [(f.address, bytes(f.dat), f.src) for f in event.can]


def read_rlog(path: str) -> RadarLog:
    return read_frames(can_events(path))


def summary(name: str, log: RadarLog) -> str:
    valid_2f6 = sum(x.valid for x in log.lead_2f6)
    valid_4f6 = sum(x.valid for x in log.lead_4f6)
    bad_checksum = sum(not x.checksum_ok for x in log.lead_2f6)
    text = (f'{name}: 0x2F6 {valid_2f6}/{len(log.lead_2f6)} with a target (bad checksum {bad_checksum}), '
                    f'0x4F6 {valid_4f6}/{len(log.lead_4f6)} with a target, '
                    f'injected by openpilot {log.injected_frames}, blocked {log.blocked_frames}')
    if log.artiv_requests_ns:
        text += f', radar silenced by openpilot {len(log.artiv_requests_ns)} time(s): no genuine radar after it'
    return text


def main(arguments: list[str]) -> int:
    if not arguments or arguments[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if arguments else 2
    for path in arguments:
        print(summary(path, read_rlog(path)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
