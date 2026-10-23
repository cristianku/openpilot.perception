"""The comma, read over ssh: its recordings, and the short ones copied to this server for the radar tests.
Nothing is ever written or started on the comma.

The address is PERCEPTION_COMMA_HOST (environment or the .env of this clone), else the COMMA_HOST of the
openpilot.distill clone whose drives this site reads (its Settings set it), else "comma", the ssh alias that
openpilot.distill's setup writes. ssh never asks anything (BatchMode) and gives up after 10 s.

Recordings are ordered by their route counter (the first 8 hex digits of the name, which the comma increases
at every start), never by the comma's clock: without GPS and network at start it can be months off.
Copied recordings go to outputs/recordings/<route>--<segment>/ with rlog.zst and, when the comma still has it,
qcamera.ts (the small video, for a picture of what the radar saw).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from web.common import WebError

DEFAULT_HOST = 'comma'
HOST_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@-]*$')
SEGMENT_DIR_RE = re.compile(r'^([0-9a-f]{8})--([0-9a-f]{10})--([0-9]{1,4})$')
RECORDING_RE = re.compile(r'^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]{1,4}$')
SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
               '-o', 'ServerAliveCountMax=4')
REALDATA = '/data/media/0/realdata'
COPIED_FILES = ('rlog.zst', 'qcamera.ts')
# A short recording: the radar tests are a minute at most; longer drives come through openpilot.distill.
MAX_SHORT_SEGMENTS = 2
NOT_REACHABLE = 'The comma does not answer: is it switched on and on the same network as this server?'


def _env_file_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return ''
    for line in reversed(lines):
        name, separator, value = line.partition('=')
        if separator and name.strip() == key:
            return value.strip().strip('"\'')
    return ''


def comma_host(repo: Path, distill_clone: Path | None = None, environ=None) -> str:
    import os

    environ = os.environ if environ is None else environ
    for value in (environ.get('PERCEPTION_COMMA_HOST', ''), _env_file_value(repo / '.env', 'PERCEPTION_COMMA_HOST'),
                  environ.get('COMMA_HOST', ''),
                  _env_file_value(distill_clone / '.env', 'COMMA_HOST') if distill_clone else ''):
        if value and HOST_RE.fullmatch(value):
            return value
    return DEFAULT_HOST


def parse_listing(text: str) -> list[dict]:
    """Recordings from lines "<route>--<segment> <rlog bytes> <qcamera bytes>", newest route counter first."""
    routes = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        match = SEGMENT_DIR_RE.fullmatch(parts[0])
        if match is None:
            continue
        counter, tag, segment = match.group(1), match.group(2), int(match.group(3))
        sizes = [int(value) if value.isdigit() else 0 for value in parts[1:3]] + [0, 0]
        route = routes.setdefault(counter + '--' + tag, {'route': counter + '--' + tag, 'counter': int(counter, 16),
                                                         'segments': [], 'rlog_bytes': 0, 'has_video': True})
        route['segments'].append(segment)
        route['rlog_bytes'] += sizes[0]
        route['has_video'] = route['has_video'] and sizes[1] > 0
    result = []
    for route in routes.values():
        route['segments'].sort()
        route['short'] = len(route['segments']) <= MAX_SHORT_SEGMENTS
        result.append(route)
    result.sort(key=lambda route: route['counter'], reverse=True)
    return result


class Comma:
    def __init__(self, host: str, runner=subprocess.run):
        if not HOST_RE.fullmatch(host):
            raise WebError(400, 'invalid_host', 'The comma address is not valid.')
        self.host = host
        self._run = runner

    def _ssh(self, command: str, timeout: float = 30) -> str:
        try:
            result = self._run(['ssh', *SSH_OPTIONS, self.host, command], capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise WebError(503, 'comma_unreachable', NOT_REACHABLE) from None
        if result.returncode != 0:
            raise WebError(503, 'comma_unreachable', NOT_REACHABLE)
        return result.stdout

    def recordings(self, limit: int = 12) -> list[dict]:
        """The newest recordings on the comma (by route counter), with their segments."""
        listing = self._ssh(
            f'cd {REALDATA} && for d in *--*--*; do '
            '[ -d "$d" ] && printf "%s %s %s\\n" "$d" "$(stat -c %s "$d/rlog.zst" 2>/dev/null || echo 0)" '
            '"$(stat -c %s "$d/qcamera.ts" 2>/dev/null || echo 0)"; done')
        return parse_listing(listing)[:limit]

    def fetch(self, recording: str, destination: Path, timeout: float = 300) -> list[str]:
        """Copies the files of one segment ("<route>--<segment>") to destination/<recording>/; returns the
        names copied. A file is written under a temporary name and renamed when complete."""
        if not RECORDING_RE.fullmatch(recording):
            raise WebError(400, 'invalid_recording', 'Unknown recording.')
        folder = destination / recording
        folder.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in COPIED_FILES:
            target = folder / name
            if target.is_file():
                copied.append(name)
                continue
            partial = folder / (name + '.part')
            try:
                result = self._run(['scp', '-q', *SSH_OPTIONS, f'{self.host}:{REALDATA}/{recording}/{name}',
                                    str(partial)], capture_output=True, text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                raise WebError(503, 'comma_unreachable', NOT_REACHABLE) from None
            if result.returncode == 0 and partial.is_file():
                partial.replace(target)
                copied.append(name)
            else:
                partial.unlink(missing_ok=True)
                if name == 'rlog.zst':
                    raise WebError(404, 'not_on_the_comma', 'The comma no longer has this recording.')
        return copied
