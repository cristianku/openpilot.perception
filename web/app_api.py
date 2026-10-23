"""The API of Perception for Android (app/android, ch.bodhi.perception), as its SiteApi.kt expects it: what the app
reads, in the exact shapes of web/API.md, and the app's downloads.

    GET  /api/app/v1                  info(): the site's code, the calibration, how to reach the comma
    POST /api/app/v1/tests            a test made with the app (web/radar.py RadarTests.add_from_app)
    GET  /api/app/v1/tests            every test, from recordings and from the app
    POST /api/app/v1/comma-key        a phone's public key added to the comma (the owner allows one phone at a
                                      time in Settings, with his password: PhoneKeys)
    GET  /download/android            the newest APK built on this server (helpers/build_android.sh)
    GET  /download/android/aab        the bundle for Google Play, when latest.json names one

API_VERSION is the number of the contract, in GET /api/auth/state ("api") and GET /api/app/v1 ("version"): a
field the app reads is never removed or renamed, nor its meaning changed, without raising it (web/API.md).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from web.common import inside
from web.radar import LABEL_RANGE_M, test_view
from web.store import KEY_OUTCOMES, Store

API_NAME = 'perception-app'
API_VERSION = 1
# Where openpilot and its Python are on a comma (the app runs app/comma_listener.py with them).
COMMA_PYTHON = '/usr/local/venv/bin/python'
COMMA_OPENPILOT = '/data/openpilot'
APP_CALIBRATION_KEYS = ('radar_to_camera_m', 'radar_correction_m', 'radar_to_reference_m', 'source', 'version',
                        'status', 'uncertainty_m', 'tested_m')
APP_TEST_KEYS = ('id', 'client_id', 'target', 'route', 'reading_m', 'tape_radar_m', 'tape_comma_m', 'correction_m',
                 'kept', 'created')
NOT_ALLOWED = 'The owner does not allow adding keys to the comma from the app.'
GOOGLE_PLAY_TEST = 'https://play.google.com/apps/internaltest/4700859580640807099'
# helpers/build_android.sh: outputs/android/perception-android-<version>.apk (-dev with local changes), and the
# bundle for Google Play of a committed version only.
ANDROID_APK_RE = re.compile(r'perception-android-[0-9]+\.[0-9]+\.[0-9]+(-dev)?\.apk')
ANDROID_AAB_RE = re.compile(r'perception-android-[0-9]+\.[0-9]+\.[0-9]+\.aab')
# "Let the app add phones to the comma": for one phone and at most this long once the owner gave his password.
PERMISSION_SECONDS = 10 * 60


def app_calibration(calibration: dict) -> dict:
    """The calibration as the app reads it (web/radar.py calibration_view, the keys of SiteCalibration)."""
    return {key: calibration[key] for key in APP_CALIBRATION_KEYS}


def app_test(test: dict) -> dict:
    """A test as the app lists it (SiteTest): client_id only for a test made with the app."""
    view = test_view(test)
    return {key: view[key] for key in APP_TEST_KEYS if key != 'client_id' or view.get('source') == 'app'}


def code_commit(code: str) -> str:
    """The short commit of web.server.code_version ("85c756f (main)" -> "85c756f")."""
    head = code.split(' ', 1)[0]
    return head if re.fullmatch(r'[0-9a-f]{7,40}', head) else ''


def info(code: str, calibration: dict, fingerprints: list[str]) -> dict:
    return {'ok': True, 'api': API_NAME, 'version': API_VERSION, 'code': code_commit(code),
            'calibration': app_calibration(calibration),
            'comma': {'host_key_fingerprints': list(fingerprints), 'python': COMMA_PYTHON,
                      'openpilot': COMMA_OPENPILOT},
            'label_range_m': list(LABEL_RANGE_M)}


# -- the phones added to the comma ------------------------------------------------------------------------
class PhoneKeys:
    """The phone keys the site sent to the comma for the app (table comma_keys, append-only, one row per step:
    "sending" before the key goes, then what the comma answered) and the owner's permission for it, "Let the app
    add phones to the comma" (table app_permissions, append-only, every change). The permission is given in
    Settings with the administrator's password (web/server.py), for one phone and PERMISSION_SECONDS at most: it
    ends by itself once a phone key is on the comma."""

    def __init__(self, repo: Path, store: Store | None = None, clock=time.time):
        self.folder = repo / 'outputs' / 'web'
        self._store = store
        self._clock = clock

    @property
    def store(self) -> Store:
        return self._store or Store.at(self.folder)

    # -- the permission ------------------------------------------------------------------------------
    def permission(self) -> dict:
        """{"allowed", "until"}: the last change of app_permissions, still on when it was turned on and its time
        has not run out (until, seconds since 1970, else None)."""
        last = self.store.rows('SELECT allowed, until FROM app_permissions ORDER BY seq DESC LIMIT 1')
        if last and last[0]['allowed'] == 1 and last[0]['until'] > self._clock():
            return {'allowed': True, 'until': last[0]['until']}
        return {'allowed': False, 'until': None}

    def allowed(self) -> bool:
        return self.permission()['allowed']

    def set_permission(self, allowed: bool) -> dict:
        """The owner's change in Settings (why "owner"): on for PERMISSION_SECONDS from now, or off. Turning off
        what is already off writes nothing."""
        now = self._clock()
        if allowed or self.allowed():
            with self.store.write() as db:
                db.execute('INSERT INTO app_permissions (at, allowed, until, why) VALUES (?, ?, ?, ?)',
                           (round(now, 1), 1 if allowed else 0,
                            round(now + PERMISSION_SECONDS, 1) if allowed else None, 'owner'))
        return self.permission()

    # -- the keys ------------------------------------------------------------------------------------
    def record(self, fingerprint: str, comment: str | None, host: str, outcome: str, detail: str | None = None,
               used: bool = False) -> None:
        """One step of an addition (KEY_OUTCOMES); used: the key is on the comma, and the permission ends with it
        (why "used", in the same transaction)."""
        if outcome not in KEY_OUTCOMES:
            raise ValueError('unknown outcome: ' + outcome)
        now = round(self._clock(), 1)
        with self.store.write() as db:
            db.execute('INSERT INTO comma_keys (at, fingerprint, comment, host, outcome, detail) '
                       'VALUES (?, ?, ?, ?, ?, ?)', (now, fingerprint, comment, host, outcome, detail))
            if used:
                db.execute("INSERT INTO app_permissions (at, allowed, until, why) VALUES (?, 0, NULL, 'used')",
                           (now,))

    def phones(self) -> list[dict]:
        """Every key the site ever sent to a comma, once per key and comma, the last one first: when it was sent
        first (at) and last (last), its fingerprint, its last comment, the comma (host), and its state:
          added        the comma took it from the site
          already      the comma had it before (the GitHub way, or another site)
          unconfirmed  sent, but what the comma did is not known (no answer in time, or it could not be
                       recorded): it may have it
          refused      the comma answered and did not write it."""
        keys: dict[tuple, dict] = {}
        for row in self.store.rows('SELECT seq, at, fingerprint, comment, host, outcome, detail FROM comma_keys '
                                   'ORDER BY seq'):
            key = keys.setdefault((row['fingerprint'], row['host']), {
                'at': row['at'], 'fingerprint': row['fingerprint'], 'host': row['host'], 'outcomes': set()})
            key.update(last=row['at'], seq=row['seq'], comment=row['comment'], final=(row['outcome'], row['detail']))
            key['outcomes'].add(row['outcome'])
        phones = []
        for key in sorted(keys.values(), key=lambda item: item['seq'], reverse=True):
            if 'added' in key['outcomes']:
                state = 'added'
            elif 'already' in key['outcomes']:
                state = 'already'
            elif key['final'] == ('failed', 'comma_refused'):
                state = 'refused'
            else:
                state = 'unconfirmed'
            phones.append({'at': key['at'], 'last': key['last'], 'fingerprint': key['fingerprint'],
                           'comment': key['comment'], 'host': key['host'], 'state': state})
        return phones


# -- the app's downloads ----------------------------------------------------------------------------------
def android_build(repo: Path) -> dict | None:
    """The newest app built on this server (outputs/android/latest.json, helpers/build_android.sh): what Settings
    shows and /download/android sends, with the bundle for Google Play (/download/android/aab) when latest.json
    names one ("aab") of a committed version (a -dev one is never published). None without a build, or when
    latest.json does not name a plain APK inside outputs/android (no links)."""
    folder = repo / 'outputs' / 'android'
    try:
        latest = json.loads((folder / 'latest.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    name = latest.get('file') if isinstance(latest, dict) else None
    if not isinstance(name, str) or not ANDROID_APK_RE.fullmatch(name):
        return None
    path = folder / name
    if path.is_symlink() or not path.is_file() or not inside(folder, path):
        return None
    build = {'file': name, 'size': path.stat().st_size, 'path': path}
    for key, kind in (('version', str), ('version_code', int), ('commit', str), ('sha256', str),
                      ('built', (int, float))):
        value = latest.get(key)
        build[key] = value if isinstance(value, kind) and not isinstance(value, bool) else None
    build['bundle'] = None
    bundle = latest.get('aab')
    if isinstance(bundle, str) and ANDROID_AAB_RE.fullmatch(bundle):
        bundle_path = folder / bundle
        if not bundle_path.is_symlink() and bundle_path.is_file() and inside(folder, bundle_path):
            build['bundle'] = {'file': bundle, 'size': bundle_path.stat().st_size, 'path': bundle_path}
    return build


def android_view(build: dict | None) -> dict | None:
    """The build for the page: never its path on this server."""
    if build is None:
        return None
    view = {key: value for key, value in build.items() if key != 'path'}
    if build['bundle'] is not None:
        view['bundle'] = {'file': build['bundle']['file'], 'size': build['bundle']['size']}
    return view
