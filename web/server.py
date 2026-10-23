"""perception-web: the control site of openpilot.perception (standard library HTTP server). Ported from
openpilot.distill's distill-web (same author, MIT): same sign-in, same protections, same look.

    python -m web.server [--host 127.0.0.1] [--port 8767]

Every page and API call needs the administrator signed in (web/auth.py): without a session the site shows
only the sign-in page, its script and stylesheet. The first visit creates the administrator with the setup
code: PERCEPTION_WEB_TOKEN (repo .env, or /etc/perception-web.env for the systemd service) or, when that is
not set, a code the server prints at start. On 127.0.0.1 (the default) only this machine reaches it and the
Host header must be a loopback name, against DNS rebinding. The session cookie (perception_web_<port>) is
HttpOnly, SameSite=Strict, and Secure when the page came over https (a reverse proxy).
POSTs are accepted only from the same origin, as JSON (an HTML form cannot send it), with a Content-Length
and a small body. Pages carry a strict Content-Security-Policy with a nonce. The server never prints the
setup code from the environment, and never logs request URLs.

Perception for Android (app/android) uses the same session and protections through its own API, /api/app/v1
(web/app_api.py, the contract in web/API.md), and downloads the app at /download/android.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from web.actions import ACTIONS, SIZES, audit_jobs, build_job
from web.app_api import (API_VERSION, NOT_ALLOWED, PERMISSION_SECONDS, PhoneKeys, android_build, android_view,
                         app_calibration, app_test)
from web.app_api import info as app_info
from web.auth import SESSION_MAX_SECONDS, AdminAuth
from web.common import REPO, WebError, render_problem, short_text, strict_json_loads
from web.comma import Comma, comma_host, public_key_line
from web.jobs import JOB_ID_RE, JobManager, describe
from web.pictures import picture
from web.radar import (DRAFT_ID_RE, RadarDrafts, RadarIndex, RadarTests, calibration_view, drive_detail, frame_for,
                       kept_ids, load_series, next_step, not_indexed, radar_overview, recordings_dir, segment_folders)
from web.settings import NO_RADAR, Settings
from web.status import (Cache, ResultReader, drives_summary, gpu_busy_reason, picture_path, segment_rows,
                        spread_sample, system_info)
from web.store import Store

TOKEN_ENV = 'PERCEPTION_WEB_TOKEN'
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{16,}$')
DEFAULT_PORT = 8767
MAX_BODY_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS = 64
STATIC_DIR = Path(__file__).resolve().parent / 'static'
NONCE_MARK = '__PERCEPTION_NONCE__'
STATIC_FILES = {
    'app.js': 'text/javascript; charset=utf-8',
    'app.css': 'text/css; charset=utf-8',
    'login.js': 'text/javascript; charset=utf-8',
    'favicon.svg': 'image/svg+xml',
}
PUBLIC_STATIC = ('app.css', 'login.js', 'favicon.svg')
PUBLIC_AUTH_ROUTES = ('/api/auth/login', '/api/auth/setup/check', '/api/auth/setup')
AUTH_ROUTES = PUBLIC_AUTH_ROUTES + ('/api/auth/logout', '/api/auth/logout-all', '/api/auth/password')
APP_PATH = '/api/app/v1'
APK_TYPE = 'application/vnd.android.package-archive'
LOGIN_MESSAGE = 'Sign in first.'
DEFAULT_CSP = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
PROBLEM_CSP = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
               "frame-ancestors 'none'")
ROUTE_RE = re.compile(r'^(?:[0-9a-f]{18}|[0-9a-f]{8}--[0-9a-f]{10})$')
SEGMENT_PATH_RE = re.compile(r'^(?:[0-9a-f]{18,24}|[0-9a-f]{8}--[0-9a-f]{10}--[0-9]{1,4})$')
NO_DRIVES = ('no localized drives on this server yet: download and localize them with openpilot.distill, '
             'or choose another drives folder in Settings')


def page_csp(nonce: str) -> str:
    return ("default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def is_loopback(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def access_token() -> str:
    """The setup code: PERCEPTION_WEB_TOKEN when set (must look like a token), otherwise a new random one."""
    configured = os.environ.get(TOKEN_ENV, '')
    if configured:
        if not TOKEN_RE.fullmatch(configured):
            raise SystemExit(TOKEN_ENV + ' must have at least 16 characters: letters, digits, "-" and "_"')
        return configured
    return secrets.token_urlsafe(24)


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine that other computers can use (best effort, no traffic sent)."""
    found = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        if not is_loopback(info[4][0]) and info[4][0] not in found:
            found.append(info[4][0])
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # A UDP connect only selects the outgoing interface; nothing is sent.
        probe.connect(('10.255.255.255', 1))
        address = probe.getsockname()[0]
        if not is_loopback(address) and address not in found:
            found.append(address)
    except OSError:
        pass
    finally:
        probe.close()
    return found


def code_version(repo: Path) -> str:
    """Short commit and branch of the checkout, read from .git without running git (a submodule's .git is
    a file that points to the real folder)."""
    git = repo / '.git'
    if git.is_file():
        try:
            pointer = git.read_text().strip()
        except OSError:
            return ''
        if not pointer.startswith('gitdir: '):
            return ''
        git = (repo / pointer[len('gitdir: '):]).resolve()
    try:
        head = (git / 'HEAD').read_text().strip()
    except OSError:
        return ''
    if not head.startswith('ref: '):
        return head[:7]
    ref = head[len('ref: '):]
    branch = ref.rsplit('/', 1)[-1]
    sha = ''
    try:
        sha = (git / ref).read_text().strip()
    except OSError:
        try:
            for line in (git / 'packed-refs').read_text().splitlines():
                parts = line.split(' ')
                if len(parts) == 2 and parts[1] == ref:
                    sha = parts[0]
        except OSError:
            pass
    return sha[:7] + ' (' + branch + ')' if sha else branch


def latest_by_kind(results: list[dict]) -> dict:
    """The newest finished result of every experiment (the plan on the Overview page)."""
    latest = {}
    for result in results:
        if result['summary'] is not None and result['kind'] not in latest:
            latest[result['kind']] = {'id': result['id'], 'started': result['started'],
                                      'segments': result['segments'], 'summary': result['summary']}
    return latest


class App:
    """State shared by the request threads."""

    def __init__(self, repo: Path, *, setup_code: str | None = None, local_only: bool = True,
                 jobs: JobManager | None = None, auth: AdminAuth | None = None, settings: Settings | None = None,
                 gpu_check=None):
        self.repo = repo
        self.local_only = local_only
        if auth is None:
            auth = AdminAuth(repo / 'outputs' / 'web' / 'auth', setup_code)
        elif setup_code is not None:
            auth.setup_code = setup_code
        self.auth = auth
        self.cookie_name = 'perception_web'
        self.gpu_check = gpu_check or gpu_busy_reason
        self.jobs = jobs or JobManager(repo, gpu_check=self.gpu_check)
        # Opened here, at the start: created and migrated, the JSON files of earlier versions imported.
        self.store = Store.at(repo / 'outputs' / 'web')
        self.settings = settings or Settings(repo, store=self.store)
        self.results = ResultReader(repo)
        self.radar_index = RadarIndex(repo)
        self.radar_tests = RadarTests(repo, self.store)
        self.radar_drafts = RadarDrafts(repo, self.store)
        self.phone_keys = PhoneKeys(repo, self.store)
        # How the site runs ssh on the comma (a fake in the tests).
        self.comma_runner = subprocess.run
        self._machine = Cache(3.0)
        self._drives = Cache(10.0)
        self._fingerprints = Cache(60.0)
        self._radar_lock = threading.Lock()
        # Every change of the calibration or of its tests, so a test saved while the numbers are typed never
        # writes the tests' means over them.
        self._calibration_lock = threading.Lock()
        # One phone key at a time on the comma.
        self._comma_key_lock = threading.Lock()

    # -- access ------------------------------------------------------------------------------------
    def host_allowed(self, host_header) -> bool:
        # On a loopback address the Host header must be a loopback name, against DNS rebinding.
        if not self.local_only:
            return True
        if not isinstance(host_header, str) or not host_header:
            return False
        name = host_header.strip().lower()
        if name.startswith('['):
            end = name.find(']')
            if end < 0:
                return False
            name = name[1:end]
        elif ':' in name:
            name = name.rsplit(':', 1)[0]
        return is_loopback(name)

    # -- data for the page -------------------------------------------------------------------------
    def drives(self) -> dict:
        """The chosen drives folder: its name, counts and ready segments (kept 10 s: it lists folders)."""
        def compute():
            chosen, _ = self.settings.drives_folder()
            if chosen is None:
                return {'source': None, 'rows': [], 'counts': drives_summary([])}
            rows = segment_rows(chosen['path'])
            return {'source': {'id': chosen['id'], 'name': chosen['name']}, 'path': chosen['path'], 'rows': rows,
                    'counts': drives_summary(rows)}
        return self._drives.get(compute)

    def action_states(self, machine: dict, jobs: list[dict], drives: dict) -> dict:
        running = {job['group'] for job in jobs if job['state'] == 'running'}
        gpu = machine['gpu']
        states = {}
        for action_id, action in ACTIONS.items():
            reason = self.jobs.conflict_reason(action.group, running)
            if not reason and action.needs_drives and not drives['counts']['ready']:
                reason = NO_DRIVES
            if not reason and action.needs_gpu:
                if gpu is None:
                    reason = 'this server has no NVIDIA GPU'
                elif gpu['processes']:
                    reason = 'the GPU is busy with another job'
            states[action_id] = {'label': action.label, 'allowed': not reason, 'reason': reason,
                                 'needs_gpu': action.needs_gpu}
        return states

    def status_payload(self) -> dict:
        machine = self._machine.get(lambda: system_info(self.repo))
        jobs = self.jobs.list_jobs()
        drives = self.drives()
        calibration = self.calibration()
        # From the kept series only: the status never starts the reading of the radar (GET /api/radar does).
        radar = self.radar_index.drives(self.radar_folders(), calibration)
        return {'time': time.time(), 'code': code_version(self.repo), 'system': machine, 'jobs': jobs,
                'actions': self.action_states(machine, jobs, drives), 'drives': {
                    'source': drives['source'], 'counts': drives['counts'],
                    'sizes': {name: min(size, drives['counts']['ready']) if size else drives['counts']['ready']
                              for name, size in SIZES.items()}},
                'audit_jobs': audit_jobs(), 'latest': latest_by_kind(self.results.results()),
                'radar': calibration, 'radar_summary': radar_overview(radar),
                'next': next_step(calibration, self.radar_drafts.drafts(), radar, jobs)}

    # -- radar -------------------------------------------------------------------------------------
    def radar_folders(self) -> dict:
        return segment_folders(self.repo, self.drives().get('path'))

    def _radar_job(self, jobs: list[dict]) -> dict | None:
        return next((job for job in jobs if job['group'] == 'radar' and job['state'] == 'running'), None)

    def radar_payload(self) -> dict:
        """The Radar page: the calibration and its tests, every drive and recording with its radar, and the
        reading of new segments, started here by itself when some are not read yet."""
        folders = self.radar_folders()
        pending = not_indexed(self.repo, folders)
        jobs = self.jobs.list_jobs()
        job = self._radar_job(jobs)
        if pending and job is None and not any(j['group'] == 'system' and j['state'] == 'running' for j in jobs):
            with self._radar_lock:
                try:
                    job = self.jobs.start(build_job('radar_index', {}, pending))
                    print('Job started: ' + describe(job), flush=True)
                except WebError:
                    job = None
        tests = self.radar_tests.summary()
        calibration = self.calibration(tests)
        return {'calibration': calibration, 'tests': tests, 'drafts': self.radar_drafts.drafts(),
                'summary': self.radar_index.drives(folders, calibration), 'pending': len(pending), 'job': job}

    def calibration(self, tests: dict | None = None) -> dict:
        """The calibration in use, its status and what the tests give (web/radar.py calibration_view)."""
        return calibration_view(self.settings.radar_state(), tests or self.radar_tests.summary())

    def radar_drive(self, route: str) -> dict:
        if not ROUTE_RE.fullmatch(route):
            raise WebError(404, 'unknown_drive', 'Unknown drive.')
        return drive_detail(self.repo, self.radar_folders(), route, self.calibration())

    def radar_picture(self, segment: str, t_ms: int) -> bytes:
        folder = self.radar_folders().get(segment) if SEGMENT_PATH_RE.fullmatch(segment) else None
        series = load_series(self.repo, segment) if folder is not None else None
        found = frame_for(series, t_ms / 1000.0) if series else None
        if found is None:
            raise WebError(404, 'no_picture', 'No picture for this moment.')
        return picture(folder, found[0], found[1], self.repo / 'outputs' / 'web' / 'pictures')

    def comma(self) -> Comma:
        """The comma of web/comma.py: PERCEPTION_COMMA_HOST, else the one of the openpilot.distill whose drives
        this site reads."""
        chosen, _ = self.settings.drives_folder()
        return Comma(comma_host(self.repo, chosen['path'].parent if chosen else None), runner=self.comma_runner)

    def comma_recordings(self) -> dict:
        """The newest recordings on the comma, the short ones first-class: which are already here."""
        comma = self.comma()
        recordings = comma.recordings()
        here = recordings_dir(self.repo)
        for recording in recordings:
            names = [recording['route'] + '--' + str(segment) for segment in recording['segments']]
            recording['names'] = names
            recording['copied'] = all((here / name / 'rlog.zst').is_file() for name in names)
        return {'recordings': recordings}

    def _follow_tests(self) -> dict:
        """After a test was added, left out, used again or deleted (the calibration lock is held): when the
        calibration comes from the tests, or there is none and nothing was typed, it becomes the means of the
        kept tests at once; with no test kept any more it is cleared. Numbers typed by hand are never touched. A
        car set as having no radar stays so until a test is kept: then it has one after all."""
        tests = self.radar_tests.summary()
        source = self.settings.radar_state()['source']
        if source in ('tests', None) or (source == NO_RADAR and tests['kept']):
            if tests['kept']:
                changed = self.settings.set_radar_from_tests(tests['radar_to_camera_m'], tests['correction_m'],
                                                             kept_ids(tests))
            else:
                changed = self.settings.set_radar_from_tests(None, None, [])
            if changed:
                self.radar_index.clear()
                print('Radar: calibration follows the tests (' + str(tests['kept']) + ' kept)', flush=True)
        return tests

    def add_radar_test(self, body) -> dict:
        """POST /api/radar/tests: the test, and the draft it finishes (draft_id) deleted with it."""
        if not isinstance(body, dict):
            raise WebError(400, 'invalid_param', 'Send {"segment", "standstill", "tape_radar_m", "tape_comma_m"}.')
        body = dict(body)
        draft_id = body.pop('draft_id', None)
        if draft_id is not None and (not isinstance(draft_id, str) or not DRAFT_ID_RE.fullmatch(draft_id)):
            raise WebError(400, 'invalid_param', 'draft_id is the id of a test waiting for its recording.')
        with self._calibration_lock:
            # Checked first: a test sent twice for one draft is saved once.
            if draft_id is not None and self.radar_drafts.get(draft_id) is None:
                raise WebError(404, 'unknown_draft', 'This test is no longer waiting: it was finished or deleted.')
            test = self.radar_tests.add(body, lambda name: load_series(self.repo, name))
            if draft_id is not None:
                self.radar_drafts.remove(draft_id)
            tests = self._follow_tests()
            calibration = self.calibration(tests)
        print('Radar: calibration test added', flush=True)
        return {'test': test, 'tests': tests, 'calibration': calibration}

    def change_radar_test(self, test_id: str, body) -> dict:
        with self._calibration_lock:
            self.radar_tests.change(test_id, body)
            tests = self._follow_tests()
            calibration = self.calibration(tests)
        return {'tests': tests, 'calibration': calibration}

    def apply_radar_tests(self, body) -> dict:
        """POST /api/radar/calibration/apply: the means of the kept tests in place of typed numbers."""
        if body:
            raise WebError(400, 'invalid_param', 'Send {}.')
        with self._calibration_lock:
            tests = self.radar_tests.summary()
            if not tests['kept']:
                raise WebError(409, 'no_tests', 'Keep at least one test to calibrate with.')
            self.settings.set_radar_from_tests(tests['radar_to_camera_m'], tests['correction_m'], kept_ids(tests))
            self.radar_index.clear()
            calibration = self.calibration(tests)
        print('Radar: calibration set from ' + str(tests['kept']) + ' tests', flush=True)
        return {'calibration': calibration}

    def add_radar_draft(self, body) -> dict:
        draft = self.radar_drafts.add(body)
        print('Radar: test saved for later, waiting for its recording', flush=True)
        return {'draft': draft, 'drafts': self.radar_drafts.drafts()}

    def change_radar_draft(self, draft_id: str, body) -> dict:
        self.radar_drafts.change(draft_id, body)
        print('Radar: waiting test deleted', flush=True)
        return {'drafts': self.radar_drafts.drafts()}

    def settings_payload(self) -> dict:
        return {'drives': self.settings.payload(), 'code': code_version(self.repo), 'app': self.app_settings(),
                'android': android_view(android_build(self.repo)),
                'store': {'problems': sorted(self.store.problems.values())}}

    # -- the app (web/API.md) ------------------------------------------------------------------------
    def app_settings(self) -> dict:
        permission = self.phone_keys.permission()
        return {'comma_keys': permission['allowed'], 'until': permission['until'],
                'minutes': PERMISSION_SECONDS // 60, 'phones': self.phone_keys.phones()}

    def set_app(self, body, address: str) -> dict:
        """POST /api/settings/app, "Let the app add phones to the comma". On, {"comma_keys": true, "password"}: a
        session alone is not enough to let anything write on the comma, the administrator's password is checked
        again (AdminAuth, its wrong attempts counted as at sign-in); then the app may add one phone, for
        PERMISSION_SECONDS at most. Off, {"comma_keys": false}, at once. Every change is recorded (PhoneKeys)."""
        allow = body.get('comma_keys') if isinstance(body, dict) else None
        if not isinstance(allow, bool) or set(body) != ({'comma_keys', 'password'} if allow else {'comma_keys'}):
            raise WebError(400, 'invalid_param', 'Send {"comma_keys": true, "password": "<the administrator\'s '
                           'password>"}, or {"comma_keys": false}.')
        if allow:
            self.auth.confirm_password(body['password'], address)
        self.phone_keys.set_permission(allow)
        print('Settings: the app may add one phone to the comma in the next ' + str(PERMISSION_SECONDS // 60)
              + ' minutes' if allow else 'Settings: the app may not add phones to the comma', flush=True)
        return self.app_settings()

    def comma_fingerprints(self) -> list[str]:
        """The comma's ssh-ed25519 host keys in this server's known_hosts (kept a minute: the app asks every 30 s)."""
        comma = self.comma()
        host, fingerprints = self._fingerprints.get(lambda: (comma.host, comma.host_key_fingerprints()))
        if host != comma.host:
            self._fingerprints.clear()
            host, fingerprints = self._fingerprints.get(lambda: (comma.host, comma.host_key_fingerprints()))
        return fingerprints

    def app_info(self) -> dict:
        """GET /api/app/v1."""
        return app_info(code_version(self.repo), self.calibration(), self.comma_fingerprints())

    def app_tests(self) -> dict:
        """GET /api/app/v1/tests: every test, from recordings and from the app, in the order they were saved."""
        return {'ok': True, 'tests': [app_test(test) for test in self.radar_tests.tests()]}

    def add_app_test(self, body) -> dict:
        """POST /api/app/v1/tests: a calibration test made with the app (source "app"), counted in the means when
        kept and re-applied as the others. Sent again with the same client_id, the stored test comes back and
        nothing changes."""
        with self._calibration_lock:
            test, created = self.radar_tests.add_from_app(body)
            # Sent again too: when the calibration could not follow the first time (an error after the test was
            # saved), the app's retry makes it follow now, before the app hears that all is well. Nothing changes
            # when it already follows.
            tests = self._follow_tests()
            calibration = self.calibration(tests)
        if created:
            print('Radar: calibration test from the app added', flush=True)
        return {'ok': True, 'test': app_test(test), 'calibration': app_calibration(calibration)}

    def add_comma_key(self, body) -> dict:
        """POST /api/app/v1/comma-key {"public_key"}: the phone's public key appended to the comma's allowed SSH
        keys, the only thing the site ever writes on the comma, while the owner allows it (set_app): one phone,
        then the permission ends. Each step is recorded (PhoneKeys.record) and printed, the first one before
        anything goes to the comma."""
        if not self.phone_keys.allowed():
            raise WebError(403, 'not_allowed', NOT_ALLOWED)
        if not isinstance(body, dict) or set(body) != {'public_key'}:
            raise WebError(400, 'invalid_param', 'Send {"public_key": "ssh-ed25519 <key> <comment>"}.')
        line, fingerprint, comment = public_key_line(body['public_key'])
        comma = self.comma()
        with self._comma_key_lock:
            # Again under the lock: one phone per permission, however many requests come at once.
            if not self.phone_keys.allowed():
                raise WebError(403, 'not_allowed', NOT_ALLOWED)
            # When this cannot be written (503 busy, 500), nothing goes to the comma.
            self.phone_keys.record(fingerprint, comment, comma.host, 'sending')
            print('Comma: adding a phone key from the app (' + fingerprint + ')', flush=True)
            try:
                result = comma.add_ssh_key(line)
            except WebError as exc:
                print('Comma: the phone key was not added (' + fingerprint + ', ' + exc.reason + ')', flush=True)
                self._key_outcome(fingerprint, comment, comma.host, 'failed', exc.reason)
                raise
            print('Comma: ' + ('the phone key was added (' if result == 'added' else 'the comma already had the '
                  'phone key (') + fingerprint + '); another phone needs the permission again', flush=True)
            self._key_outcome(fingerprint, comment, comma.host, result, used=True)
        return {'ok': True, 'added': result == 'added', 'already': result == 'already'}

    def _key_outcome(self, fingerprint: str, comment: str | None, host: str, outcome: str, detail: str | None = None,
                     used: bool = False) -> None:
        """What the comma answered, after the "sending" row. The comma has the key or not whatever happens here, so
        a failed write is printed and never changes the answer to the app: Settings then shows the key as sent and
        not confirmed (and a permission it should have ended runs out by itself)."""
        try:
            self.phone_keys.record(fingerprint, comment, host, outcome, detail, used=used)
        except WebError as exc:
            print('Comma: what the comma answered (' + outcome + ') could not be recorded (' + exc.reason + '): '
                  'Settings shows the key as sent, not confirmed', flush=True)

    def set_radar(self, body) -> dict:
        """POST /api/settings/radar: numbers typed by hand (source "typed"), or both cleared."""
        with self._calibration_lock:
            answer = self.settings.set_radar(body)
            self.radar_index.clear()
            calibration = self.calibration()
        print('Settings: radar calibration changed (' + ', '.join(sorted(body)) + ')', flush=True)
        return {'drives': answer, 'calibration': calibration}

    def choose_drives(self, body) -> dict:
        answer = self.settings.choose(body)
        self._drives.clear()
        print('Settings: drives folder changed', flush=True)
        return answer

    # -- actions -----------------------------------------------------------------------------------
    def run_action(self, action_id: str, params) -> dict:
        action = ACTIONS.get(action_id)
        if action is None:
            raise WebError(404, 'unknown_action', 'Unknown action.')
        segments = None
        drives = None
        if action.needs_drives:
            self._drives.clear()
            drives = self.drives()
            ready = [row['name'] for row in drives['rows'] if row['ready']]
            if not ready:
                raise WebError(409, 'no_drives', NO_DRIVES[0].upper() + NO_DRIVES[1:] + '.')
            size = SIZES.get(params.get('size', 'standard')) if isinstance(params, dict) else None
            names = spread_sample(ready, size or len(ready))
            segments = [drives['path'] / name for name in names]
        if action_id == 'radar_index':
            folders = self.radar_folders()
            segments = not_indexed(self.repo, folders) or list(folders.values())
        spec = build_job(action_id, params, segments)
        if segments is not None and action.needs_drives:
            def prepare(run_dir: Path, job_id: str) -> None:
                meta = {'kind': action_id, 'label': action.label, 'job': job_id, 'started': time.time(),
                        'segments': len(segments), 'routes': len({folder.name[:18] for folder in segments}),
                        'size': spec.params['size'], 'source': drives['source']['name'],
                        'names': [folder.name for folder in segments]}
                (run_dir / 'experiment.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
            spec.prepare = prepare
        job = self.jobs.start(spec)
        print('Job started: ' + describe(job), flush=True)
        return job


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'perception-web'
        sys_version = ''
        timeout = REQUEST_TIMEOUT_SECONDS
        head_only = False

        def do_GET(self):
            self.head_only = False
            self._dispatch('GET')

        def do_HEAD(self):
            self.head_only = True
            self._dispatch('GET')

        def do_POST(self):
            self.head_only = False
            self._dispatch('POST')

        def log_message(self, *args):
            # Nothing is logged per request.
            pass

        def _dispatch(self, method):
            try:
                self._handle(method)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except WebError as exc:
                self._problem(exc.status, exc.reason, exc.message, self._is_api())
            except Exception as exc:
                print('Internal error: ' + type(exc).__name__ + ': ' + short_text(exc, 200), flush=True)
                self._problem(500, 'server_error', 'Internal server error: ' + type(exc).__name__ + '.',
                              self._is_api())

        def _is_api(self):
            return urlsplit(self.path).path.startswith('/api/')

        def _handle(self, method):
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            api = path.startswith('/api/')
            if not app.host_allowed(self.headers.get('Host')):
                self._problem(403, 'host_not_allowed', 'Host name not allowed for this server.', api)
                return
            if self._session() is None:
                self._public(method, path, api)
                return
            if method == 'POST':
                self._post(path)
            else:
                self._get(path, parse_qs(parts.query, keep_blank_values=True))

        def _cookie_value(self):
            # Plain "name=value; name=value" split: the browser also sends the cookies of the other sites on
            # this host (distill-web on 8766), and a value a stricter parser would not like must not hide ours.
            for raw in self.headers.get_all('Cookie', []):
                for part in raw.split(';'):
                    name, separator, value = part.strip().partition('=')
                    if separator and name.strip() == app.cookie_name:
                        yield value.strip()

        def _session(self):
            for value in self._cookie_value():
                if app.auth.session_valid(value):
                    return value
            return None

        def _session_cookie(self, token):
            parts = [app.cookie_name + '=' + token, 'Path=/', 'HttpOnly', 'SameSite=Strict',
                     'Max-Age=' + str(SESSION_MAX_SECONDS)]
            origin = (self.headers.get('Origin') or '').strip().lower()
            proto = (self.headers.get('X-Forwarded-Proto') or '').strip().lower()
            if origin.startswith('https://') or proto == 'https':
                parts.append('Secure')
            return '; '.join(parts)

        def _expired_cookie(self):
            return app.cookie_name + '=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'

        def _public(self, method, path, api):
            """A request without a session: the sign-in page, its files and the sign-in calls only."""
            if method == 'GET' and path in ('/', '/index.html'):
                self._page('login.html')
                return
            match = re.fullmatch(r'/static/([a-z]+\.[a-z]+)', path)
            if method == 'GET' and match and match.group(1) in PUBLIC_STATIC:
                self._static(match.group(1))
                return
            if method == 'GET' and path == '/api/auth/state':
                self._json(200, self._auth_state(False))
                return
            if method == 'POST' and path in PUBLIC_AUTH_ROUTES:
                self._post(path)
                return
            self._problem(401, 'login_required', LOGIN_MESSAGE, api)

        def _auth_state(self, signed_in):
            state = {'configured': app.auth.configured(), 'signed_in': signed_in, 'api': API_VERSION}
            if signed_in:
                state['user'] = app.auth.username()
                state['sessions'] = app.auth.sessions_count()
            return state

        def _page(self, name):
            nonce = secrets.token_urlsafe(18)
            page = (STATIC_DIR / name).read_text(encoding='utf-8').replace(NONCE_MARK, nonce)
            self._send(200, 'text/html; charset=utf-8', page.encode('utf-8'), page_csp(nonce))

        def _static(self, name):
            self._send(200, STATIC_FILES[name], (STATIC_DIR / name).read_bytes())

        # -- GET -----------------------------------------------------------------------------------
        def _get(self, path, query):
            if path in ('/', '/index.html'):
                self._page('index.html')
                return
            match = re.fullmatch(r'/static/([a-z]+\.[a-z]+)', path)
            if match:
                if match.group(1) not in STATIC_FILES:
                    self._problem(404, 'not_found', 'File not found.', False)
                    return
                self._static(match.group(1))
                return
            if path == '/api/auth/state':
                self._json(200, self._auth_state(True))
                return
            if path == '/api/status':
                self._json(200, app.status_payload())
                return
            if path == '/api/results':
                self._json(200, {'results': app.results.results()})
                return
            if path == '/api/settings':
                self._json(200, app.settings_payload())
                return
            if path == '/api/jobs':
                self._json(200, {'jobs': app.jobs.list_jobs()})
                return
            match = re.fullmatch(r'/api/jobs/([^/]+)', path)
            if match:
                self._json(200, app.jobs.get(match.group(1)))
                return
            match = re.fullmatch(r'/api/jobs/([^/]+)/log', path)
            if match:
                raw_offset = query.get('offset', ['0'])[0]
                offset = int(raw_offset) if re.fullmatch(r'[0-9]{1,15}', raw_offset) else 0
                self._json(200, app.jobs.log(match.group(1), offset))
                return
            if path == '/api/radar':
                self._json(200, app.radar_payload())
                return
            match = re.fullmatch(r'/api/radar/drives/([^/]+)', path)
            if match:
                self._json(200, app.radar_drive(match.group(1)))
                return
            if path == '/api/comma/recordings':
                self._json(200, app.comma_recordings())
                return
            if path == APP_PATH:
                self._json(200, app.app_info())
                return
            if path == APP_PATH + '/tests':
                self._json(200, app.app_tests())
                return
            if path in ('/download/android', '/download/android/aab'):
                self._download(path.endswith('/aab'))
                return
            match = re.fullmatch(r'/picture/radar/([^/]+)/([0-9]{1,7})\.jpg', path)
            if match:
                self._send(200, 'image/jpeg', app.radar_picture(match.group(1), int(match.group(2))),
                           extra=[('Cache-Control', 'private, max-age=86400')])
                return
            match = re.fullmatch(r'/picture/([^/]+)/([^/]+)', path)
            if match:
                picture = picture_path(app.repo, match.group(1), match.group(2))
                if picture is None:
                    self._problem(404, 'not_found', 'Picture not found.', False)
                    return
                self._send(200, 'image/jpeg', picture.read_bytes(), extra=[('Cache-Control', 'private, max-age=86400')])
                return
            if path.startswith(('/api/run/', '/api/settings/', '/api/radar/tests', '/api/radar/calibration',
                                '/api/radar/drafts')) \
                    or path in AUTH_ROUTES or path == APP_PATH + '/comma-key' \
                    or re.fullmatch(r'/api/jobs/[^/]+/stop', path):
                self._problem(405, 'method_not_allowed', 'Use POST.', True)
                return
            self._problem(404, 'not_found', 'Page not found.', path.startswith('/api/'))

        # -- POST ----------------------------------------------------------------------------------
        def _post(self, path):
            run = re.fullmatch(r'/api/run/([a-z_]+)', path)
            stop = re.fullmatch(r'/api/jobs/([^/]+)/stop', path)
            drives = path == '/api/settings/drives'
            radar = path == '/api/settings/radar'
            tests = path == '/api/radar/tests'
            test = re.fullmatch(r'/api/radar/tests/([0-9a-f]{12})', path)
            apply = path == '/api/radar/calibration/apply'
            drafts = path == '/api/radar/drafts'
            draft = re.fullmatch(r'/api/radar/drafts/([0-9a-f]{12})', path)
            auth = path in AUTH_ROUTES
            app_settings = path == '/api/settings/app'
            app_tests = path == APP_PATH + '/tests'
            comma_key = path == APP_PATH + '/comma-key'
            if not any((run, stop, drives, radar, tests, test, apply, drafts, draft, auth, app_settings, app_tests,
                        comma_key)):
                self._problem(404, 'not_found', 'Invalid address.', True)
                return
            # Same origin and JSON only: no other site and no HTML form can start a job.
            if not self._same_origin():
                self._problem(403, 'cross_origin', 'Request from another origin refused.', True)
                return
            content_type = self.headers.get('Content-Type', '')
            if content_type.split(';')[0].strip().lower() != 'application/json':
                self._problem(415, 'unsupported_media_type', 'Only JSON data is accepted.', True)
                return
            body = self._read_json()
            if auth:
                self._auth_post(path, body)
            elif run:
                self._json(200, {'ok': True, 'job': app.run_action(run.group(1), body)})
            elif stop:
                job_id = stop.group(1)
                if not JOB_ID_RE.fullmatch(job_id):
                    raise WebError(404, 'unknown_job', 'Unknown job.')
                job = app.jobs.stop(job_id)
                print('Job stopped from the site: ' + describe(job), flush=True)
                self._json(200, {'ok': True, 'job': job})
            elif radar:
                self._json(200, {'ok': True, **app.set_radar(body)})
            elif tests:
                self._json(200, {'ok': True, **app.add_radar_test(body)})
            elif test:
                self._json(200, {'ok': True, **app.change_radar_test(test.group(1), body)})
            elif apply:
                self._json(200, {'ok': True, **app.apply_radar_tests(body)})
            elif drafts:
                self._json(200, {'ok': True, **app.add_radar_draft(body)})
            elif draft:
                self._json(200, {'ok': True, **app.change_radar_draft(draft.group(1), body)})
            elif app_settings:
                self._json(200, {'ok': True, 'app': app.set_app(body, self.client_address[0])})
            elif app_tests:
                self._json(200, app.add_app_test(body))
            elif comma_key:
                self._json(200, app.add_comma_key(body))
            else:
                self._json(200, {'ok': True, 'drives': app.choose_drives(body)})

        def _auth_post(self, path, body):
            address = self.client_address[0]
            if path == '/api/auth/setup/check':
                app.auth.check_setup_code(body.get('code'), address)
                self._json(200, {'ok': True})
            elif path == '/api/auth/setup':
                token = app.auth.setup(body.get('code'), body.get('user'), body.get('password'), address)
                self._json(200, {'ok': True}, [('Set-Cookie', self._session_cookie(token))])
            elif path == '/api/auth/login':
                token = app.auth.login(body.get('user'), body.get('password'), address)
                print('Site: administrator signed in', flush=True)
                self._json(200, {'ok': True}, [('Set-Cookie', self._session_cookie(token))])
            elif path == '/api/auth/password':
                token = app.auth.change_password(self._session(), body.get('current'), body.get('password'), address)
                self._json(200, {'ok': True, 'state': self._auth_state(True)},
                           [('Set-Cookie', self._session_cookie(token))])
            else:
                app.auth.logout(self._session(), everywhere=path == '/api/auth/logout-all')
                self._json(200, {'ok': True}, [('Set-Cookie', self._expired_cookie())])

        def _download(self, bundle: bool):
            """The newest APK of outputs/android/latest.json, or its bundle for Google Play."""
            build = android_build(app.repo)
            item = (build or {}).get('bundle') if bundle else build
            if item is None:
                self._problem(404, 'not_found', 'No bundle for Google Play has been built on this server.' if bundle
                              else 'No Android app has been built on this server yet.', False)
                return
            self._send(200, 'application/octet-stream' if bundle else APK_TYPE, item['path'].read_bytes(),
                       extra=[('Content-Disposition', 'attachment; filename="' + item['file'] + '"'),
                              ('Cache-Control', 'no-store')])

        def _same_origin(self):
            fetch_site = self.headers.get('Sec-Fetch-Site')
            if fetch_site is not None and fetch_site not in ('same-origin', 'none'):
                return False
            origin = self.headers.get('Origin')
            if origin is None:
                return True
            host = self.headers.get('Host')
            if not host:
                return False
            supplied = origin.strip().lower()
            return supplied in (('http://' + host.strip()).lower(), ('https://' + host.strip()).lower())

        def _read_json(self):
            if self.headers.get('Transfer-Encoding') is not None:
                raise WebError(411, 'length_required', 'Request without Content-Length.')
            raw_length = self.headers.get('Content-Length')
            if raw_length is None or not re.fullmatch(r'[0-9]{1,9}', raw_length.strip()):
                raise WebError(411, 'length_required', 'Request without Content-Length.')
            length = int(raw_length.strip())
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                raise WebError(413, 'too_large', 'Request too large (at most 64 KiB).')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise WebError(400, 'invalid_json', 'Incomplete request.')
            if length == 0:
                return {}
            try:
                data = strict_json_loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
                raise WebError(400, 'invalid_json', 'Invalid JSON data.') from exc
            if not isinstance(data, dict):
                raise WebError(400, 'invalid_json', 'A JSON object is required.')
            return data

        # -- responses -----------------------------------------------------------------------------
        def _security_headers(self, csp):
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', csp)

        def _send(self, status, content_type, body, csp=DEFAULT_CSP, extra=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            extra = extra or []
            if any(name == 'Cache-Control' for name, _ in extra):
                # A picture may be kept by the browser: the rest of the headers stay the same.
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
                self.send_header('Content-Security-Policy', csp)
            else:
                self._security_headers(csp)
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            if not self.head_only:
                self.wfile.write(body)

        def _json(self, status, payload, extra=None):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self._send(status, 'application/json; charset=utf-8', body, extra=extra)

        def _problem(self, status, reason, message, api):
            if api:
                self._json(status, {'ok': False, 'error': reason, 'message': message})
            else:
                self._send(status, 'text/html; charset=utf-8', render_problem(status, message).encode('utf-8'),
                           PROBLEM_CSP)

    return Handler


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class):
        self._connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            # Every slot is busy: close this connection now instead of starting another thread.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


class WebServerIPv6(WebServer):
    address_family = socket.AF_INET6


def create_server(repo: Path, *, host: str = '127.0.0.1', port: int = DEFAULT_PORT, setup_code: str | None = None,
                  local_only: bool | None = None, app: App | None = None):
    """Binds the server (not started). Without a setup code a random one is made (printed by main)."""
    if local_only is None:
        local_only = is_loopback(host)
    if setup_code is None:
        setup_code = access_token()
    if app is None:
        app = App(repo, setup_code=setup_code, local_only=local_only)
    else:
        app.local_only = local_only
        app.auth.setup_code = setup_code
    server_class = WebServerIPv6 if ':' in host else WebServer
    server = server_class((host, port), make_handler(app))
    # One cookie per port, so the sites of one machine (distill-web on 8766) never mix them up.
    app.cookie_name = 'perception_web_' + str(server.server_port)
    return server, app


def base_urls(host: str, port: int) -> list[str]:
    if host in ('0.0.0.0', '', '::'):
        names = lan_addresses() or [socket.gethostname()]
    elif ':' in host:
        names = ['[' + host + ']']
    else:
        names = [host]
    return ['http://' + name + ':' + str(port) for name in names]


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m web.server', description='perception-web, the control site')
    parser.add_argument('--host', default='127.0.0.1', help='address (default 127.0.0.1; 0.0.0.0 = local network)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='port (default 8767)')
    options = parser.parse_args(arguments)

    token_from_env = bool(os.environ.get(TOKEN_ENV))
    try:
        server, app = create_server(REPO, host=options.host, port=options.port)
    except OSError as exc:
        print('Cannot start the site on ' + options.host + ':' + str(options.port) + ': ' + str(exc),
              file=sys.stderr, flush=True)
        return 1
    with server:
        port = server.server_port
        print('perception-web listening on ' + options.host + ':' + str(port), flush=True)
        print('Open in the browser:', flush=True)
        for base in base_urls(options.host, port):
            print('  ' + base + '/', flush=True)
        if app.auth.configured():
            print('Sign in as the administrator (' + str(app.auth.username()) + ').', flush=True)
        elif token_from_env:
            print('First visit: create the administrator with the setup code, the value of ' + TOKEN_ENV
                  + ' (service: /etc/perception-web.env, by hand: .env of the clone).', flush=True)
        else:
            print('First visit: create the administrator with this setup code: ' + app.auth.setup_code, flush=True)
        if app.local_only:
            print('Only from this computer. From another one: ssh -L ' + str(port) + ':127.0.0.1:' + str(port)
                  + ' <server>, or restart with --host 0.0.0.0', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
