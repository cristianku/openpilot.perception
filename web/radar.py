"""The car's radar for the site: which drives and recordings have it, the readings of one drive as distances from
the comma, and the calibration tests in front of a wall (or a parked car).

Where the segments come from:
  drives      the drives folder of openpilot.distill this site reads (Settings > Drives);
  recordings  outputs/recordings/<route>--<segment>/, the short recordings copied from the comma for the tests.
Their radar is read once by the radar_index job (perception.radar.index) into outputs/radar/<segment>.json.
Every list of drives and recordings is sorted by the route counter, newest first: the comma increases it at
every start, while its clock can be months off.

Calibration tests (outputs/web/radar_tests.json, 600): a recording standing still in front of something (a
wall close up, or a parked car farther away), the radar's steady reading there, and the tape: radar (front
bumper) to it and comma to it. Every test gives
  correction        = tape radar - reading            (the radar's zero)
  radar to comma    = tape comma - tape radar
and the car's calibration is the mean over the tests kept. web/settings.py stores it with where it comes from:
"tests", "typed" by hand in Settings, or "none" for a car the owner set as having no radar. A test whose
recording is not on this server yet waits as a draft, tapes only (outputs/web/radar_drafts.json, 600): only the
owner picking its recording finishes it.

How far the calibration can be trusted (docs/design-radar-objects.md 4.2), from its source and the kept tests:
  no_radar    the owner said the car has no radar ("My car has no radar" in Settings);
  not_set     no distance from the radar to the comma;
  typed       numbers typed by hand;
  ready       from the tests, one of them at 5 m or more (where the labels will be), their corrections at most
              0.5 m apart;
  close_only  from the tests, otherwise.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import statistics
import threading
import time
from pathlib import Path

from web.common import WebError, read_private, write_private
from web.settings import NO_RADAR, RADAR_CORRECTION_RANGE_M, RADAR_TO_CAMERA_RANGE_M

# A segment of a drive is a minute long: segment k starts about k minutes after the drive.
SEGMENT_SECONDS = 60.0
SEGMENT_NAME_RE = re.compile(r'^[0-9a-f]{18,24}$')
RECORDING_NAME_RE = re.compile(r'^([0-9a-f]{8}--[0-9a-f]{10})--([0-9]{1,4})$')
TEST_ID_RE = re.compile(r'^[0-9a-f]{12}$')
DRAFT_ID_RE = TEST_ID_RE
# Readings come in 0.25 m steps: half a step is the uncertainty of one reading.
READING_STEP_M = 0.25
TAPE_RANGE_M = (0.3, 100.0)
OVERVIEW_POINTS = 1500
TARGETS = ('wall', 'car')
# Confirmed pairs become labels at these distances, so the calibration counts as checked for them only after a
# test at FAR_TEST_M or more.
LABEL_RANGE_M = (5.0, 30.0)
FAR_TEST_M = LABEL_RANGE_M[0]
# Kept corrections farther apart than this disagree beyond what the radar's 25 cm steps explain.
DISAGREE_M = 0.5
# Tape-only tests kept while their recording is out of reach: a few, never a backlog.
MAX_DRAFTS = 5
COUNTER_MAX = 0xffffffff
# The jobs that read or bring radar readings (web/actions.py): while one runs, Home shows it.
RADAR_ACTIONS = ('radar_index', 'fetch_recordings')
# The next step on Home, first that applies first (docs/design-radar-objects.md 2.1); "caught_up" when none does.
NEXT_CASES = ('radar_not_set', 'test_waiting', 'radar_reading', 'no_radar_drive', 'far_test', 'tests_disagree',
              'use_tests', 'no_radar')
MAX_ALSO = 2


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


def route_number(route: str) -> int:
    """The route counter of a drive or a recording ("drive no. 301"): the first 8 hex digits of both names."""
    return int(route[:8], 16)


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
        # The software versions (commits) that gave readings somewhere: a drive without readings on one of them
        # had nothing ahead, one on another most likely predates the software that sends the radar.
        radar_commits = set()
        for name in folders:
            series = load_series(self.repo, name)
            route = route_of(name)
            entry = by_route.setdefault(route, {
                'route': route, 'number': route_number(route),
                'kind': 'recording' if RECORDING_NAME_RE.fullmatch(name) else 'drive',
                'segments': 0, 'read': 0, 'with_radar': 0, 'start_unix': None, 'start_from': None,
                'duration_s': 0.0, 'measurements': 0, 'samples': 0.0, 'minimum_m': None, 'maximum_m': None,
                'standstills': 0, 'video': False, 'commit': None, 'commits': set()})
            entry['segments'] += 1
            if series is None or 'error' in series:
                continue
            entry['read'] += 1
            entry['duration_s'] += series['duration_s']
            entry['video'] = entry['video'] or bool(series.get('video'))
            entry['commit'] = entry['commit'] or series.get('commit')
            entry['commits'].add(series.get('commit'))
            # The start of the drive: from the GPS when a segment has a fix, else from the comma clock; each
            # segment's own start minus the minutes before it.
            start = series.get('start_unix')
            if start is not None:
                start -= segment_number(name) * SEGMENT_SECONDS
                better = entry['start_unix'] is None or (series['start_from'] == 'gps') > (entry['start_from'] == 'gps')
                if better:
                    entry['start_unix'], entry['start_from'] = round(start, 1), series['start_from']
            if series['has_radar']:
                radar_commits.add(series.get('commit'))
                entry['with_radar'] += 1
                entry['measurements'] += series['measurements']
                entry['samples'] += series['target_share'] * series['duration_s']
                reading = series['reading_m']
                entry['minimum_m'] = reading['min'] if entry['minimum_m'] is None else min(entry['minimum_m'],
                                                                                           reading['min'])
                entry['maximum_m'] = reading['max'] if entry['maximum_m'] is None else max(entry['maximum_m'],
                                                                                           reading['max'])
                entry['standstills'] += len(series['standstills'])
        radar_commits.discard(None)
        offset = calibration.get('radar_to_reference_m')
        drives = []
        for entry in by_route.values():
            entry['target_share'] = round(entry['samples'] / entry['duration_s'], 3) if entry['duration_s'] else 0.0
            del entry['samples']
            entry['has_radar'] = entry['with_radar'] > 0
            commits = entry.pop('commits')
            # Never a reason the site cannot know: none while nothing of the drive is read yet.
            entry['no_radar_reason'] = None
            if not entry['has_radar'] and entry['read']:
                entry['no_radar_reason'] = 'same_software' if commits & radar_commits else 'old_software'
            # Not "key": that name holds the key of the cache, stored below.
            for field in ('minimum_m', 'maximum_m'):
                value = entry[field]
                entry[field.replace('_m', '_from_comma_m')] = None if value is None or offset is None \
                    else round(value + offset, 2)
            entry['duration_s'] = round(entry['duration_s'], 1)
            drives.append(entry)
        # Newest first by route counter, drives and recordings alike (a copied minute of a drive shares its
        # counter: the drive comes first).
        drives.sort(key=lambda d: (-d['number'], d['kind'], d['route']))
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
                         'longest_still_s': longest_still(data['t_s'], data['speed_mps']),
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
        'route': route, 'number': route_number(route),
        'kind': 'recording' if RECORDING_NAME_RE.fullmatch(names[0]) else 'drive',
        'segments': segments, 'calibration': calibration,
        'chart': {'t_s': [round(p[0], 1) for p in shown],
                  'reading_m': [p[1] for p in shown],
                  'from_comma_m': [None if p[1] is None or offset is None else round(p[1] + offset, 2) for p in shown],
                  'speed_kmh': [round(p[2] * 3.6, 1) for p in shown]},
        'step_m': READING_STEP_M,
    }


def longest_still(times: list[float], speeds: list[float]) -> float:
    """The longest stretch of a segment with the car standing still, in seconds (as perception.radar.series
    finds its standstills): the page says "never stood still for 2 s" from it."""
    from perception.radar.series import STANDSTILL_SPEED_MPS

    best = 0.0
    start = None
    for index in range(len(times) + 1):
        still = index < len(times) and is_number(speeds[index]) and abs(speeds[index]) < STANDSTILL_SPEED_MPS
        if still and start is None:
            start = index
        elif not still and start is not None:
            best = max(best, times[index - 1] - times[start])
            start = None
    return round(best, 1)


def frame_for(series: dict, segment_t_s: float) -> tuple[int, float] | None:
    """(frame index, its time from the first frame) of the road frame closest to a time of the segment."""
    times = series.get('frames', {}).get('t_s') or []
    if not times:
        return None
    index = min(range(len(times)), key=lambda i: abs(times[i] - segment_t_s))
    return index, round(times[index] - times[0], 3)


# -- calibration tests --------------------------------------------------------------------------------
def is_number(value) -> bool:
    """A real number: never a bool, NaN or an infinity (json.loads reads NaN from a file edited by hand, and the
    page's JSON can never carry it)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number_or_none(value):
    return value if is_number(value) else None


def half_up(value: float) -> float:
    """Rounded to the centimetre half up, as the page rounds: round() goes to the even one (0.125 m would be
    0.12)."""
    return math.floor(value * 100 + 0.5) / 100


def tapes_of(body: dict) -> dict:
    """The two tape distances of a test or a draft, checked: the comma behind the radar, as on a car."""
    tapes = {}
    low, high = TAPE_RANGE_M
    for key in ('tape_radar_m', 'tape_comma_m'):
        value = body[key]
        if not is_number(value) or not low <= value <= high:
            raise WebError(400, 'invalid_param', 'Tape distances are in metres, between 0.3 and 100.')
        tapes[key] = round(float(value), 2)
    if tapes['tape_comma_m'] <= tapes['tape_radar_m']:
        raise WebError(400, 'invalid_param', 'The comma sits behind the radar: its distance must be the larger.')
    # The same bound as the distance typed in Settings: a comma farther back is a tape gone astray.
    if round(tapes['tape_comma_m'] - tapes['tape_radar_m'], 2) > RADAR_TO_CAMERA_RANGE_M[1]:
        raise WebError(400, 'invalid_param', 'The comma is more than 5 m behind the radar: check the tape.')
    return tapes


def target_of(value) -> str:
    if value not in TARGETS:
        raise WebError(400, 'invalid_param', 'The target is "wall" or "car".')
    return value


def test_view(test: dict) -> dict:
    """A saved test as the page sees it, with its recording number. Tests saved before the parked-car test
    existed were all in front of a wall."""
    route = test.get('route') or route_of(test['segment'])
    return {'id': test.get('id'), 'segment': test['segment'], 'route': route, 'recording_number': route_number(route),
            'target': test.get('target') if test.get('target') in TARGETS else 'wall',
            'from_s': number_or_none(test.get('from_s')), 'to_s': number_or_none(test.get('to_s')),
            'reading_m': test['reading_m'], 'tape_radar_m': test['tape_radar_m'], 'tape_comma_m': test['tape_comma_m'],
            'correction_m': round(test['tape_radar_m'] - test['reading_m'], 2), 'kept': test.get('kept', True) is True,
            'created': number_or_none(test.get('created'))}


def stored_list(path: Path, what: str | None = None) -> list:
    """The entries of a small JSON list file, as saved ([] when there is none). A file that is there but holds no
    list reads as [] for the page; before a write (`what` names it in the message) it is refused instead, so what
    it holds is never written over."""
    text = read_private(path)
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list):
        return data
    if what is not None:
        raise WebError(500, 'unreadable', 'Cannot read the ' + what + ' saved on this server: the file is damaged. '
                       'It is kept as it is; nothing was saved.')
    return []


def usable(test) -> bool:
    """A test with what the means need (a file edited by hand may have less: it is skipped, never dropped)."""
    return isinstance(test, dict) and isinstance(test.get('id'), str) and isinstance(test.get('segment'), str) \
        and (SEGMENT_NAME_RE.fullmatch(test['segment']) or RECORDING_NAME_RE.fullmatch(test['segment'])) is not None \
        and test.get('route') in (None, route_of(test['segment'])) \
        and all(is_number(test.get(key)) for key in ('reading_m', 'tape_radar_m', 'tape_comma_m'))


class RadarTests:
    TEST_KEYS = {'segment', 'standstill', 'tape_radar_m', 'tape_comma_m'}

    def __init__(self, repo: Path):
        self.path = repo / 'outputs' / 'web' / 'radar_tests.json'
        self._lock = threading.Lock()

    def tests(self) -> list[dict]:
        return [test for test in stored_list(self.path) if usable(test)]

    def summary(self) -> dict:
        """The tests and what they say together: correction and distance radar -> comma (means), their spread,
        the range of distances tested."""
        tests = [test_view(test) for test in self.tests()]
        kept = [test for test in tests if test['kept']]
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
        result['uncertainty_m'] = half_up(READING_STEP_M / 2 / max(1, len(kept)) ** 0.5)
        return result

    def add(self, body, series_of) -> dict:
        """{"segment", "standstill" (index in the segment's standstills), "tape_radar_m", "tape_comma_m"} and
        optionally "target" ("wall", the default, or "car")."""
        if not isinstance(body, dict) or not self.TEST_KEYS <= set(body) <= self.TEST_KEYS | {'target'}:
            raise WebError(400, 'invalid_param', 'Send {"segment", "standstill", "tape_radar_m", "tape_comma_m"} '
                           'and optionally "target": "wall" or "car".')
        segment, index = body['segment'], body['standstill']
        target = target_of(body.get('target', 'wall'))
        if not isinstance(segment, str) or not (SEGMENT_NAME_RE.fullmatch(segment)
                                                or RECORDING_NAME_RE.fullmatch(segment)):
            raise WebError(400, 'invalid_param', 'Unknown recording.')
        tapes = tapes_of(body)
        series = series_of(segment)
        if series is None or 'error' in series:
            raise WebError(404, 'unknown_recording', 'The radar of this recording has not been read yet.')
        stills = series.get('standstills', [])
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(stills):
            raise WebError(400, 'invalid_param', 'Choose one of the moments when the car stood still.')
        still = stills[index]
        correction = round(tapes['tape_radar_m'] - still['reading_m'], 2)
        # Beyond what can be typed in Settings: most likely the wrong recording or a slipped tape, and one such
        # test would pull the mean of the others with it.
        low, high = RADAR_CORRECTION_RANGE_M
        if not low <= correction <= high:
            raise WebError(400, 'invalid_param', 'The tape and the radar reading are more than 1 m apart: check the '
                           'tape, or that this is the recording of the test.')
        test = {'id': secrets.token_hex(6), 'segment': segment, 'route': route_of(segment), 'target': target,
                'from_s': still['from_s'], 'to_s': still['to_s'], 'reading_m': still['reading_m'], **tapes,
                'correction_m': correction, 'kept': True, 'created': round(time.time(), 1)}
        with self._lock:
            tests = stored_list(self.path, 'radar tests')
            tests.append(test)
            write_private(self.path, json.dumps(tests, indent=1))
        return test_view(test)

    def change(self, test_id: str, body) -> None:
        """{"kept": bool} keeps or leaves out a test; {"delete": true} removes it."""
        if not TEST_ID_RE.fullmatch(test_id) or not isinstance(body, dict) or len(body) != 1 or (
                'kept' in body and not isinstance(body['kept'], bool)) or (
                'delete' in body and body['delete'] is not True) or not ({'kept', 'delete'} & set(body)):
            raise WebError(400, 'invalid_param', 'Send {"kept": true|false} or {"delete": true}.')
        with self._lock:
            tests = stored_list(self.path, 'radar tests')
            for test in tests:
                if usable(test) and test['id'] == test_id:
                    break
            else:
                raise WebError(404, 'unknown_test', 'Unknown test.')
            if 'delete' in body:
                tests = [t for t in tests if t is not test]
            else:
                test['kept'] = body['kept']
            write_private(self.path, json.dumps(tests, indent=1))


def kept_ids(summary: dict) -> list[str]:
    return [test['id'] for test in summary['tests'] if test['kept']]


def calibration_view(values: dict, summary: dict) -> dict:
    """The calibration as the page and the status see it: the values in use and their source and version
    (Settings.radar_state), how far the kept tests checked them, and what the tests give, so the page can offer
    them when the numbers were typed."""
    kept = [test for test in summary['tests'] if test['kept']]
    tested = sorted(test['tape_radar_m'] for test in kept)
    spread = summary['spread_m']
    disagree = spread is not None and spread > DISAGREE_M
    source = values['source']
    if source == NO_RADAR:
        status = 'no_radar'
    elif values['radar_to_camera_m'] is None:
        status = 'not_set'
    elif source == 'typed':
        status = 'typed'
    elif source == 'tests' and tested and tested[-1] >= FAR_TEST_M and not disagree:
        status = 'ready'
    else:
        status = 'close_only'
    return {
        'radar_to_camera_m': values['radar_to_camera_m'], 'radar_correction_m': values['radar_correction_m'],
        'radar_to_reference_m': values['radar_to_reference_m'], 'source': source, 'version': values['version'],
        'status': status, 'far_test_done': status == 'ready', 'tests_disagree': disagree,
        # The tests' uncertainty belongs to numbers that come from them, never to numbers typed by hand.
        'uncertainty_m': summary['uncertainty_m'] if source == 'tests' else None,
        'spread_m': spread, 'tested_m': tested, 'label_range_m': list(LABEL_RANGE_M),
        'tests_give': {'radar_to_camera_m': summary['radar_to_camera_m'],
                       'radar_correction_m': summary['correction_m']} if kept else None,
    }


# -- drafts -------------------------------------------------------------------------------------------
DRAFTS_WHAT = 'radar tests waiting for their recording'


class RadarDrafts:
    """Tape-only tests waiting for their recording (the comma was out of reach): at most MAX_DRAFTS. A draft is
    deleted when its test is saved or from the page; it never turns into a test by itself."""
    KEYS = {'target', 'tape_radar_m', 'tape_comma_m'}

    def __init__(self, repo: Path):
        self.path = repo / 'outputs' / 'web' / 'radar_drafts.json'
        self._lock = threading.Lock()

    @staticmethod
    def _usable(draft) -> bool:
        return isinstance(draft, dict) and isinstance(draft.get('id'), str) and draft.get('target') in TARGETS \
            and all(is_number(draft.get(key)) for key in ('tape_radar_m', 'tape_comma_m'))

    def drafts(self) -> list[dict]:
        """Newest first."""
        return [{'id': draft['id'], 'created': number_or_none(draft.get('created')), 'target': draft['target'],
                 'tape_radar_m': draft['tape_radar_m'], 'tape_comma_m': draft['tape_comma_m'],
                 'marker_counter': draft.get('marker_counter') if is_number(draft.get('marker_counter')) else None}
                for draft in reversed(stored_list(self.path)) if self._usable(draft)]

    def get(self, draft_id: str) -> dict | None:
        return next((draft for draft in self.drafts() if draft['id'] == draft_id), None)

    def add(self, body) -> dict:
        """{"target", "tape_radar_m", "tape_comma_m"} and optionally "marker_counter": the newest recording
        number on the comma when the car was parked, or null when the comma was out of reach."""
        if not isinstance(body, dict) or not self.KEYS <= set(body) <= self.KEYS | {'marker_counter'}:
            raise WebError(400, 'invalid_param', 'Send {"target", "tape_radar_m", "tape_comma_m"} and optionally '
                           '"marker_counter".')
        target = target_of(body['target'])
        tapes = tapes_of(body)
        marker = body.get('marker_counter')
        if marker is not None and (isinstance(marker, bool) or not isinstance(marker, int)
                                   or not 0 <= marker <= COUNTER_MAX):
            raise WebError(400, 'invalid_param', 'marker_counter is a recording number, or null.')
        draft = {'id': secrets.token_hex(6), 'created': round(time.time(), 1), 'target': target, **tapes,
                 'marker_counter': marker}
        with self._lock:
            drafts = stored_list(self.path, DRAFTS_WHAT)
            if sum(1 for stored in drafts if self._usable(stored)) >= MAX_DRAFTS:
                raise WebError(409, 'too_many_drafts', str(MAX_DRAFTS) + ' tests are already waiting for their '
                               'recording: finish or delete one first.')
            drafts.append(draft)
            write_private(self.path, json.dumps(drafts, indent=1))
        return self.get(draft['id'])

    def change(self, draft_id: str, body) -> None:
        """{"delete": true} removes a draft."""
        if not DRAFT_ID_RE.fullmatch(draft_id) or body != {'delete': True}:
            raise WebError(400, 'invalid_param', 'Send {"delete": true}.')
        if not self.remove(draft_id):
            raise WebError(404, 'unknown_draft', 'This test is no longer waiting: it was finished or deleted.')

    def remove(self, draft_id: str) -> bool:
        with self._lock:
            drafts = stored_list(self.path, DRAFTS_WHAT)
            left = [draft for draft in drafts if not (self._usable(draft) and draft['id'] == draft_id)]
            if len(left) == len(drafts):
                return False
            write_private(self.path, json.dumps(left, indent=1))
        return True


# -- the next step on Home ----------------------------------------------------------------------------
def radar_overview(summary: dict) -> dict | None:
    """The radar tile of Home: drives and recordings with readings; None while nothing has been read."""
    if not summary['read']:
        return None
    return {'drives_with_radar': sum(1 for d in summary['drives'] if d['kind'] == 'drive' and d['has_radar']),
            'recordings_with_radar': sum(1 for d in summary['drives'] if d['kind'] == 'recording' and d['has_radar']),
            'read': summary['read']}


def next_step(calibration: dict, drafts: list[dict], summary: dict, jobs: list[dict]) -> dict:
    """The one thing to do next (Home), from what is already known: it never reads a log or starts a job.
    "also" names up to two more cases that apply, in the same order."""
    with_radar = [d for d in summary['drives'] if d['kind'] == 'drive' and d['has_radar']]
    status = calibration['status']
    far_tested = any(distance >= FAR_TEST_M for distance in calibration['tested_m'])
    applies = {
        'radar_not_set': status == 'not_set',
        'test_waiting': bool(drafts),
        'radar_reading': any(job.get('state') == 'running' and job.get('action') in RADAR_ACTIONS for job in jobs),
        # A car set as having no radar has no drive to record with it.
        'no_radar_drive': status != 'no_radar' and not with_radar,
        # Whatever the source, until a kept test checked the distances where the labels will be.
        'far_test': status in ('close_only', 'typed') and not far_tested,
        # From the tests, with one at 5 m or more, and still close only: their corrections disagree. So Home never
        # says "caught up" while the calibration is not ready.
        'tests_disagree': status == 'close_only' and bool(calibration.get('tests_disagree')),
        # Typed by hand while tests are kept: only numbers from the tests make confirmed pairs count as labels.
        'use_tests': status == 'typed' and calibration.get('tests_give') is not None,
        'no_radar': status == 'no_radar',
    }
    cases = [case for case in NEXT_CASES if applies[case]] or ['caught_up']
    step = {'case': cases[0], 'also': cases[1:1 + MAX_ALSO], 'drives_with_radar': len(with_radar)}
    if drafts:
        step['draft_created'] = drafts[0]['created']
    if with_radar:
        # Already newest first by route counter.
        step['newest_radar_drive'] = with_radar[0]['route']
    return step
