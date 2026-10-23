"""The settings of the site, saved from the page in outputs/web/settings.json (600): which drives folder the
experiments read, and where the car's radar sits. The page sees the folders by name and id (web/status.py),
never by path.

The radar measures from its own position, the comma from the windshield: `radar_to_camera_m` is how far the
comma sits behind the radar along the car, so that radar distance + radar_to_camera_m = distance from the
comma (perception.association.AssociationConfig.radar_to_reference_m). It belongs to one car, measured on it,
so it is a setting of this server and never a constant of the code. None until it is set.

PERCEPTION_DATA_DIR (environment, or the .env of this clone written by install.sh) adds a folder to the
choices and is the default when nothing was chosen on the page yet.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from web.common import WebError, read_private, write_private
from web.status import SOURCE_ID_RE, drives_summary, segment_rows, source_candidates

DATA_ENV = 'PERCEPTION_DATA_DIR'
# A comma behind the radar of its own car: between the bumper and the windshield.
RADAR_TO_CAMERA_RANGE_M = (0.0, 5.0)


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

    def radar_to_camera_m(self) -> float | None:
        value = self._saved().get('radar_to_camera_m')
        low, high = RADAR_TO_CAMERA_RANGE_M
        if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
            return float(value)
        return None

    def set_radar(self, body) -> dict:
        """{"radar_to_camera_m": metres} sets it (to the centimetre), {"radar_to_camera_m": null} clears it."""
        if not isinstance(body, dict) or set(body) != {'radar_to_camera_m'}:
            raise WebError(400, 'invalid_param', 'Send {"radar_to_camera_m": <metres> or null}.')
        value = body['radar_to_camera_m']
        low, high = RADAR_TO_CAMERA_RANGE_M
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not low <= value <= high):
            raise WebError(400, 'invalid_param', 'The distance from the radar to the comma must be between '
                           + str(low) + ' and ' + str(high) + ' m.')
        with self._lock:
            saved = self._saved()
            if value is None:
                saved.pop('radar_to_camera_m', None)
            else:
                saved['radar_to_camera_m'] = round(float(value), 2)
            write_private(self.path, json.dumps(saved, indent=1))
        return self.payload()

    def payload(self) -> dict:
        chosen, candidates = self.drives_folder()
        sources = []
        for candidate in candidates:
            item = {'id': candidate['id'], 'name': candidate['name'],
                    'chosen': chosen is not None and candidate['id'] == chosen['id']}
            item.update(drives_summary(segment_rows(candidate['path'])))
            sources.append(item)
        return {'sources': sources, 'radar_to_camera_m': self.radar_to_camera_m()}

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
