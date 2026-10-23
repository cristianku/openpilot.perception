"""The settings of the site, saved from the page in outputs/web/settings.json (600): which drives folder the
experiments read. The page sees the folders by name and id (web/status.py), never by path.

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

    def payload(self) -> dict:
        chosen, candidates = self.drives_folder()
        sources = []
        for candidate in candidates:
            item = {'id': candidate['id'], 'name': candidate['name'],
                    'chosen': chosen is not None and candidate['id'] == chosen['id']}
            item.update(drives_summary(segment_rows(candidate['path'])))
            sources.append(item)
        return {'sources': sources}

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
