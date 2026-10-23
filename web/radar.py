"""The car's radar for the site: which drives and recordings have it, the readings of one drive as distances from
the comma, and the calibration tests in front of a wall (or a parked car).

Where the segments come from:
  drives      the drives folder of openpilot.distill this site reads (Settings > Drives);
  recordings  outputs/recordings/<route>--<segment>/, the short recordings copied from the comma for the tests.
Their radar is read once by the radar_index job (perception.radar.index) into outputs/radar/<segment>.json.

Calibration tests (outputs/web/radar_tests.json, 600): a recording standing still in front of something, the
radar's steady reading there, and the tape: radar (front bumper) to it and comma to it. Every test gives
  correction        = tape radar - reading            (the radar's zero)
  radar to comma    = tape comma - tape radar
and the car's calibration is the mean over the tests kept (web/settings.py stores it; it can still be typed
by hand in Settings).
"""
from __future__ import annotations

import json
import re
import secrets
import statistics
import threading
import time
from pathlib import Path

from web.common import WebError, read_private, write_private

# A segment of a drive is a minute long: segment k starts about k minutes after the drive.
SEGMENT_SECONDS = 60.0
SEGMENT_NAME_RE = re.compile(r'^[0-9a-f]{18,24}$')
RECORDING_NAME_RE = re.compile(r'^([0-9a-f]{8}--[0-9a-f]{10})--([0-9]{1,4})$')
TEST_ID_RE = re.compile(r'^[0-9a-f]{12}$')
# Readings come in 0.25 m steps: half a step is the uncertainty of one reading.
READING_STEP_M = 0.25
TAPE_RANGE_M = (0.3, 100.0)
OVERVIEW_POINTS = 1500


def radar_dir(repo: Path) -> Path:
    return repo / 'outputs' / 'radar'


def recordings_dir(repo: Path) -> Path:
    return repo / 'outputs' / 'recordings'


def route_of(name: str) -> str:
    """The drive (route) a segment belongs to: distill names are <route 18 hex><segment 2>, comma names
    <counter>--<tag>--<segment>."""
    match = RECORDING_NAME_RE.fullmatch(name)
    if match:
        return match.group(1)
    return name[:18]


def segment_number(name: str) -> int:
    match = RECORDING_NAME_RE.fullmatch(name)
    if match:
        return int(match.group(2))
    try:
        return int(name[18:])
    except ValueError:
        return 0


def segment_folders(repo: Path, drives_path: Path | None) -> dict[str, Path]:
    """Every segment folder with a log, by name: the drives of distill, then the copied recordings."""
    folders = {}
    if drives_path is not None:
        try:
            for entry in sorted(drives_path.iterdir()):
                if SEGMENT_NAME_RE.fullmatch(entry.name) and (entry / 'rlog.zst').is_file():
                    folders[entry.name] = entry
        except OSError:
            pass
    try:
        for entry in sorted(recordings_dir(repo).iterdir()):
            if RECORDING_NAME_RE.fullmatch(entry.name) and (entry / 'rlog.zst').is_file():
                folders[entry.name] = entry
    except OSError:
        pass
    return folders


def not_indexed(repo: Path, folders: dict[str, Path]) -> list[Path]:
    from perception.radar.index import cached

    out = radar_dir(repo)
    return [folder for folder in folders.values() if cached(out, folder) is None]


def load_series(repo: Path, name: str) -> dict | None:
    path = radar_dir(repo) / (name + '.json')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class RadarIndex:
    """The kept series, summarised per drive. Reading 300 small JSON files takes a moment, so the summary is kept
    until a series is written (the folder changes), the segments change or the calibration does."""

    def __init__(self, repo: Path):
        self.repo = repo
        self._lock = threading.Lock()
        self._summary = None
        self._key = None

    def clear(self) -> None:
        with self._lock:
            self._summary = None

    def drives(self, folders: dict[str, Path], calibration: dict) -> dict:
        try:
            changed = radar_dir(self.repo).stat().st_mtime_ns
        except OSError:
            changed = 0
        key = (changed, tuple(sorted(folders)), calibration.get('radar_to_reference_m'))
        with self._lock:
            if self._summary is not None and self._key == key:
                return self._summary
        by_route = {}
        for name in folders:
            series = load_series(self.repo, name)
            route = route_of(name)
            entry = by_route.setdefault(route, {
                'route': route, 'kind': 'recording' if RECORDING_NAME_RE.fullmatch(name) else 'drive',
                'segments': 0, 'read': 0, 'with_radar': 0, 'start_unix': None, 'start_from': None,
                'duration_s': 0.0, 'measurements': 0, 'samples': 0.0, 'minimum_m': None, 'maximum_m': None,
                'standstills': 0, 'video': False, 'commit': None})
            entry['segments'] += 1
            if series is None or 'error' in series:
                continue
            entry['read'] += 1
            entry['duration_s'] += series['duration_s']
            entry['video'] = entry['video'] or bool(series.get('video'))
            entry['commit'] = entry['commit'] or series.get('commit')
            # The start of the drive: from the GPS when a segment has a fix, else from the comma clock; each
            # segment's own start minus the minutes before it.
            start = series.get('start_unix')
            if start is not None:
                start -= segment_number(name) * SEGMENT_SECONDS
                better = entry['start_unix'] is None or (series['start_from'] == 'gps') > (entry['start_from'] == 'gps')
                if better:
                    entry['start_unix'], entry['start_from'] = round(start, 1), series['start_from']
            if series['has_radar']:
                entry['with_radar'] += 1
                entry['measurements'] += series['measurements']
                entry['samples'] += series['target_share'] * series['duration_s']
                reading = series['reading_m']
                entry['minimum_m'] = reading['min'] if entry['minimum_m'] is None else min(entry['minimum_m'],
                                                                                           reading['min'])
                entry['maximum_m'] = reading['max'] if entry['maximum_m'] is None else max(entry['maximum_m'],
                                                                                           reading['max'])
                entry['standstills'] += len(series['standstills'])
        offset = calibration.get('radar_to_reference_m')
        drives = []
        for entry in by_route.values():
            entry['target_share'] = round(entry['samples'] / entry['duration_s'], 3) if entry['duration_s'] else 0.0
            del entry['samples']
            entry['has_radar'] = entry['with_radar'] > 0
            for key in ('minimum_m', 'maximum_m'):
                value = entry[key]
                entry[key.replace('_m', '_from_comma_m')] = None if value is None or offset is None \
                    else round(value + offset, 2)
            entry['duration_s'] = round(entry['duration_s'], 1)
            drives.append(entry)
        # Recordings by route counter (newest first), drives by start time (newest first).
        drives.sort(key=lambda d: (d['kind'] != 'recording', -(int(d['route'][:8], 16) if d['kind'] == 'recording'
                                                                  else (d['start_unix'] or 0))))
        summary = {'drives': drives, 'segments': len(folders),
                   'read': sum(d['read'] for d in drives), 'with_radar': sum(1 for d in drives if d['has_radar'])}
        with self._lock:
            self._summary = summary
            self._key = key
        return summary


def drive_detail(repo: Path, folders: dict[str, Path], route: str, calibration: dict) -> dict:
    """The readings of one drive, segment after segment, as distances from the comma (None without the
    calibration), with the standstill stretches and where the pictures are."""
    names = sorted((name for name in folders if route_of(name) == route), key=segment_number)
    if not names:
        raise WebError(404, 'unknown_drive', 'Unknown drive.')
    offset = calibration.get('radar_to_reference_m')
    segments = []
    t_offset = 0.0
    points = []
    for name in names:
        series = load_series(repo, name)
        if series is None or 'error' in series:
            segments.append({'segment': name, 'read': False})
            continue
        data = series['series']
        for t, reading, speed in zip(data['t_s'], data['reading_m'], data['speed_mps'], strict=True):
            points.append((t_offset + t, reading, speed))
        segments.append({'segment': name, 'read': True, 'from_s': round(t_offset, 1),
                         'duration_s': series['duration_s'], 'has_radar': series['has_radar'],
                         'video': series.get('video'), 'frames': series['frames']['count'],
                         'standstills': [dict(still, from_s=round(t_offset + still['from_s'], 2),
                                              to_s=round(t_offset + still['to_s'], 2),
                                              segment_from_s=still['from_s'])
                                         for still in series['standstills']],
                         'start_unix': series.get('start_unix'), 'start_from': series.get('start_from'),
                         'relative_speed_valid': series.get('relative_speed_valid', 0)})
        t_offset += series['duration_s']
    stride = max(1, len(points) // OVERVIEW_POINTS)
    shown = points[::stride]
    return {
        'route': route, 'segments': segments, 'calibration': calibration,
        'chart': {'t_s': [round(p[0], 1) for p in shown],
                  'reading_m': [p[1] for p in shown],
                  'from_comma_m': [None if p[1] is None or offset is None else round(p[1] + offset, 2) for p in shown],
                  'speed_kmh': [round(p[2] * 3.6, 1) for p in shown]},
        'step_m': READING_STEP_M,
    }


def frame_for(series: dict, segment_t_s: float) -> tuple[int, float] | None:
    """(frame index, its time from the first frame) of the road frame closest to a time of the segment."""
    times = series.get('frames', {}).get('t_s') or []
    if not times:
        return None
    index = min(range(len(times)), key=lambda i: abs(times[i] - segment_t_s))
    return index, round(times[index] - times[0], 3)


# -- calibration tests --------------------------------------------------------------------------------
class RadarTests:
    def __init__(self, repo: Path):
        self.path = repo / 'outputs' / 'web' / 'radar_tests.json'
        self._lock = threading.Lock()

    def tests(self) -> list[dict]:
        text = read_private(self.path)
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
        return [test for test in data if isinstance(test, dict)] if isinstance(data, list) else []

    def summary(self) -> dict:
        """The tests and what they say together: correction and distance radar -> comma (means), their spread,
        the range of distances tested."""
        tests = self.tests()
        kept = [test for test in tests if test.get('kept', True)]
        result = {'tests': tests, 'kept': len(kept), 'correction_m': None, 'radar_to_camera_m': None,
                  'spread_m': None, 'tested_from_m': None, 'tested_to_m': None, 'uncertainty_m': None}
        if not kept:
            return result
        corrections = [test['tape_radar_m'] - test['reading_m'] for test in kept]
        offsets = [test['tape_comma_m'] - test['tape_radar_m'] for test in kept]
        result['correction_m'] = round(statistics.mean(corrections), 2)
        result['radar_to_camera_m'] = round(statistics.mean(offsets), 2)
        result['spread_m'] = round(max(corrections) - min(corrections), 2)
        result['tested_from_m'] = min(test['tape_radar_m'] for test in kept)
        result['tested_to_m'] = max(test['tape_radar_m'] for test in kept)
        # Half a reading step for one test, less with more tests (they are not fully independent: kept honest).
        result['uncertainty_m'] = round(READING_STEP_M / 2 / max(1, len(kept)) ** 0.5, 2)
        return result

    def add(self, body, series_of) -> dict:
        """{"segment", "standstill" (index in the segment's standstills), "tape_radar_m", "tape_comma_m"}."""
        if not isinstance(body, dict) or set(body) != {'segment', 'standstill', 'tape_radar_m', 'tape_comma_m'}:
            raise WebError(400, 'invalid_param', 'Send {"segment", "standstill", "tape_radar_m", "tape_comma_m"}.')
        segment, index = body['segment'], body['standstill']
        if not isinstance(segment, str) or not (SEGMENT_NAME_RE.fullmatch(segment)
                                                or RECORDING_NAME_RE.fullmatch(segment)):
            raise WebError(400, 'invalid_param', 'Unknown recording.')
        series = series_of(segment)
        if series is None or 'error' in series:
            raise WebError(404, 'unknown_recording', 'The radar of this recording has not been read yet.')
        stills = series.get('standstills', [])
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(stills):
            raise WebError(400, 'invalid_param', 'Choose one of the moments when the car stood still.')
        tapes = {}
        for key in ('tape_radar_m', 'tape_comma_m'):
            value = body[key]
            low, high = TAPE_RANGE_M
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise WebError(400, 'invalid_param', 'Tape distances are in metres, between 0.3 and 100.')
            tapes[key] = round(float(value), 2)
        if tapes['tape_comma_m'] <= tapes['tape_radar_m']:
            raise WebError(400, 'invalid_param', 'The comma sits behind the radar: its distance must be the larger.')
        still = stills[index]
        test = {'id': secrets.token_hex(6), 'segment': segment, 'route': route_of(segment),
                'from_s': still['from_s'], 'to_s': still['to_s'], 'reading_m': still['reading_m'], **tapes,
                'correction_m': round(tapes['tape_radar_m'] - still['reading_m'], 2), 'kept': True,
                'created': round(time.time(), 1)}
        with self._lock:
            tests = self.tests()
            tests.append(test)
            write_private(self.path, json.dumps(tests, indent=1))
        return test

    def change(self, test_id: str, body) -> None:
        """{"kept": bool} keeps or leaves out a test; {"delete": true} removes it."""
        if not TEST_ID_RE.fullmatch(test_id) or not isinstance(body, dict) or len(body) != 1 or (
                'kept' in body and not isinstance(body['kept'], bool)) or (
                'delete' in body and body['delete'] is not True) or not ({'kept', 'delete'} & set(body)):
            raise WebError(400, 'invalid_param', 'Send {"kept": true|false} or {"delete": true}.')
        with self._lock:
            tests = self.tests()
            for test in tests:
                if test.get('id') == test_id:
                    break
            else:
                raise WebError(404, 'unknown_test', 'Unknown test.')
            if 'delete' in body:
                tests = [t for t in tests if t.get('id') != test_id]
            else:
                test['kept'] = body['kept']
            write_private(self.path, json.dumps(tests, indent=1))
