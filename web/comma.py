"""The comma, read over ssh: its recordings, and the short ones copied to this server for the radar tests; its
SSH host keys as this server knows them (for the app). Nothing is started on the comma, and only one thing is ever
written there: when the owner lets the app add phones to the comma (Settings), a phone's public key, one line
appended to its list of SSH keys (add_ssh_key). A GitHub refresh on the comma replaces that list.

The address is PERCEPTION_COMMA_HOST (environment or the .env of this clone), else the COMMA_HOST of the
openpilot.distill clone whose drives this site reads (its Settings set it), else "comma", the ssh alias that
openpilot.distill's setup writes. ssh never asks anything (BatchMode) and gives up after 10 s.

Recordings are ordered by their route counter (the first 8 hex digits of the name, which the comma increases
at every start), never by the comma's clock: without GPS and network at start it can be months off.
Copied recordings go to outputs/recordings/<route>--<segment>/ with rlog.zst and, when the comma still has it,
qcamera.ts (the small video, for a picture of what the radar saw).
"""
from __future__ import annotations

import base64
import hashlib
import os
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
KEY_UNREACHABLE = 'The comma cannot be reached from the site right now.'
KEY_REFUSED = ('The comma answered but did not take the key: its list of SSH keys could not be written. Use the '
               'GitHub way.')
# The comma's allowed SSH keys: openpilot's GithubSshKeys param, the AuthorizedKeysFile of its sshd.
COMMA_SSH_KEYS = '/data/params/d/GithubSshKeys'
ED25519_PREFIX = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'
PUBLIC_KEY_RE = re.compile(r'ssh-ed25519 ([A-Za-z0-9+/]{68})(?: ([A-Za-z0-9._@-]{1,80}))?')
# Run on the comma by the login shell of the user ssh signs in as (comma, the owner of the list). The key line comes
# on the standard input, never in the command: it is read as data, compared field by field, and written with
# printf. One line is appended atomically: a temporary file next to the list (the owner of the list, or the user
# when there is none yet; mode 600) with the list and the line, flushed, then renamed over it. "already" when the
# list has the key (whatever its comment), "added" once it is written; exit 3 for a line that is not a key, 4 when
# the list is not a plain file.
ADD_KEY_SCRIPT = r"""set -euf
umask 077
f=/data/params/d/GithubSshKeys
line=
IFS= read -r line || [ -n "$line" ]
set -- $line
[ "$#" -ge 2 ] && [ "$1" = ssh-ed25519 ] || exit 3
key=$2
if [ -e "$f" ] || [ -L "$f" ]; then
  [ -f "$f" ] && [ ! -L "$f" ] || exit 4
  while IFS= read -r have || [ -n "$have" ]; do
    set -- $have
    if [ "$#" -ge 2 ] && [ "$1" = ssh-ed25519 ] && [ "$2" = "$key" ]; then echo already; exit 0; fi
  done < "$f"
fi
t=$(mktemp "$f.XXXXXX")
trap 'rm -f "$t"' EXIT
if [ -e "$f" ]; then
  cat "$f" > "$t"
  if [ -s "$t" ] && [ -n "$(tail -c 1 "$t")" ]; then echo >> "$t"; fi
  chown --reference="$f" "$t"
fi
printf '%s\n' "$line" >> "$t"
chmod 600 "$t"
sync "$t" 2>/dev/null || sync
mv -f "$t" "$f"
trap - EXIT
echo added
"""


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


def fingerprint_of(blob: bytes) -> str:
    """SHA256:... of a key's wire form, as ssh-keygen -l prints it."""
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode('ascii').rstrip('=')


def ed25519_blob(text: str) -> bytes | None:
    """The wire form of an ssh-ed25519 key from its base64, None unless it is exactly one: the type, then 32 bytes."""
    try:
        blob = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        return None
    if len(blob) != len(ED25519_PREFIX) + 32 or not blob.startswith(ED25519_PREFIX) \
            or base64.b64encode(blob).decode('ascii') != text:
        return None
    return blob


def public_key_line(value) -> tuple[str, str, str | None]:
    """A phone's public key as the app sends it: exactly "ssh-ed25519 <base64 of the 32-byte key>", and an optional
    short comment of letters, digits and ._@- ("perception-app@Pixel-8"). (line, fingerprint, comment); a refusal
    (400) for anything else: another type, a second key, options, another character, a new line."""
    match = PUBLIC_KEY_RE.fullmatch(value) if isinstance(value, str) else None
    blob = ed25519_blob(match.group(1)) if match else None
    if blob is None:
        raise WebError(400, 'invalid_param', 'public_key is one key, "ssh-ed25519 <key>" and an optional short '
                       'comment of letters, digits and ._@-, as the app shows it.')
    return value, fingerprint_of(blob), match.group(2)


def known_host_fingerprints(lines: str) -> list[str]:
    """The SHA256 fingerprints of the ssh-ed25519 keys among known_hosts lines (ssh-keygen -F prints them)."""
    found = []
    for line in lines.splitlines():
        parts = line.split()
        # Comments, and @cert-authority or @revoked lines: not the comma's own key.
        if len(parts) < 3 or parts[0].startswith(('#', '@')) or parts[1] != 'ssh-ed25519':
            continue
        blob = ed25519_blob(parts[2])
        if blob is not None and fingerprint_of(blob) not in found:
            found.append(fingerprint_of(blob))
    return found


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

    def host_key_fingerprints(self) -> list[str]:
        """The comma's ssh-ed25519 host keys as this server knows them (its known_hosts files, the ones ssh uses for
        this host: ssh -G resolves the alias, HostKeyAlias and port), as SHA256 fingerprints. Nothing goes to the
        comma. [] when there is none or ssh is missing."""
        def run(argv):
            try:
                result = self._run(argv, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return ''
            return result.stdout if result.returncode == 0 else ''

        options = {}
        for line in run(['ssh', '-G', self.host]).splitlines():
            name, _, value = line.partition(' ')
            options.setdefault(name.lower(), value.strip())
        name = options.get('hostkeyalias', '')
        if not name or name == 'none':
            name = options.get('hostname', '')
        if not HOST_RE.fullmatch(name):
            return []
        port = options.get('port', '22')
        lookup = name if port == '22' else '[' + name + ']:' + port
        found = []
        for file in (options.get('userknownhostsfile', '') + ' ' + options.get('globalknownhostsfile', '')).split():
            if not os.path.isfile(os.path.expanduser(file)):
                continue
            for fingerprint in known_host_fingerprints(run(['ssh-keygen', '-F', lookup, '-f',
                                                            os.path.expanduser(file)])):
                if fingerprint not in found:
                    found.append(fingerprint)
        return found

    def add_ssh_key(self, line: str) -> str:
        """Appends a phone's public key (public_key_line) to the comma's allowed SSH keys (COMMA_SSH_KEYS, owner
        comma, mode 600), over ssh with this server's key: the line goes on the standard input of ADD_KEY_SCRIPT,
        never into a command. "added", or "already" when the comma had the key. 503 comma_unreachable when the comma
        does not answer or does not let this server in; 502 comma_refused when it answers but cannot write."""
        public_key_line(line)
        try:
            result = self._run(['ssh', *SSH_OPTIONS, self.host, ADD_KEY_SCRIPT], input=line + '\n',
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise WebError(503, 'comma_unreachable', KEY_UNREACHABLE) from None
        # 255: ssh itself (no answer, host key or sign-in refused).
        if result.returncode == 255:
            raise WebError(503, 'comma_unreachable', KEY_UNREACHABLE)
        answer = (result.stdout or '').strip().splitlines()
        if result.returncode == 0 and answer and answer[-1] in ('added', 'already'):
            return answer[-1]
        raise WebError(502, 'comma_refused', KEY_REFUSED)
