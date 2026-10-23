"""The car's radar over one segment, ready to show and to calibrate with: what it read and when it had a target,
the car's speed, the steady stretches with the car standing still, the frames of the road video.

    from perception.radar.series import radar_series
    series = radar_series('data/<segment>/rlog.zst')

Readings are the radar's own (carState stockRadarLeadDistance through perception.radar.oem_lead, with its
freshness rules); turning them into a distance from the comma is the car's calibration (`from_comma`). The
time of the segment comes from the GPS when it has a fix: the comma's own clock can be months off when it
starts without network or GPS, so recordings are ordered by their route counter, never by that clock.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from pathlib import Path

from perception.radar.oem_lead import read_oem_leads
from perception.sources.rlog import only, read_events

SERIES_HZ = 10
# Standing still: below this speed for at least STANDSTILL_MIN_S, with a target most of the time and one
# reading most of the time (the radar reports in 0.25 m steps).
STANDSTILL_SPEED_MPS = 0.05
STANDSTILL_MIN_S = 2.0
STANDSTILL_MIN_TARGET_SHARE = 0.5
STANDSTILL_MIN_MODE_SHARE = 0.8
KINDS = {'initData', 'carState', 'gpsLocationExternal', 'narrowRoadEncodeIdx'}


def from_comma(reading_m: float, correction_m: float, radar_to_camera_m: float) -> float:
    """A radar reading as a distance from the comma: reading + correction + distance radar -> comma."""
    return reading_m + correction_m + radar_to_camera_m


def _standstills(times: list[float], speeds: list[float], readings: list[float | None]) -> list[dict]:
    """Stretches of at least STANDSTILL_MIN_S standing still, with the reading the radar kept most of the time."""
    found = []
    start = None
    for index in range(len(times) + 1):
        still = index < len(times) and abs(speeds[index]) < STANDSTILL_SPEED_MPS
        if still and start is None:
            start = index
        elif not still and start is not None:
            end = index
            if times[end - 1] - times[start] >= STANDSTILL_MIN_S:
                values = [r for r in readings[start:end] if r is not None]
                share = len(values) / (end - start)
                if values and share >= STANDSTILL_MIN_TARGET_SHARE:
                    reading, count = Counter(round(v, 2) for v in values).most_common(1)[0]
                    if count / len(values) >= STANDSTILL_MIN_MODE_SHARE:
                        found.append({'from_s': round(times[start], 2), 'to_s': round(times[end - 1], 2),
                                      'reading_m': reading, 'target_share': round(share, 2),
                                      'steady_share': round(count / len(values), 2)})
            start = None
    return found


def radar_series(path: str | Path, events=None) -> dict:
    """The radar of one segment as a JSON-ready dict (see the module docstring). `events` is for tests."""
    path = Path(path)
    if events is None:
        events = read_events(path)
    commit = None
    wall_ns = None
    first_ns = None
    gps = None
    car_states = []
    car_rows = []
    frame_ns = []
    for event in only(events, KINDS):
        log_ns = int(event.logMonoTime)
        which = event.which()
        # initData carries the time of the start of the drive, not of this segment.
        if which != 'initData':
            first_ns = log_ns if first_ns is None else min(first_ns, log_ns)
        if which == 'initData' and commit is None:
            commit = str(event.initData.gitCommit)[:8] or None
            wall_ns = int(event.initData.wallTimeNanos) or None
        elif which == 'carState' and event.valid:
            car_states.append((log_ns, event.carState))
            car_rows.append((log_ns, float(event.carState.vEgo)))
        elif which == 'gpsLocationExternal' and gps is None:
            fix = event.gpsLocationExternal
            if fix.hasFix and fix.unixTimestampMillis > 0:
                gps = (log_ns, fix.unixTimestampMillis / 1000.0)
        elif which == 'narrowRoadEncodeIdx':
            frame_ns.append(int(event.narrowRoadEncodeIdx.timestampEof))
    track = read_oem_leads(car_states)
    origin = first_ns or 0
    if gps is not None:
        start_unix, start_from = gps[1] - (gps[0] - origin) / 1e9, 'gps'
    elif wall_ns:
        start_unix, start_from = wall_ns / 1e9, 'comma clock'
    else:
        start_unix, start_from = None, None

    # One point every 1/SERIES_HZ s: the speed of the carState, the newest radar reading not older than that.
    times, speeds, readings = [], [], []
    step = int(1e9 / SERIES_HZ)
    samples = track.samples
    sample_index = 0
    next_t = car_rows[0][0] if car_rows else 0
    for log_ns, speed in car_rows:
        if log_ns < next_t:
            continue
        next_t = log_ns + step
        while sample_index + 1 < len(samples) and samples[sample_index + 1].t_ns <= log_ns:
            sample_index += 1
        reading = None
        if samples and samples[sample_index].t_ns <= log_ns and log_ns - samples[sample_index].t_ns <= 2 * step:
            reading = round(samples[sample_index].distance_m, 2)
        times.append(round((log_ns - origin) / 1e9, 2))
        speeds.append(round(speed, 2))
        readings.append(reading)

    distances = [s.distance_m for s in samples]
    total = len(car_states)
    with_target = total - track.rejected.get('no valid target', 0)
    return {
        'segment': path.parent.name,
        'commit': commit,
        'radar_in_schema': track.available,
        'has_radar': bool(samples),
        'start_unix': None if start_unix is None else round(start_unix, 1),
        'start_from': start_from,
        'duration_s': round((car_rows[-1][0] - origin) / 1e9, 1) if car_rows else 0.0,
        'measurements': len(samples),
        'target_share': round(with_target / total, 3) if total else 0.0,
        'relative_speed_valid': sum(s.relative_speed_mps is not None for s in samples),
        'reading_m': None if not distances else {
            'min': round(min(distances), 2), 'median': round(statistics.median(distances), 2),
            'max': round(max(distances), 2)},
        'standstills': _standstills(times, speeds, readings),
        'series': {'hz': SERIES_HZ, 't_s': times, 'reading_m': readings, 'speed_mps': speeds},
        'frames': {'count': len(frame_ns),
                   't_s': [round((t - origin) / 1e9, 3) for t in frame_ns] if frame_ns else []},
    }


def frame_at(series: dict, t_s: float) -> int | None:
    """The index of the road frame closest to a time of the series (None without frames)."""
    frame_times = series['frames']['t_s']
    if not frame_times:
        return None
    best = min(range(len(frame_times)), key=lambda index: abs(frame_times[index] - t_s))
    return best if math.isfinite(frame_times[best]) else None
