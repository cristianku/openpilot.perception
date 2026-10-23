"""The car's radar for the site: which drives and recordings have it, the readings of one drive as distances from
the comma, and the calibration tests in front of a wall (or a parked car).

Where the segments come from:
  drives      the drives folder of openpilot.distill this site reads (Settings > Drives);
  recordings  outputs/recordings/<route>--<segment>/, the short recordings copied from the comma for the tests.
Their radar is read once by the radar_index job (perception.radar.index) into outputs/radar/<segment>.json.
Every list of drives and recordings is sorted by the route counter, newest first: the comma increases it at
every start, while its clock can be months off.

Calibration tests (the store, outputs/web/perception.sqlite, web/store.py): the car standing still in front of
something (a wall close up, or a parked car farther away), the radar's steady reading there, and the tape: radar
(front bumper) to it and comma to it. The reading comes from a recording on this server (a standstill of it), or
was captured live on the phone by the app (source "app", POST /api/app/v1/tests, web/API.md: the comma's recording
of that moment is kept with it, to check it against later). Every test gives
  correction        = tape radar - reading            (the radar's zero)
  radar to comma    = tape comma - tape radar
and the car's calibration is the mean over the tests kept, whatever they come from. web/settings.py stores it with
where it comes from: "tests", "typed" by hand in Settings, or "none" for a car the owner set as having no radar.
A test whose recording is not on this server yet waits as a draft, tapes only (the store too): only the owner
picking its recording finishes it.

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
import unicodedata
from pathlib import Path

from web.common import WebError
from web.settings import NO_RADAR, RADAR_CORRECTION_RANGE_M, RADAR_TO_CAMERA_RANGE_M
from web.store import (COUNTER_MAX, LEGACY_DRAFTS, LEGACY_TESTS, RECORDING_NAME_RE, SEGMENT_NAME_RE, TARGETS, Store,
                       is_number, number_or_none, route_of)

# A segment of a drive is a minute long: segment k starts about k minutes after the drive.
SEGMENT_SECONDS = 60.0
TEST_ID_RE = re.compile(r'^[0-9a-f]{12}$')
DRAFT_ID_RE = TEST_ID_RE
# Readings come in 0.25 m steps: half a step is the uncertainty of one reading.
READING_STEP_M = 0.25
TAPE_RANGE_M = (0.3, 100.0)
OVERVIEW_POINTS = 1500
# Confirmed pairs become labels at these distances, so the calibration counts as checked for them only after a
# test at FAR_TEST_M or more.
LABEL_RANGE_M = (5.0, 30.0)
FAR_TEST_M = LABEL_RANGE_M[0]
# Kept corrections farther apart than this disagree beyond what the radar's 25 cm steps explain.
DISAGREE_M = 0.5
# Tape-only tests kept while their recording is out of reach: a few, never a backlog.
MAX_DRAFTS = 5
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
# A test from the app (POST /api/app/v1/tests, web/API.md): exactly these keys, as SiteApi.kt sends them.
APP_TEST_KEYS = frozenset({'client_id', 'created_unix', 'target', 'tape_radar_m', 'tape_comma_m', 'capture', 'comma',
                           'note'})
APP_CAPTURE_KEYS = frozenset({'started_unix', 'duration_s', 'samples', 'with_reading', 'reading_m', 'steady_share',
                              'histogram', 'speed_max_mps', 'onroad'})
APP_COMMA_KEYS = frozenset({'route', 'git_commit'})
CLIENT_ID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
COMMA_ROUTE_RE = re.compile(r'[0-9a-f]{8}--[0-9a-f]{10}')
GIT_COMMIT_RE = re.compile(r'[0-9a-f]{7,40}')
HISTOGRAM_KEY_RE = re.compile(r'[0-9]{1,3}\.[0-9]{2}')
# A phone's time: from 2017 to 2100, in seconds since 1970.
UNIX_RANGE = (1_500_000_000.0, 4_102_444_800.0)
MAX_CAPTURE_S = 600.0
MAX_SAMPLES = 100_000
MAX_READING_M = 300.0
MAX_SPEED_MPS = 100.0
MAX_NOTE = 500
APP_TEST_FIELDS = ('client_id', 'target', 'route', 'reading_m', 'tape_radar_m', 'tape_comma_m', 'correction_m',
                   'made_unix', 'capture', 'git_commit', 'note')
TEST_COLUMNS = ('id', 'source', 'segment', 'route', 'target', 'from_s', 'to_s', 'reading_m', 'tape_radar_m',
                'tape_comma_m', 'correction_m', 'kept', 'created', 'client_id', 'made_unix', 'capture', 'git_commit',
                'note')


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


def correction_of(tapes: dict, reading: float, what: str) -> float:
    correction = round(tapes['tape_radar_m'] - reading, 2)
    # Beyond what can be typed in Settings: most likely the wrong recording or a slipped tape, and one such test
    # would pull the mean of the others with it.
    low, high = RADAR_CORRECTION_RANGE_M
    if not low <= correction <= high:
        raise WebError(400, 'invalid_param', 'The tape and the radar reading are more than 1 m apart: check the '
                       'tape' + what + '.')
    return correction


def _number(value, low: float, high: float, message: str, *, above: bool = False) -> float:
    """A number from low (excluded with `above`) to high, or a refusal with the message."""
    if not is_number(value) or value > high or value < low or (above and value == low):
        raise WebError(400, 'invalid_param', message)
    return float(value)


def _count(value, high: int, message: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= high:
        raise WebError(400, 'invalid_param', message)
    return value


def _plain_text(value) -> bool:
    """Text a person typed: no control characters but new lines and tabs, no lone surrogates."""
    return isinstance(value, str) and not any(unicodedata.category(character) in ('Cc', 'Cs')
                                              and character not in '\n\t' for character in value)


def app_test_of(body) -> dict:
    """The fields of a test from the app (POST /api/app/v1/tests, web/API.md), every one checked: a refusal (400
    invalid_param, with a sentence the app shows) otherwise. The capture must be one the app itself accepts: a
    target at least half of the time and one reading 80% of it (perception/radar/series.py)."""
    from perception.radar.series import STANDSTILL_MIN_MODE_SHARE, STANDSTILL_MIN_TARGET_SHARE

    if not isinstance(body, dict) or set(body) != APP_TEST_KEYS:
        raise WebError(400, 'invalid_param', 'Send exactly {"client_id", "created_unix", "target", "tape_radar_m", '
                       '"tape_comma_m", "capture", "comma", "note"}.')
    client_id = body['client_id']
    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        raise WebError(400, 'invalid_param', 'client_id is the id the app gave the test (a UUID).')
    made = _number(body['created_unix'], *UNIX_RANGE, 'created_unix is when the test was made, in seconds since 1970.')
    target = target_of(body['target'])
    tapes = tapes_of(body)
    capture = body['capture']
    if not isinstance(capture, dict) or set(capture) != APP_CAPTURE_KEYS:
        raise WebError(400, 'invalid_param', 'capture has exactly {"started_unix", "duration_s", "samples", '
                       '"with_reading", "reading_m", "steady_share", "histogram", "speed_max_mps", "onroad"}.')
    started = _number(capture['started_unix'], *UNIX_RANGE,
                      'capture.started_unix is when the capture started, in seconds since 1970.')
    duration = _number(capture['duration_s'], 0.0, MAX_CAPTURE_S,
                       'capture.duration_s is the length of the capture: more than 0 s, at most 600 s.', above=True)
    samples = _count(capture['samples'], MAX_SAMPLES, 'capture.samples is the number of radar lines, at most 100000.')
    with_reading = _count(capture['with_reading'], samples,
                          'capture.with_reading is how many radar lines had a reading: at most capture.samples.')
    if capture['reading_m'] is None:
        raise WebError(400, 'invalid_param', 'The capture has no steady reading: it cannot be a test.')
    reading = round(_number(capture['reading_m'], 0.0, MAX_READING_M,
                            'capture.reading_m is the steady reading in metres: more than 0, at most 300.',
                            above=True), 2)
    share = _number(capture['steady_share'], 0.0, 1.0, 'capture.steady_share is a share, from 0 to 1.')
    histogram = capture['histogram']
    if not isinstance(histogram, dict) or not all(
            HISTOGRAM_KEY_RE.fullmatch(key) and isinstance(count, int) and not isinstance(count, bool) and count >= 1
            for key, count in histogram.items()):
        raise WebError(400, 'invalid_param', 'capture.histogram counts the radar lines of each reading, '
                       'as {"2.25": 176}.')
    if sum(histogram.values()) != with_reading:
        raise WebError(400, 'invalid_param', 'capture.histogram must count every radar line with a reading '
                       '(capture.with_reading).')
    key = f'{reading:.2f}'
    if key not in histogram or abs(histogram[key] / with_reading - share) > 0.005 + 1e-9:
        raise WebError(400, 'invalid_param', 'capture.reading_m and capture.steady_share must match '
                       'capture.histogram.')
    if with_reading < STANDSTILL_MIN_TARGET_SHARE * samples or share < STANDSTILL_MIN_MODE_SHARE:
        raise WebError(400, 'invalid_param', 'The capture was not steady enough to be a test: the radar must see '
                       'the target at least half of the time, and one reading 80% of it.')
    speed = capture['speed_max_mps']
    if speed is not None:
        speed = _number(speed, 0.0, MAX_SPEED_MPS, 'capture.speed_max_mps is the highest speed in m/s, or null.')
    if not isinstance(capture['onroad'], bool):
        raise WebError(400, 'invalid_param', 'capture.onroad is true or false.')
    comma = body['comma']
    if not isinstance(comma, dict) or set(comma) != APP_COMMA_KEYS:
        raise WebError(400, 'invalid_param', 'comma has exactly {"route", "git_commit"}.')
    route = comma['route']
    if route is not None and (not isinstance(route, str) or not COMMA_ROUTE_RE.fullmatch(route)):
        raise WebError(400, 'invalid_param', 'comma.route is the comma\'s recording during the capture, as '
                       '"00000134--a5c8197883", or null.')
    commit = comma['git_commit']
    if commit is not None and (not isinstance(commit, str) or not GIT_COMMIT_RE.fullmatch(commit)):
        raise WebError(400, 'invalid_param', 'comma.git_commit is the openpilot commit of the comma (7 to 40 hex '
                       'digits), or null.')
    note = body['note']
    if not _plain_text(note) or len(note) > MAX_NOTE:
        raise WebError(400, 'invalid_param', 'note is a text of at most 500 characters.')
    correction = correction_of(tapes, reading, '')
    kept = {'started_unix': started, 'duration_s': duration, 'samples': samples, 'with_reading': with_reading,
            'reading_m': reading, 'steady_share': share, 'histogram': dict(sorted(histogram.items())),
            'speed_max_mps': speed, 'onroad': capture['onroad']}
    return {'client_id': client_id, 'target': target, 'route': route, 'reading_m': reading, **tapes,
            'correction_m': correction, 'made_unix': made,
            'capture': json.dumps(kept, sort_keys=True, separators=(',', ':')), 'git_commit': commit, 'note': note}


def test_view(test: dict) -> dict:
    """A saved test as the page sees it, with its recording number. A test from the app (source "app") also has
    its client_id, when it was made on the phone (made_unix), its note and the comma's commit; its route is the
    comma's recording during the capture (null when there was none), its segment and moment null."""
    segment = test.get('segment')
    route = test.get('route') or (route_of(segment) if segment else None)
    view = {'id': test.get('id'), 'segment': segment, 'route': route,
            'recording_number': route_number(route) if route else None,
            'target': test.get('target') if test.get('target') in TARGETS else 'wall',
            'from_s': number_or_none(test.get('from_s')), 'to_s': number_or_none(test.get('to_s')),
            'reading_m': test['reading_m'], 'tape_radar_m': test['tape_radar_m'], 'tape_comma_m': test['tape_comma_m'],
            'correction_m': round(test['tape_radar_m'] - test['reading_m'], 2), 'kept': test.get('kept', True) is True,
            'created': number_or_none(test.get('created'))}
    if test.get('source') == 'app':
        view.update({'source': 'app', 'client_id': test.get('client_id'),
                     'made_unix': number_or_none(test.get('made_unix')), 'note': test.get('note') or '',
                     'git_commit': test.get('git_commit')})
    return view


def test_row(row: dict) -> dict:
    """A test of the store as the means and the views read it."""
    return {**row, 'kept': bool(row['kept'])}


def new_id(db, table: str) -> str:
    while True:
        candidate = secrets.token_hex(6)
        if db.execute('SELECT 1 FROM ' + table + ' WHERE id = ?', (candidate,)).fetchone() is None:
            return candidate


def insert_test(db, test: dict) -> None:
    db.execute('INSERT INTO radar_tests (' + ', '.join(TEST_COLUMNS) + ') VALUES ('
               + ', '.join('?' * len(TEST_COLUMNS)) + ')',
               [int(test['kept']) if key == 'kept' else test.get(key) for key in TEST_COLUMNS])


class RadarTests:
    """The calibration tests, in the store (web/store.py, table radar_tests): from recordings (POST
    /api/radar/tests) and from the app (POST /api/app/v1/tests), in the order they were saved."""
    TEST_KEYS = {'segment', 'standstill', 'tape_radar_m', 'tape_comma_m'}

    def __init__(self, repo: Path, store: Store | None = None):
        # The JSON file of earlier versions, imported into the store the first time it is seen.
        self.path = repo / 'outputs' / 'web' / LEGACY_TESTS
        self._store = store

    @property
    def store(self) -> Store:
        return self._store or Store.at(self.path.parent)

    def tests(self) -> list[dict]:
        store = self.store
        store.check(LEGACY_TESTS)
        return [test_row(row) for row in store.rows('SELECT * FROM radar_tests ORDER BY seq')
                if all(is_number(row[key]) for key in ('reading_m', 'tape_radar_m', 'tape_comma_m'))]

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
        correction = correction_of(tapes, still['reading_m'], ', or that this is the recording of the test')
        test = {'source': 'recording', 'segment': segment, 'route': route_of(segment), 'target': target,
                'from_s': still['from_s'], 'to_s': still['to_s'], 'reading_m': still['reading_m'], **tapes,
                'correction_m': correction, 'kept': True, 'created': round(time.time(), 1)}
        store = self.store
        store.require(LEGACY_TESTS)
        with store.write() as db:
            test['id'] = new_id(db, 'radar_tests')
            insert_test(db, test)
        return test_view(test)

    def add_from_app(self, body) -> tuple[dict, bool]:
        """A test made with the app, the reading captured live on the phone (app_test_of). Idempotent on its
        client_id: sent again (a reply lost on the way), it is the test already stored, never a second one, and
        (stored, False) comes back; another test under a client_id already taken is refused (409)."""
        fields = app_test_of(body)
        store = self.store
        store.require(LEGACY_TESTS)
        with store.write() as db:
            found = db.execute('SELECT * FROM radar_tests WHERE client_id = ?', (fields['client_id'],)).fetchone()
            if found is not None:
                stored = test_row(dict(found))
                if any(stored[key] != value for key, value in fields.items()):
                    raise WebError(409, 'client_id_taken', 'Another test was already saved on the site with the id '
                                   'of this one.')
                return stored, False
            test = {'source': 'app', 'segment': None, 'from_s': None, 'to_s': None, 'kept': True,
                    'created': round(time.time(), 1), **fields}
            test['id'] = new_id(db, 'radar_tests')
            insert_test(db, test)
        return test, True

    def change(self, test_id: str, body) -> None:
        """{"kept": bool} keeps or leaves out a test; {"delete": true} removes it (a test from the app can then
        be sent again)."""
        if not TEST_ID_RE.fullmatch(test_id) or not isinstance(body, dict) or len(body) != 1 or (
                'kept' in body and not isinstance(body['kept'], bool)) or (
                'delete' in body and body['delete'] is not True) or not ({'kept', 'delete'} & set(body)):
            raise WebError(400, 'invalid_param', 'Send {"kept": true|false} or {"delete": true}.')
        store = self.store
        store.require(LEGACY_TESTS)
        with store.write() as db:
            if db.execute('SELECT 1 FROM radar_tests WHERE id = ?', (test_id,)).fetchone() is None:
                raise WebError(404, 'unknown_test', 'Unknown test.')
            if 'delete' in body:
                db.execute('DELETE FROM radar_tests WHERE id = ?', (test_id,))
            else:
                db.execute('UPDATE radar_tests SET kept = ? WHERE id = ?', (1 if body['kept'] else 0, test_id))


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
class RadarDrafts:
    """Tape-only tests waiting for their recording (the comma was out of reach), in the store (table
    radar_drafts): at most MAX_DRAFTS. A draft is deleted when its test is saved or from the page; it never turns
    into a test by itself."""
    KEYS = {'target', 'tape_radar_m', 'tape_comma_m'}

    def __init__(self, repo: Path, store: Store | None = None):
        # The JSON file of earlier versions, imported into the store the first time it is seen.
        self.path = repo / 'outputs' / 'web' / LEGACY_DRAFTS
        self._store = store

    @property
    def store(self) -> Store:
        return self._store or Store.at(self.path.parent)

    def drafts(self) -> list[dict]:
        """Newest first."""
        store = self.store
        store.check(LEGACY_DRAFTS)
        return [{'id': draft['id'], 'created': number_or_none(draft['created']), 'target': draft['target'],
                 'tape_radar_m': draft['tape_radar_m'], 'tape_comma_m': draft['tape_comma_m'],
                 'marker_counter': draft['marker_counter']}
                for draft in store.rows('SELECT * FROM radar_drafts ORDER BY seq DESC')]

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
        store = self.store
        store.require(LEGACY_DRAFTS)
        with store.write() as db:
            if db.execute('SELECT COUNT(*) FROM radar_drafts').fetchone()[0] >= MAX_DRAFTS:
                raise WebError(409, 'too_many_drafts', str(MAX_DRAFTS) + ' tests are already waiting for their '
                               'recording: finish or delete one first.')
            draft_id = new_id(db, 'radar_drafts')
            db.execute('INSERT INTO radar_drafts (id, created, target, tape_radar_m, tape_comma_m, marker_counter) '
                       'VALUES (?, ?, ?, ?, ?, ?)', (draft_id, round(time.time(), 1), target, tapes['tape_radar_m'],
                                                     tapes['tape_comma_m'], marker))
        return self.get(draft_id)

    def change(self, draft_id: str, body) -> None:
        """{"delete": true} removes a draft."""
        if not DRAFT_ID_RE.fullmatch(draft_id) or body != {'delete': True}:
            raise WebError(400, 'invalid_param', 'Send {"delete": true}.')
        if not self.remove(draft_id):
            raise WebError(404, 'unknown_draft', 'This test is no longer waiting: it was finished or deleted.')

    def remove(self, draft_id: str) -> bool:
        store = self.store
        store.require(LEGACY_DRAFTS)
        with store.write() as db:
            return db.execute('DELETE FROM radar_drafts WHERE id = ?', (draft_id,)).rowcount > 0


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
