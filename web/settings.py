"""The settings of the site, saved from the page in outputs/web/settings.json (600): which drives folder the
experiments read, and where the car's radar sits. The page sees the folders by name and id (web/status.py),
never by path.

The car's radar, two values measured on the car (a setting of this server, never a constant of the code):
  radar_to_camera_m   how far the comma sits behind the radar along the car (with a tape, e.g. from a wall:
                      comma to wall minus radar to wall);
  radar_correction_m  what to add to the radar's own reading to get the distance from the point measured with
                      the tape (the radar's zero: a sensor behind the bumper skin, or an offset of the car's
                      radar), from the known-distance tests; 0 until it is known.
Distance from the comma = radar reading + radar_correction_m + radar_to_camera_m: the sum is
perception.association.AssociationConfig.radar_to_reference_m. None until the distance is set.
With them in settings.json:
  radar_source               "tests" (the means of the kept tests, web/radar.py), "typed" (by hand), or "none"
                             ("My car has no radar": both values cleared on purpose, remembered until one comes
                             back); values saved before the source was kept were typed;
  radar_calibration_version  +1 on every change of the values or their source, 0 before any: a confirmed pair
                             records the version it was seen with.
Every version is also appended to outputs/web/radar_calibrations.json (600, entries never changed):
[{version, at, radar_to_camera_m, radar_correction_m, source, test_ids}].

PERCEPTION_DATA_DIR (environment, or the .env of this clone written by install.sh) adds a folder to the
choices and is the default when nothing was chosen on the page yet.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from web.common import WebError, read_private, write_private
from web.status import SOURCE_ID_RE, drives_summary, segment_rows, source_candidates

DATA_ENV = 'PERCEPTION_DATA_DIR'
# A comma behind the radar of its own car: between the bumper and the windshield.
RADAR_TO_CAMERA_RANGE_M = (0.0, 5.0)
# The radar's zero, against a tape: a few centimetres to a few tens.
RADAR_CORRECTION_RANGE_M = (-1.0, 1.0)
RADAR_KEYS = {'radar_to_camera_m': RADAR_TO_CAMERA_RANGE_M, 'radar_correction_m': RADAR_CORRECTION_RANGE_M}
RADAR_SOURCES = ('tests', 'typed')
# The owner said the car has no radar: not the same as never set up.
NO_RADAR = 'none'


def env_value(repo: Path, key: str) -> str:
    """KEY from the environment, else from the .env of the clone (last line wins), else ''."""
    value = os.environ.get(key, '')
    if value:
        return value
    try:
        lines = (repo / '.env').read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return ''
    for line in reversed(lines):
        name, separator, rest = line.partition('=')
        if separator and name.strip() == key:
            return rest.strip().strip('"\'')
    return ''


class Settings:
    def __init__(self, repo: Path, path: Path | None = None):
        self.repo = repo
        self.path = path or repo / 'outputs' / 'web' / 'settings.json'
        self._lock = threading.Lock()

    def _saved(self) -> dict:
        text = read_private(self.path)
        if not text:
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def candidates(self) -> list[dict]:
        return source_candidates(self.repo, env_value(self.repo, DATA_ENV))

    def drives_folder(self) -> tuple[dict | None, list[dict]]:
        """(the chosen source, every candidate). The saved choice when it is still there, else the first
        candidate that has localized segments, else the first one."""
        candidates = self.candidates()
        saved = self._saved().get('drives')
        for candidate in candidates:
            if candidate['id'] == saved:
                return candidate, candidates
        for candidate in candidates:
            if any(row['ready'] for row in segment_rows(candidate['path'])):
                return candidate, candidates
        return (candidates[0] if candidates else None), candidates

    @staticmethod
    def _radar_value(saved: dict, key: str) -> float | None:
        value = saved.get(key)
        low, high = RADAR_KEYS[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
            return float(value)
        return None

    @classmethod
    def _radar_source(cls, saved: dict) -> str | None:
        source = saved.get('radar_source')
        typed = any(cls._radar_value(saved, key) is not None for key in RADAR_KEYS)
        if source == NO_RADAR:
            # A value written in the file by hand since says otherwise.
            return 'typed' if typed else NO_RADAR
        if source in RADAR_SOURCES:
            return source
        # Saved before the source was kept: the values came from the Settings fields, typed by hand.
        return 'typed' if typed else None

    @staticmethod
    def _radar_version(saved: dict) -> int:
        version = saved.get('radar_calibration_version')
        return version if isinstance(version, int) and not isinstance(version, bool) and version >= 0 else 0

    def radar_to_camera_m(self) -> float | None:
        return self._radar_value(self._saved(), 'radar_to_camera_m')

    def radar_correction_m(self) -> float:
        value = self._radar_value(self._saved(), 'radar_correction_m')
        return 0.0 if value is None else value

    @classmethod
    def _radar_values(cls, saved: dict) -> dict:
        to_camera = cls._radar_value(saved, 'radar_to_camera_m')
        correction = cls._radar_value(saved, 'radar_correction_m')
        correction = 0.0 if correction is None else correction
        total = None if to_camera is None else round(to_camera + correction, 2)
        return {'radar_to_camera_m': to_camera, 'radar_correction_m': correction, 'radar_to_reference_m': total}

    def radar_payload(self) -> dict:
        """The two values and their sum, radar reading -> distance from the comma (None without the distance)."""
        return self._radar_values(self._saved())

    def radar_state(self) -> dict:
        """radar_payload() with where the values come from ("tests", "typed", "none" for a car without radar, or
        None) and their version."""
        saved = self._saved()
        return {**self._radar_values(saved), 'source': self._radar_source(saved),
                'version': self._radar_version(saved)}

    @property
    def history_path(self) -> Path:
        return self.path.parent / 'radar_calibrations.json'

    def radar_history(self) -> list[dict]:
        """Every calibration version, oldest first. A file that is there but cannot be read is refused: it is
        never written over."""
        text = read_private(self.history_path)
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, list):
            raise WebError(500, 'unreadable', 'Cannot read the history of the radar calibration on this server.')
        return data

    def set_radar(self, body) -> dict:
        """{"radar_to_camera_m": metres | null, "radar_correction_m": metres | null}, either or both, typed by
        hand: each is kept to the centimetre; null clears it. The source becomes "typed", or "none" when both are
        cleared ("My car has no radar")."""
        if not isinstance(body, dict) or not body or set(body) - set(RADAR_KEYS):
            raise WebError(400, 'invalid_param', 'Send {"radar_to_camera_m": <metres> or null, '
                           '"radar_correction_m": <metres> or null}.')
        for key, value in body.items():
            low, high = RADAR_KEYS[key]
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not low <= value <= high):
                what = 'The distance from the radar to the comma' if key == 'radar_to_camera_m' \
                    else 'The correction of the radar reading'
                raise WebError(400, 'invalid_param', what + ' must be between ' + str(low) + ' and ' + str(high)
                               + ' m.')
        with self._lock:
            saved = self._saved()
            values = {key: self._radar_value(saved, key) for key in RADAR_KEYS}
            for key, value in body.items():
                values[key] = None if value is None else round(float(value), 2)
            source = 'typed' if any(value is not None for value in values.values()) else NO_RADAR
            self._store_radar(saved, values, source, [])
        return self.payload()

    def set_radar_from_tests(self, radar_to_camera_m: float | None, radar_correction_m: float | None,
                             test_ids: list[str]) -> bool:
        """The means of the kept tests (web/radar.py), source "tests"; both None clears the calibration (no test
        is kept any more). True when it changed."""
        values = {'radar_to_camera_m': radar_to_camera_m, 'radar_correction_m': radar_correction_m}
        for key, value in values.items():
            low, high = RADAR_KEYS[key]
            if value is not None and not low <= value <= high:
                raise WebError(409, 'out_of_range', 'The tests give values the settings cannot hold: check them.')
        with self._lock:
            source = 'tests' if radar_to_camera_m is not None else None
            return self._store_radar(self._saved(), values, source, list(test_ids) if source else [])

    def _store_radar(self, saved: dict, values: dict, source: str | None, test_ids: list[str]) -> bool:
        """Writes a calibration (the lock is held) when it differs from the one in use: a new version, first in
        the history, then in settings.json. True when it changed."""
        history = self.radar_history()
        before = {key: self._radar_value(saved, key) for key in RADAR_KEYS}
        before_ids = history[-1].get('test_ids') if history and isinstance(history[-1], dict) else []
        if before == values and self._radar_source(saved) == source and (source != 'tests' or before_ids == test_ids):
            return False
        # A history entry whose settings.json write failed keeps its number: the next version comes after it.
        last = max((entry.get('version', 0) for entry in history
                    if isinstance(entry, dict) and isinstance(entry.get('version'), int)), default=0)
        version = max(self._radar_version(saved), last) + 1
        correction = values['radar_correction_m']
        entry = {'version': version, 'at': round(time.time(), 1), 'radar_to_camera_m': values['radar_to_camera_m'],
                 'radar_correction_m': 0.0 if correction is None else correction, 'source': source,
                 'test_ids': test_ids}
        write_private(self.history_path, json.dumps([*history, entry], indent=1))
        for key, value in values.items():
            if value is None:
                saved.pop(key, None)
            else:
                saved[key] = value
        if source is None:
            saved.pop('radar_source', None)
        else:
            saved['radar_source'] = source
        saved['radar_calibration_version'] = version
        write_private(self.path, json.dumps(saved, indent=1))
        return True

    def payload(self) -> dict:
        chosen, candidates = self.drives_folder()
        sources = []
        for candidate in candidates:
            item = {'id': candidate['id'], 'name': candidate['name'],
                    'chosen': chosen is not None and candidate['id'] == chosen['id']}
            item.update(drives_summary(segment_rows(candidate['path'])))
            sources.append(item)
        return {'sources': sources, **self.radar_payload()}

    def choose(self, body) -> dict:
        if not isinstance(body, dict) or set(body) != {'drives'} or not isinstance(body['drives'], str) \
                or not SOURCE_ID_RE.fullmatch(body['drives']):
            raise WebError(400, 'invalid_param', 'Send {"drives": "<id of a folder of the list>"}.')
        if not any(candidate['id'] == body['drives'] for candidate in self.candidates()):
            raise WebError(404, 'unknown_source', 'That drives folder is no longer on this server: reload the page.')
        with self._lock:
            saved = self._saved()
            saved['drives'] = body['drives']
            write_private(self.path, json.dumps(saved, indent=1))
        return self.payload()
