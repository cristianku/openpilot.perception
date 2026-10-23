"""perception-web for Perception for Android (web/API.md): the store and its import of the old JSON files, the app's
tests (validation, idempotency, the calibration they move), the phone key on the comma, the downloads, and the
contract against the app's own fixtures. Synthetic data only; ssh is always a fake: nothing goes to a real comma."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_web import ADMIN, PASSWORD, TOKEN, FakeJobs, ServerTestCase
from test_web_calibration import WALL, WALL2, write_recording

import web.auth
import web.store
from web.app_api import API_VERSION, PERMISSION_SECONDS, PhoneKeys, android_build
from web.comma import (ADD_KEY_SCRIPT, COMMA_SSH_KEYS, ED25519_PREFIX, SSH_OPTIONS, Comma, fingerprint_of,
                       known_host_fingerprints, public_key_line)
from web.common import WebError
from web.radar import RadarDrafts, RadarTests, app_test_of
from web.server import App
from web.settings import Settings
from web.store import DB_NAME, SCHEMA_VERSION, Store

FIXTURES = Path(__file__).resolve().parent.parent / 'app' / 'android' / 'app' / 'src' / 'test' / 'resources' / \
    'fixtures'
CLIENT_ID = '6f1c1f9e-6c7e-4c64-9f43-1d1a9b0e2a11'
FINGERPRINT_RE = re.compile(r'SHA256:[A-Za-z0-9+/]{43}')
DELETE = object()


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def upload(**changes) -> dict:
    """test_upload_body.json, the body the app sends, changed: capture__reading_m=... for a nested key, DELETE to
    leave one out."""
    body = fixture('test_upload_body.json')
    for name, value in changes.items():
        *path, last = name.split('__')
        target = body
        for part in path:
            target = target[part]
        if value is DELETE:
            del target[last]
        else:
            target[last] = value
    return body


def phone_key(seed: int = 1, comment: str | None = 'perception-app@Pixel-8') -> tuple[str, str]:
    """A made-up phone key line and its fingerprint."""
    blob = ED25519_PREFIX + bytes((seed + index) % 256 for index in range(32))
    line = 'ssh-ed25519 ' + base64.b64encode(blob).decode('ascii')
    return (line + ' ' + comment if comment else line), fingerprint_of(blob)


def known_hosts_line(seed: int = 7, kind: str = 'ssh-ed25519') -> tuple[str, str]:
    blob = ED25519_PREFIX + bytes((seed * 3 + index) % 256 for index in range(32))
    return '|1|c2FsdA==|aGFzaA== ' + kind + ' ' + base64.b64encode(blob).decode('ascii'), fingerprint_of(blob)


class FakeSsh:
    """subprocess.run for the comma: records every call and answers by kind; it never runs anything."""

    def __init__(self):
        self.calls = []
        self.add = (0, 'added\n')
        self.config = (0, '')
        self.keygen = (1, '')

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options))
        if argv[:2] == ['ssh', '-G']:
            reply = self.config
        elif argv[0] == 'ssh-keygen':
            reply = self.keygen
        elif argv[0] == 'ssh' and argv[-1] == ADD_KEY_SCRIPT:
            reply = self.add
        else:
            reply = (255, '')
        if isinstance(reply, BaseException):
            raise reply
        return subprocess.CompletedProcess(argv, reply[0], reply[1], '')

    def additions(self):
        return [call for call in self.calls if call[0][-1] == ADD_KEY_SCRIPT]


# -- the store -------------------------------------------------------------------------------------------
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.folder = self.repo / 'outputs' / 'web'

    def write(self, name, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def test_a_new_store(self):
        store = Store.at(self.folder)
        self.assertIs(Store.at(self.folder), store)
        self.assertEqual(store.path, self.folder / DB_NAME)
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((store.schema_version(), SCHEMA_VERSION), (2, 2))
        RadarDrafts(self.repo).add({'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8})
        raw = sqlite3.connect(store.path)
        self.addCleanup(raw.close)
        self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(raw.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], '2')
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({'meta', 'radar_tests', 'radar_drafts', 'radar_calibrations', 'comma_keys', 'imports',
                              'app_permissions'}, tables)
        # The journal files of WAL take the mode of the database.
        for suffix in ('-wal', '-shm'):
            journal = Path(str(store.path) + suffix)
            if journal.exists():
                self.assertEqual(journal.stat().st_mode & 0o777, 0o600, suffix)
        self.assertEqual(store.problems, {})

    def test_the_json_files_are_imported_once(self):
        tests = [{'id': 'aaaaaaaaaaaa', 'segment': WALL, 'route': WALL[:20], 'target': 'wall', 'from_s': 2.5,
                  'to_s': 20.0, 'reading_m': 2.25, 'tape_radar_m': 1.32, 'tape_comma_m': 2.82, 'correction_m': -0.93,
                  'kept': True, 'created': 1_792_000_000.0},
                 {'id': 'bbbbbbbbbbbb', 'segment': '0000011e0c8825628a01', 'reading_m': 10.75, 'tape_radar_m': 10.0,
                  'tape_comma_m': 11.5, 'kept': False},
                 {'id': 'broken', 'note': 'by hand'}]
        drafts = [{'id': 'dddddddddddd', 'created': 1_792_100_000.0, 'target': 'car', 'tape_radar_m': 10.2,
                   'tape_comma_m': 11.7, 'marker_counter': 0x133}]
        history = [{'version': 1, 'at': 1_792_000_001.0, 'radar_to_camera_m': 1.5, 'radar_correction_m': -0.93,
                    'source': 'tests', 'test_ids': ['aaaaaaaaaaaa']},
                   {'version': 2, 'at': 1_792_000_002.0, 'radar_to_camera_m': 1.45, 'radar_correction_m': -0.5,
                    'source': 'typed', 'test_ids': []}]
        texts = {name: self.write(name, value).read_text() for name, value in
                 (('radar_tests.json', tests), ('radar_drafts.json', drafts), ('radar_calibrations.json', history))}
        Store.at(self.folder)
        stored = RadarTests(self.repo).summary()['tests']
        self.assertEqual([(t['id'], t['target'], t['kept'], t['correction_m']) for t in stored],
                         [('aaaaaaaaaaaa', 'wall', True, -0.93), ('bbbbbbbbbbbb', 'wall', False, -0.75)])
        self.assertEqual(RadarDrafts(self.repo).drafts(), [{'id': 'dddddddddddd', 'created': 1_792_100_000.0,
                                                            'target': 'car', 'tape_radar_m': 10.2,
                                                            'tape_comma_m': 11.7, 'marker_counter': 0x133}])
        self.assertEqual(Settings(self.repo).radar_history(), history)
        # Renamed, as they were: the entry that could not be imported is still there.
        for name, text in texts.items():
            self.assertFalse((self.folder / name).exists(), name)
            self.assertEqual((self.folder / (name + '.imported')).read_text(), text, name)
        # The next start (another process) imports nothing again.
        again = Store(self.folder / DB_NAME)
        self.addCleanup(again._db.close)
        self.assertEqual(len(again.rows('SELECT id FROM radar_tests')), 2)
        self.assertEqual(len(again.rows('SELECT * FROM imports')), 3)
        # A file written again by an older site still running: only what is new comes in.
        self.write('radar_tests.json', [tests[0], dict(tests[0], id='cccccccccccc', created=1_792_200_000.0)])
        self.assertEqual([t['id'] for t in RadarTests(self.repo).tests()],
                         ['aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc'])
        self.assertTrue((self.folder / 'radar_tests.json.imported.1').is_file())

    def test_empty_and_missing_files(self):
        self.write('radar_tests.json', '[]')
        self.write('radar_drafts.json', '')
        store = Store.at(self.folder)
        self.assertEqual((store.problems, RadarTests(self.repo).tests(), RadarDrafts(self.repo).drafts()), ({}, [], []))
        self.assertEqual(sorted(path.name for path in self.folder.iterdir() if '.json' in path.name),
                         ['radar_drafts.json.imported', 'radar_tests.json.imported'])
        self.assertEqual(Settings(self.repo).radar_history(), [])

    def test_a_damaged_file_is_left_as_it_is_and_reported(self):
        cut = '[{"id": "bbbbbbbbbbbb", "segment": "' + WALL + '", "reading_m": 2.25'
        path = self.write('radar_tests.json', cut)
        self.write('radar_drafts.json', '{"not": "a list"}')
        store = Store.at(self.folder)
        self.assertEqual(sorted(store.problems), ['radar_drafts.json', 'radar_tests.json'])
        self.assertIn('outputs/web/radar_tests.json', store.problems['radar_tests.json'])
        tests = RadarTests(self.repo)
        self.assertEqual(tests.tests(), [])
        for call in (lambda: tests.add({'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32, 'tape_comma_m': 2.82},
                                       lambda name: {'standstills': [{'from_s': 1, 'to_s': 9, 'reading_m': 2.25}]}),
                     lambda: tests.add_from_app(upload()),
                     lambda: RadarDrafts(self.repo).add({'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8})):
            with self.assertRaises(WebError) as refused:
                call()
            self.assertEqual((refused.exception.status, refused.exception.reason), (500, 'unreadable'))
        self.assertEqual(path.read_text(), cut)
        self.assertEqual(store.rows('SELECT * FROM radar_tests'), [])
        # Fixed by hand: imported at its next use.
        path.write_text(cut + ', "tape_radar_m": 1.32, "tape_comma_m": 2.82}]')
        self.assertEqual([t['id'] for t in tests.tests()], ['bbbbbbbbbbbb'])
        self.assertEqual(sorted(store.problems), ['radar_drafts.json'])
        (self.folder / 'radar_drafts.json').unlink()
        self.assertEqual(RadarDrafts(self.repo).drafts(), [])
        RadarDrafts(self.repo).add({'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8})
        self.assertEqual(store.problems, {})

    def test_a_file_whose_rename_failed_is_never_imported_twice(self):
        self.write('radar_drafts.json', [{'id': 'dddddddddddd', 'target': 'wall', 'tape_radar_m': 1.3,
                                          'tape_comma_m': 2.8}])
        with mock.patch.object(web.store.os, 'rename', side_effect=PermissionError('read-only')):
            store = Store.at(self.folder)
        self.assertTrue((self.folder / 'radar_drafts.json').is_file())
        self.assertEqual(len(RadarDrafts(self.repo).drafts()), 1)
        self.assertFalse((self.folder / 'radar_drafts.json').exists())
        again = Store(store.path)
        self.addCleanup(again._db.close)
        self.assertEqual(len(again.rows('SELECT * FROM radar_drafts')), 1)

    def test_a_write_is_one_transaction(self):
        store = Store.at(self.folder)
        with self.assertRaises(RuntimeError):
            with store.write() as db:
                db.execute("INSERT INTO radar_drafts (id, target, tape_radar_m, tape_comma_m) "
                           "VALUES ('aaaaaaaaaaaa', 'wall', 1.3, 2.8)")
                raise RuntimeError('stopped half way')
        with self.assertRaises(WebError) as refused:
            with store.write() as db:
                db.execute("INSERT INTO radar_drafts (id, target, tape_radar_m, tape_comma_m) "
                           "VALUES ('aaaaaaaaaaaa', 'wall', 1.3, 2.8)")
                db.execute("INSERT INTO radar_drafts (id, target, tape_radar_m, tape_comma_m) "
                           "VALUES ('bbbbbbbbbbbb', 'tree', 1.3, 2.8)")
        self.assertEqual(refused.exception.reason, 'cannot_save')
        self.assertEqual(store.rows('SELECT * FROM radar_drafts'), [])

    def test_the_logs_are_append_only(self):
        settings = Settings(self.repo)
        settings.set_radar({'radar_to_camera_m': 1.5})
        keys = PhoneKeys(self.repo)
        keys.record('SHA256:' + 'A' * 43, 'phone', 'comma', 'sending')
        keys.set_permission(True)
        for statement in ('UPDATE radar_calibrations SET version = 9', 'DELETE FROM radar_calibrations',
                          "UPDATE comma_keys SET outcome = 'added'", 'DELETE FROM comma_keys',
                          'UPDATE app_permissions SET allowed = 0', 'DELETE FROM app_permissions'):
            with self.assertRaises(WebError), settings.store.write() as db:
                db.execute(statement)
        self.assertEqual([entry['version'] for entry in settings.radar_history()], [1])
        self.assertEqual([phone['state'] for phone in keys.phones()], ['unconfirmed'])
        self.assertTrue(keys.allowed())
        for row in ((1.0, 2, None, 'owner'), (1.0, 1, None, 'owner'), (1.0, 0, None, 'phone')):
            with self.assertRaises(WebError), settings.store.write() as db:
                db.execute('INSERT INTO app_permissions (at, allowed, until, why) VALUES (?, ?, ?, ?)', row)
        with self.assertRaises(WebError), settings.store.write() as db:
            db.execute("INSERT INTO comma_keys (at, fingerprint, host, outcome) VALUES (1, 'x', 'comma', 'gone')")

    def test_a_database_of_schema_1_is_migrated(self):
        self.folder.mkdir(parents=True)
        raw = sqlite3.connect(self.folder / DB_NAME, isolation_level=None)
        raw.execute('PRAGMA journal_mode=WAL')
        raw.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        for statement in web.store.SCHEMA:
            raw.execute(statement)
        raw.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
        raw.execute("INSERT INTO comma_keys (at, fingerprint, comment, host) VALUES (5.0, 'SHA256:old', 'phone', "
                    "'comma')")
        raw.close()
        store = Store.at(self.folder)
        self.assertEqual(store.schema_version(), 2)
        # A key of schema 1 was added: the only rows it had.
        self.assertEqual(PhoneKeys(self.repo).phones(), [{'at': 5.0, 'last': 5.0, 'fingerprint': 'SHA256:old',
                                                          'comment': 'phone', 'host': 'comma', 'state': 'added'}])
        self.assertFalse(PhoneKeys(self.repo).allowed())

    def test_writes_are_serialised(self):
        tests = RadarTests(self.repo)
        barrier = threading.Barrier(12)
        answers, errors = [], []

        def send(client_id):
            try:
                barrier.wait()
                answers.append(tests.add_from_app(upload(client_id=client_id)))
            except Exception as exc:   # noqa: BLE001 - reported below
                errors.append(exc)

        ids = [f'{index:08x}-0000-4000-8000-000000000000' for index in range(6)] + [CLIENT_ID] * 6
        threads = [threading.Thread(target=send, args=(client_id,)) for client_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        stored = tests.tests()
        self.assertEqual(len(stored), 7)
        self.assertEqual(len({test['id'] for test in stored}), 7)
        # Six times the same test: stored once, the others got it back.
        self.assertEqual(sum(1 for _, created in answers if created), 7)
        self.assertEqual(len({test['id'] for test, _ in answers if test['client_id'] == CLIENT_ID}), 1)

    def test_a_newer_database_is_never_opened(self):
        store = Store.at(self.folder)
        with store.write() as db:
            db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        with self.assertRaises(WebError) as refused:
            Store(store.path)
        self.assertEqual(refused.exception.reason, 'store_too_new')

    def test_never_through_a_link(self):
        self.folder.mkdir(parents=True)
        elsewhere = self.repo / 'elsewhere.sqlite'
        elsewhere.write_bytes(b'')
        os.symlink(elsewhere, self.folder / DB_NAME)
        with self.assertRaises(WebError) as refused:
            Store.at(self.folder)
        self.assertEqual(refused.exception.reason, 'file_is_a_link')


# -- the tests of the app --------------------------------------------------------------------------------
class AppTests(unittest.TestCase):
    """POST /api/app/v1/tests behind the handler: what is stored, what the calibration does, what is refused."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        write_recording(self.repo, WALL, 2.25)
        write_recording(self.repo, WALL2, 1.75)
        self.app = App(self.repo, setup_code=TOKEN, jobs=FakeJobs(), gpu_check=lambda: '')

    def test_a_test_from_the_app(self):
        answer = self.app.add_app_test(upload())
        self.assertEqual(set(answer), {'ok', 'test', 'calibration'})
        test = answer['test']
        self.assertEqual(set(test), set(fixture('test_post_reply.json')['test']))
        self.assertEqual((test['client_id'], test['route'], test['reading_m'], test['correction_m'], test['kept']),
                         (CLIENT_ID, '00000134--a5c8197883', 2.25, -0.93, True))
        calibration = answer['calibration']
        self.assertEqual((calibration['source'], calibration['version'], calibration['radar_correction_m'],
                          calibration['radar_to_camera_m'], calibration['tested_m']), ('tests', 1, -0.93, 1.5, [1.32]))
        # The same table as the tests from recordings, labelled for the page.
        view = self.app.radar_payload()['tests']['tests'][0]
        self.assertEqual((view['source'], view['segment'], view['route'], view['recording_number'],
                          view['made_unix'], view['note'], view['git_commit'], view['from_s']),
                         ('app', None, '00000134--a5c8197883', 0x134, 1792771200.5, '', '1b6a472a', None))
        row = self.app.store.rows('SELECT * FROM radar_tests')[0]
        self.assertEqual(json.loads(row['capture'])['histogram'], {'2.25': 176, '2.50': 4})
        self.assertEqual(self.app.settings.radar_history()[-1]['test_ids'], [test['id']])

    def test_sent_again_it_is_the_same_test(self):
        first = self.app.add_app_test(upload())
        again = self.app.add_app_test(upload())
        self.assertEqual(again['test'], first['test'])
        self.assertEqual(again['calibration']['version'], 1)
        self.assertEqual(len(self.app.radar_tests.tests()), 1)
        # Another test under the same id: refused, nothing changes.
        with self.assertRaises(WebError) as refused:
            self.app.add_app_test(upload(tape_radar_m=1.30))
        self.assertEqual((refused.exception.status, refused.exception.reason), (409, 'client_id_taken'))
        # Deleted on the site: the app may send it again, a new test.
        self.app.change_radar_test(first['test']['id'], {'delete': True})
        self.assertEqual(self.app.calibration()['status'], 'not_set')
        back = self.app.add_app_test(upload())
        self.assertNotEqual(back['test']['id'], first['test']['id'])
        self.assertEqual(back['calibration']['source'], 'tests')

    def test_sent_again_after_an_error_the_calibration_follows(self):
        # Saved, then the calibration could not follow (settings.json not written): the app hears 500 ...
        failing = WebError(500, 'cannot_save', 'Cannot save it on this server.')
        with mock.patch.object(self.app.settings, 'set_radar_from_tests', side_effect=failing):
            with self.assertRaises(WebError) as refused:
                self.app.add_app_test(upload())
        self.assertEqual(refused.exception.reason, 'cannot_save')
        self.assertEqual(len(self.app.radar_tests.tests()), 1)
        self.assertEqual(self.app.calibration()['status'], 'not_set')
        # ... and sends it again: the same test, and only now all is well, with the calibration of it.
        again = self.app.add_app_test(upload())
        self.assertEqual(len(self.app.radar_tests.tests()), 1)
        self.assertEqual((again['calibration']['source'], again['calibration']['version'],
                          again['calibration']['tested_m']), ('tests', 1, [1.32]))
        self.assertEqual(self.app.add_app_test(upload())['calibration']['version'], 1)

    def test_counted_with_the_tests_of_recordings(self):
        recording = self.app.add_radar_test({'segment': WALL2, 'standstill': 0, 'tape_radar_m': 0.9,
                                             'tape_comma_m': 2.4})['test']
        answer = self.app.add_app_test(upload())
        self.assertEqual((answer['calibration']['radar_correction_m'], answer['calibration']['tested_m'],
                          answer['calibration']['version']), (-0.89, [0.9, 1.32], 2))
        # Left out, it no longer counts; the recording's test alone.
        left = self.app.change_radar_test(answer['test']['id'], {'kept': False})['calibration']
        self.assertEqual((left['radar_correction_m'], left['tested_m']), (-0.85, [0.9]))
        self.app.change_radar_test(answer['test']['id'], {'kept': True})
        # Numbers typed by hand stay: the tests are only offered.
        self.app.change_radar_test(recording['id'], {'delete': True})
        self.app.set_radar({'radar_to_camera_m': 1.45, 'radar_correction_m': -0.5})
        typed = self.app.add_app_test(upload(client_id='00000000-0000-4000-8000-000000000001'))['calibration']
        self.assertEqual((typed['source'], typed['radar_correction_m']), ('typed', -0.5))
        self.assertEqual(self.app.calibration()['tests_give'], {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.93})

    def test_every_field_is_checked(self):
        histogram = {'2.25': 176, '2.50': 4}
        bad = {
            'missing key': upload(note=DELETE), 'extra key': upload(segment=WALL),
            'capture missing key': upload(capture__onroad=DELETE), 'capture extra key': upload(capture__steady_s=17.6),
            'comma missing key': upload(comma__git_commit=DELETE), 'comma extra key': upload(comma__branch='x'),
            'capture not an object': upload(capture=[]), 'comma not an object': upload(comma=None),
            'client_id': upload(client_id='abc'), 'client_id upper': upload(client_id=CLIENT_ID.upper()),
            'client_id number': upload(client_id=42),
            'created_unix text': upload(created_unix='now'), 'created_unix bool': upload(created_unix=True),
            'created_unix 1973': upload(created_unix=1e8), 'created_unix 2128': upload(created_unix=5e9),
            'created_unix NaN': upload(created_unix=float('nan')),
            'target': upload(target='tree'),
            'comma before radar': upload(tape_comma_m=1.0), 'tape too short': upload(tape_radar_m=0.1),
            'tape too long': upload(tape_comma_m=101.0),
            'comma 6 m behind': upload(tape_radar_m=1.32, tape_comma_m=7.4),
            'started_unix': upload(capture__started_unix=0), 'duration 0': upload(capture__duration_s=0),
            'duration 601': upload(capture__duration_s=601), 'duration text': upload(capture__duration_s='20'),
            'samples negative': upload(capture__samples=-1), 'samples float': upload(capture__samples=200.5),
            'samples bool': upload(capture__samples=True), 'samples too many': upload(capture__samples=100_001),
            'with_reading over samples': upload(capture__with_reading=201),
            'no reading': upload(capture__reading_m=None), 'reading 0': upload(capture__reading_m=0),
            'reading 301': upload(capture__reading_m=301), 'reading text': upload(capture__reading_m='2.25'),
            'steady_share over 1': upload(capture__steady_share=1.2),
            'steady_share null': upload(capture__steady_share=None),
            'histogram list': upload(capture__histogram=[176, 4]),
            'histogram key': upload(capture__histogram={'2.255': 176, '2.50': 4}),
            'histogram text key': upload(capture__histogram={'far': 176, '2.50': 4}),
            'histogram count 0': upload(capture__histogram={'2.25': 180, '2.50': 0}),
            'histogram count bool': upload(capture__histogram={'2.25': 179, '2.50': True}),
            'histogram sum': upload(capture__histogram={'2.25': 170, '2.50': 4}),
            'histogram without the reading': upload(capture__histogram={'2.00': 176, '2.50': 4}),
            'steady_share against histogram': upload(capture__steady_share=0.9),
            'target only part of the time': upload(capture__samples=400, capture__histogram=histogram),
            'reading changing': upload(capture__histogram={'2.25': 140, '2.50': 40}, capture__steady_share=0.78),
            'speed negative': upload(capture__speed_max_mps=-0.1), 'speed text': upload(capture__speed_max_mps='0'),
            'speed 101': upload(capture__speed_max_mps=101),
            'onroad text': upload(capture__onroad='yes'), 'onroad null': upload(capture__onroad=None),
            'route': upload(comma__route='00000134'), 'route segment': upload(comma__route=WALL),
            'route number': upload(comma__route=12),
            'git_commit 6': upload(comma__git_commit='1b6a47'), 'git_commit 41': upload(comma__git_commit='a' * 41),
            'git_commit upper': upload(comma__git_commit='1B6A472A'), 'git_commit number': upload(comma__git_commit=7),
            'note 501': upload(note='x' * 501), 'note control': upload(note='bad\x00note'),
            'note escape': upload(note='\x1b[31m'), 'note number': upload(note=7), 'note null': upload(note=None),
            'more than 1 m apart': upload(tape_radar_m=3.5, tape_comma_m=5.0),
            'not an object': [upload()],
        }
        for what, body in bad.items():
            with self.subTest(what), self.assertRaises(WebError) as refused:
                app_test_of(body)
            self.assertEqual((refused.exception.status, refused.exception.reason), (400, 'invalid_param'), what)
        self.assertEqual(self.app.radar_tests.tests(), [])
        good = {
            'no route': upload(comma__route=None), 'no commit': upload(comma__git_commit=None),
            'commit 7': upload(comma__git_commit='1b6a472'), 'commit 40': upload(comma__git_commit='1b' * 20),
            'note of 500 with a new line': upload(note='Garage, \n' + 'x' * 487 + ' 🚗'),
            'no speed': upload(capture__speed_max_mps=None), 'whole numbers': upload(capture__duration_s=20),
            'car farther': upload(target='car', tape_radar_m=1.5, tape_comma_m=3.0),
        }
        for what, body in good.items():
            with self.subTest(what):
                self.assertEqual(app_test_of(body)['client_id'], CLIENT_ID)
        self.assertIsNone(app_test_of(upload(comma__route=None))['route'])


# -- the phone key on the comma ----------------------------------------------------------------------------
class CommaKey(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.ssh = FakeSsh()
        self.app.comma_runner = self.ssh
        self.key, self.fingerprint = phone_key()

    def call(self, payload, status, **kwargs):
        response, body = self.post('/api/app/v1/comma-key', payload, **kwargs)
        self.assertEqual(response.status, status, body)
        return json.loads(body)

    def allow(self, allowed=True, password=PASSWORD):
        response, body = self.post('/api/settings/app', {'comma_keys': True, 'password': password} if allowed
                                   else {'comma_keys': False})
        self.assertEqual(response.status, 200, body)
        return json.loads(body)

    def app_settings(self):
        return json.loads(self.request('GET', '/api/settings')[1])['app']

    def permissions(self):
        return self.app.store.rows('SELECT allowed, until, why FROM app_permissions ORDER BY seq')

    def test_off_by_default(self):
        self.assertFalse(self.app_settings()['comma_keys'])
        self.assertEqual(self.call({'public_key': self.key}, 403), fixture('comma_key_not_allowed.json'))
        self.assertEqual(self.ssh.calls, [])
        # Turned off again after being on: refused again.
        self.allow()
        self.allow(False)
        self.assertEqual(self.call({'public_key': self.key}, 403)['error'], 'not_allowed')
        self.assertEqual(self.ssh.additions(), [])
        self.assertEqual(self.app.phone_keys.phones(), [])
        # The switch of an earlier version in settings.json is not read: a session alone never turns it on.
        (self.repo / 'outputs' / 'web' / 'settings.json').write_text(json.dumps({'app_comma_keys': True}))
        self.assertFalse(self.app_settings()['comma_keys'])
        self.assertEqual(self.call({'public_key': self.key}, 403)['error'], 'not_allowed')

    def test_on_only_with_the_password(self):
        # A session alone cannot turn it on.
        response, body = self.post('/api/settings/app', {'comma_keys': True})
        self.assertEqual((response.status, json.loads(body)['error']), (400, 'invalid_param'))
        response, body = self.post('/api/settings/app', {'comma_keys': True, 'password': 'not the password'})
        self.assertEqual((response.status, json.loads(body)['error']), (403, 'wrong_password'))
        self.assertFalse(self.app_settings()['comma_keys'])
        self.assertEqual(self.permissions(), [])
        before = time.time()
        answer = self.allow()
        self.assertEqual((answer['ok'], answer['app']['comma_keys'], answer['app']['minutes'],
                          answer['app']['phones']), (True, True, PERMISSION_SECONDS // 60, []))
        # The end is kept to a tenth of a second (rounded): up to 0.05 s past time.time() + PERMISSION_SECONDS.
        until = answer['app']['until']
        self.assertTrue(before + PERMISSION_SECONDS - 1 <= until <= time.time() + PERMISSION_SECONDS + 0.1)
        self.assertTrue(self.app_settings()['comma_keys'])
        for body in ({'comma_keys': 'yes', 'password': PASSWORD}, {'comma_keys': 1}, {}, {'password': PASSWORD},
                     {'comma_keys': True, 'password': PASSWORD, 'x': 1}, {'comma_keys': False, 'password': PASSWORD},
                     {'comma_keys': True, 'password': 42}):
            self.assertIn(self.post('/api/settings/app', body)[0].status, (400, 403), body)
        self.assertEqual(self.post('/api/settings/app', {'comma_keys': True, 'password': 42})[0].status, 403)
        self.assertEqual(self.post('/api/settings/app', {'comma_keys': False},
                                   {'Origin': 'http://evil.example'})[0].status, 403)
        self.assertEqual(self.post('/api/settings/app', {'comma_keys': False}, auth=False)[0].status, 401)
        self.assertEqual(self.request('GET', '/api/settings/app')[0].status, 405)
        self.assertTrue(self.app_settings()['comma_keys'])
        # Off at once, without the password; every change is in the store, an "off" of what is off is not one.
        self.assertFalse(self.allow(False)['app']['comma_keys'])
        self.allow(False)
        self.assertEqual([(row['allowed'], row['why']) for row in self.permissions()], [(1, 'owner'), (0, 'owner')])

    def test_wrong_passwords_lock_the_address(self):
        for _ in range(web.auth.FAILS_PER_ADDRESS):
            response, _ = self.post('/api/settings/app', {'comma_keys': True, 'password': 'not the password'})
            self.assertEqual(response.status, 403)
        response, body = self.post('/api/settings/app', {'comma_keys': True, 'password': PASSWORD})
        self.assertEqual((response.status, json.loads(body)['error']), (429, 'too_many_attempts'))
        self.assertFalse(self.app_settings()['comma_keys'])

    def test_one_phone_then_it_ends(self):
        self.allow()
        self.assertEqual(self.call({'public_key': self.key}, 200), fixture('comma_key_added.json'))
        # Used: the next phone needs the password again.
        other, _ = phone_key(9)
        self.assertEqual(self.call({'public_key': other}, 403)['error'], 'not_allowed')
        self.assertEqual(len(self.ssh.additions()), 1)
        self.assertFalse(self.app_settings()['comma_keys'])
        self.assertEqual([(row['allowed'], row['why']) for row in self.permissions()], [(1, 'owner'), (0, 'used')])
        # A key the comma already had ends it too.
        self.allow()
        self.ssh.add = (0, 'already\n')
        self.assertEqual(self.call({'public_key': other}, 200), {'ok': True, 'added': False, 'already': True})
        self.assertEqual(self.call({'public_key': other}, 403)['error'], 'not_allowed')
        # And time: it runs out by itself.
        self.allow()
        clock = time.time() + PERMISSION_SECONDS + 1
        with mock.patch.object(self.app.phone_keys, '_clock', lambda: clock):
            self.assertEqual(self.call({'public_key': other}, 403)['error'], 'not_allowed')
            self.assertEqual(self.app_settings()['comma_keys'], False)
        self.assertEqual(len(self.ssh.additions()), 2)

    def test_one_phone_however_many_ask_at_once(self):
        self.allow()
        keys = [phone_key(seed)[0] for seed in range(6)]
        barrier = threading.Barrier(len(keys))
        statuses = []

        def send(key):
            barrier.wait()
            statuses.append(self.post('/api/app/v1/comma-key', {'public_key': key})[0].status)

        threads = [threading.Thread(target=send, args=(key,)) for key in keys]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(sorted(statuses), [200] + [403] * 5)
        self.assertEqual(len(self.ssh.additions()), 1)

    def test_added_then_already(self):
        self.allow()
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(self.call({'public_key': self.key}, 200), fixture('comma_key_added.json'))
        lines = printed.getvalue().splitlines()
        self.assertEqual([line for line in lines if line.startswith('Comma: ')],
                         ['Comma: adding a phone key from the app (' + self.fingerprint + ')',
                          'Comma: the phone key was added (' + self.fingerprint + '); another phone needs the '
                          'permission again'])
        (argv, options), = self.ssh.additions()
        self.assertEqual(argv, ['ssh', *SSH_OPTIONS, self.app.comma().host, ADD_KEY_SCRIPT])
        self.assertEqual(options['input'], self.key + '\n')
        self.assertIn('timeout', options)
        # The key goes on the standard input only, never into a command.
        blob = self.key.split()[1]
        for call_argv, _ in self.ssh.calls:
            self.assertFalse(any(blob in part or 'Pixel-8' in part for part in call_argv), call_argv)
        self.assertIn(COMMA_SSH_KEYS, ADD_KEY_SCRIPT)
        phones = self.app_settings()['phones']
        self.assertEqual([(p['fingerprint'], p['comment'], p['host'], p['state']) for p in phones],
                         [(self.fingerprint, 'perception-app@Pixel-8', self.app.comma().host, 'added')])
        self.allow()
        self.ssh.add = (0, 'already\n')
        self.assertEqual(self.call({'public_key': self.key}, 200), {'ok': True, 'added': False, 'already': True})
        self.assertEqual([phone['state'] for phone in self.app.phone_keys.phones()], ['added'])
        # A key without a comment.
        self.allow()
        self.ssh.add = (0, 'added\n')
        other, other_print = phone_key(9, comment=None)
        self.assertTrue(self.call({'public_key': other}, 200)['added'])
        self.assertEqual([(p['fingerprint'], p['comment']) for p in self.app.phone_keys.phones()],
                         [(other_print, None), (self.fingerprint, 'perception-app@Pixel-8')])
        # A key the comma had before the site sent it: listed as such.
        self.allow()
        self.ssh.add = (0, 'already\n')
        third, third_print = phone_key(17)
        self.assertTrue(self.call({'public_key': third}, 200)['already'])
        self.assertEqual(self.app.phone_keys.phones()[0]['fingerprint'], third_print)
        self.assertEqual(self.app.phone_keys.phones()[0]['state'], 'already')

    def test_every_key_is_listed(self):
        for seed in range(25):
            self.app.phone_keys.record(phone_key(seed)[1], 'phone-' + str(seed), 'comma', 'sending')
            self.app.phone_keys.record(phone_key(seed)[1], 'phone-' + str(seed), 'comma', 'added')
        phones = self.app_settings()['phones']
        self.assertEqual(len(phones), 25)
        self.assertEqual(phones[0]['comment'], 'phone-24')

    def test_the_comma_does_not_answer(self):
        self.allow()
        for failure in ((255, ''), subprocess.TimeoutExpired('ssh', 30), OSError('no ssh')):
            self.ssh.add = failure
            self.assertEqual(self.call({'public_key': self.key}, 503), fixture('comma_key_unreachable.json'))
        # Perhaps written all the same (a time-out after the rename): shown as sent, not confirmed.
        self.assertEqual([phone['state'] for phone in self.app.phone_keys.phones()], ['unconfirmed'])
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            for failure in ((1, ''), (4, ''), (0, 'something else\n')):
                self.ssh.add = failure
                self.assertEqual(self.call({'public_key': self.key}, 502)['error'], 'comma_refused')
        self.assertIn('Comma: the phone key was not added (' + self.fingerprint + ', comma_refused)',
                      printed.getvalue())
        self.assertEqual([phone['state'] for phone in self.app.phone_keys.phones()], ['refused'])
        outcomes = self.app.store.rows('SELECT outcome, detail FROM comma_keys ORDER BY seq')
        self.assertEqual(outcomes, [{'outcome': 'sending', 'detail': None},
                                    {'outcome': 'failed', 'detail': 'comma_unreachable'}] * 3
                         + [{'outcome': 'sending', 'detail': None},
                            {'outcome': 'failed', 'detail': 'comma_refused'}] * 3)
        # A failure does not use the permission: the app can try again.
        self.assertTrue(self.app_settings()['comma_keys'])

    def test_no_key_reaches_the_comma_without_a_record(self):
        self.allow()
        busy = WebError(503, 'busy', 'The database of this site is busy: try again.')
        record = self.app.phone_keys.record
        # The first row cannot be written: nothing goes to the comma.
        with mock.patch.object(self.app.phone_keys, 'record', side_effect=busy):
            self.assertEqual(self.call({'public_key': self.key}, 503)['error'], 'busy')
        self.assertEqual(self.ssh.additions(), [])

        # The comma's answer cannot be written: the key is on the comma, the app hears it, and Settings shows it.
        def answer_lost(fingerprint, comment, host, outcome, detail=None, used=False):
            if outcome != 'sending':
                raise busy
            record(fingerprint, comment, host, outcome, detail, used)

        printed = io.StringIO()
        with mock.patch.object(self.app.phone_keys, 'record', side_effect=answer_lost), \
                contextlib.redirect_stdout(printed):
            self.assertEqual(self.call({'public_key': self.key}, 200), fixture('comma_key_added.json'))
        self.assertIn('could not be recorded (busy)', printed.getvalue())
        self.assertEqual(len(self.ssh.additions()), 1)
        self.assertEqual([(p['fingerprint'], p['state']) for p in self.app_settings()['phones']],
                         [(self.fingerprint, 'unconfirmed')])
        # Sent again, the comma has it: recorded now.
        self.ssh.add = (0, 'already\n')
        self.assertTrue(self.call({'public_key': self.key}, 200)['already'])
        self.assertEqual([(p['fingerprint'], p['state']) for p in self.app_settings()['phones']],
                         [(self.fingerprint, 'already')])

    def test_anything_but_one_key_is_refused(self):
        self.allow()
        blob = self.key.split()[1]
        short = base64.b64encode(ED25519_PREFIX + bytes(31)).decode()
        other_type = base64.b64encode(b'\x00\x00\x00\x0bssh-ed25518\x00\x00\x00\x20' + bytes(32)).decode()
        for value in ('ssh-rsa ' + blob + ' phone', 'ssh-ed25519  ' + blob, 'ssh-ed25519 ' + blob + ' two words',
                      self.key + '\n' + self.key, self.key + '\n', ' ' + self.key, 'ssh-ed25519 ' + blob + ' ',
                      'command="reboot" ' + self.key, 'ssh-ed25519 ' + blob + ' ' + 'x' * 81,
                      'ssh-ed25519 ' + blob + ' a;reboot', 'ssh-ed25519 ' + blob + " $(reboot)",
                      'ssh-ed25519 ' + short + ' phone', 'ssh-ed25519 ' + other_type + ' phone',
                      'ssh-ed25519 ' + blob[:-1] + '=', 'ssh-ed25519 ' + blob + blob, '', 42, None, [self.key]):
            with self.subTest(value):
                self.assertEqual(self.call({'public_key': value}, 400)['error'], 'invalid_param')
        for body in ({}, {'public_key': self.key, 'name': 'phone'}, {'key': self.key}):
            self.assertEqual(self.call(body, 400)['error'], 'invalid_param')
        self.assertEqual(self.ssh.calls, [])
        self.assertEqual(self.call({'public_key': self.key}, 403, headers={'Origin': 'http://evil.example'})['error'],
                         'cross_origin')
        self.assertEqual(self.request('GET', '/api/app/v1/comma-key')[0].status, 405)

    def test_the_line_and_its_fingerprint(self):
        self.assertEqual(public_key_line(self.key), (self.key, self.fingerprint, 'perception-app@Pixel-8'))
        # As ssh-keygen -l prints it, and the app's own key of SiteApiTest.
        app_key = ('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea '
                   'perception-app@Pixel-8')
        self.assertTrue(FINGERPRINT_RE.fullmatch(public_key_line(app_key)[1]))
        if shutil.which('ssh-keygen'):
            with tempfile.NamedTemporaryFile('w', suffix='.pub') as stream:
                stream.write(self.key + '\n')
                stream.flush()
                printed = subprocess.run(['ssh-keygen', '-l', '-f', stream.name], capture_output=True, text=True)
            self.assertIn(self.fingerprint, printed.stdout)


class KeyScript(unittest.TestCase):
    """ADD_KEY_SCRIPT run here by bash and sh on a list in a temporary folder (COMMA_SSH_KEYS replaced): never on a
    comma."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.list = Path(self.directory.name) / 'd' / 'GithubSshKeys'
        self.list.parent.mkdir()
        self.script = ADD_KEY_SCRIPT.replace(COMMA_SSH_KEYS, str(self.list))
        self.assertNotEqual(self.script, ADD_KEY_SCRIPT)

    def run_script(self, shell, line):
        return subprocess.run([shell, '-c', self.script], input=line + '\n', capture_output=True, text=True,
                              timeout=20)

    def test_appends_one_line(self):
        key, _ = phone_key()
        for shell in ('bash', 'sh'):
            if not shutil.which(shell):
                continue
            with self.subTest(shell):
                self.list.write_text('ssh-rsa AAAAB3NzaC1yc2E github-key\nssh-ed25519 AAAAother no-newline')
                self.list.chmod(0o640)
                result = self.run_script(shell, key)
                self.assertEqual((result.returncode, result.stdout), (0, 'added\n'), result.stderr)
                self.assertEqual(self.list.read_text(), 'ssh-rsa AAAAB3NzaC1yc2E github-key\n'
                                 'ssh-ed25519 AAAAother no-newline\n' + key + '\n')
                self.assertEqual(self.list.stat().st_mode & 0o777, 0o600)
                self.assertEqual(os.listdir(self.list.parent), ['GithubSshKeys'])
                # The same key with another comment: already there, nothing written.
                before = self.list.stat().st_mtime_ns
                again = self.run_script(shell, key.replace('Pixel-8', 'Other'))
                self.assertEqual((again.returncode, again.stdout), (0, 'already\n'))
                self.assertEqual(self.list.stat().st_mtime_ns, before)
                # No list yet: a new one with the line.
                self.list.unlink()
                self.assertEqual(self.run_script(shell, key).stdout, 'added\n')
                self.assertEqual((self.list.read_text(), self.list.stat().st_mode & 0o777), (key + '\n', 0o600))
                self.list.unlink()

    def test_refuses_what_is_not_a_key_or_a_plain_list(self):
        key, _ = phone_key()
        self.assertEqual(self.run_script('bash', 'touch /tmp/never').returncode, 3)
        target = Path(self.directory.name) / 'elsewhere'
        target.write_text('x\n')
        os.symlink(target, self.list)
        self.assertEqual(self.run_script('bash', key).returncode, 4)
        self.assertEqual(target.read_text(), 'x\n')


class Fingerprints(unittest.TestCase):
    """GET /api/app/v1 comma.host_key_fingerprints: this server's known_hosts for the comma's host, ed25519 only."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.known = Path(self.directory.name) / 'known_hosts'
        self.known.write_text('made up\n')
        self.ssh = FakeSsh()

    def test_known_hosts_lines(self):
        ed, ed_print = known_hosts_line(1)
        other, other_print = known_hosts_line(2)
        rsa, _ = known_hosts_line(3, 'ssh-rsa')
        lines = '\n'.join(['# Host 192.168.200.208 found: line 3', ed, rsa,
                           '@cert-authority * ' + other.split(' ', 1)[1], '@revoked ' + other, ed,
                           '192.168.200.208 ssh-ed25519 not-base64', other, ''])
        self.assertEqual(known_host_fingerprints(lines), [ed_print, other_print])

    def test_the_host_of_web_comma(self):
        ed, ed_print = known_hosts_line(1)
        self.ssh.config = (0, 'user comma\nhostname 192.168.200.208\nport 22\nuserknownhostsfile ' + str(self.known)
                           + ' ' + str(self.known) + '2\nglobalknownhostsfile /nonexistent/ssh_known_hosts\n')
        self.ssh.keygen = (0, '# Host 192.168.200.208 found: line 1\n' + ed + '\n')
        self.assertEqual(Comma('comma', runner=self.ssh).host_key_fingerprints(), [ed_print])
        self.assertEqual([call[0] for call in self.ssh.calls],
                         [['ssh', '-G', 'comma'], ['ssh-keygen', '-F', '192.168.200.208', '-f', str(self.known)]])
        # Another port, a HostKeyAlias.
        self.ssh.calls.clear()
        self.ssh.config = (0, 'hostname 10.0.0.5\nport 2222\nhostkeyalias mycomma\nuserknownhostsfile '
                           + str(self.known) + '\n')
        Comma('comma', runner=self.ssh).host_key_fingerprints()
        self.assertEqual(self.ssh.calls[-1][0][:3], ['ssh-keygen', '-F', '[mycomma]:2222'])
        # No ssh on this server, or nothing known.
        self.ssh.config = OSError('no ssh')
        self.assertEqual(Comma('comma', runner=self.ssh).host_key_fingerprints(), [])
        self.ssh.config = (0, 'hostname 192.168.200.208\nport 22\nuserknownhostsfile ' + str(self.known) + '\n')
        self.ssh.keygen = (1, '')
        self.assertEqual(Comma('comma', runner=self.ssh).host_key_fingerprints(), [])


# -- downloads ---------------------------------------------------------------------------------------------
class AndroidDownload(ServerTestCase):
    """The app built on this server by helpers/build_android.sh, offered in Settings and at /download/android."""

    def build(self, info, name='perception-android-0.1.19.apk', content=b'PK apk'):
        folder = self.repo / 'outputs' / 'android'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        (folder / 'latest.json').write_text(json.dumps(info))
        return folder

    def test_no_build(self):
        self.assertIsNone(json.loads(self.request('GET', '/api/settings')[1])['android'])
        for path in ('/download/android', '/download/android/aab'):
            self.assertEqual(self.request('GET', path)[0].status, 404, path)

    def test_the_newest_build(self):
        self.build({'version': '0.1.19', 'version_code': 1019, 'file': 'perception-android-0.1.19.apk', 'size': 6,
                    'sha256': 'f' * 64, 'built': 1.79e9, 'commit': '85c756f'})
        android = json.loads(self.request('GET', '/api/settings')[1])['android']
        self.assertEqual(android, {'file': 'perception-android-0.1.19.apk', 'size': 6, 'version': '0.1.19',
                                   'version_code': 1019, 'commit': '85c756f', 'sha256': 'f' * 64, 'built': 1.79e9,
                                   'bundle': None})
        response, body = self.request('GET', '/download/android')
        self.assertEqual((response.status, body), (200, b'PK apk'))
        self.assertEqual(response.getheader('Content-Type'), 'application/vnd.android.package-archive')
        self.assertEqual(response.getheader('Content-Disposition'),
                         'attachment; filename="perception-android-0.1.19.apk"')
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        response, body = self.request('HEAD', '/download/android')
        self.assertEqual((response.status, body), (200, b''))
        self.assertEqual(self.request('GET', '/download/android', auth=False)[0].status, 401)
        self.assertEqual(self.request('GET', '/download/android/aab')[0].status, 404)

    def test_the_bundle_for_google_play(self):
        folder = self.build({'file': 'perception-android-0.1.19.apk', 'aab': 'perception-android-0.1.19.aab',
                             'aab_size': 6, 'aab_sha256': 'e' * 64})
        (folder / 'perception-android-0.1.19.aab').write_bytes(b'PK aab')
        self.assertEqual(json.loads(self.request('GET', '/api/settings')[1])['android']['bundle'],
                         {'file': 'perception-android-0.1.19.aab', 'size': 6})
        response, body = self.request('GET', '/download/android/aab')
        self.assertEqual((response.status, body), (200, b'PK aab'))
        self.assertEqual(response.getheader('Content-Disposition'),
                         'attachment; filename="perception-android-0.1.19.aab"')
        self.assertEqual(self.request('GET', '/download/android/aab', auth=False)[0].status, 401)
        # Never a -dev bundle, a link or a file outside outputs/android.
        secret = self.repo / 'secret.aab'
        secret.write_bytes(b'secret')
        os.symlink(secret, folder / 'perception-android-0.1.20.aab')
        (folder / 'perception-android-0.1.21-dev.aab').write_bytes(b'dev')
        for name in ('perception-android-0.1.20.aab', 'perception-android-0.1.21-dev.aab', '../secret.aab'):
            (folder / 'latest.json').write_text(json.dumps({'file': 'perception-android-0.1.19.apk', 'aab': name}))
            response, body = self.request('GET', '/download/android/aab')
            self.assertEqual(response.status, 404, name)
            self.assertNotEqual(body, b'secret')

    def test_only_a_plain_apk_inside_outputs_android(self):
        secret = self.repo / 'secret.apk'
        secret.write_bytes(b'secret')
        folder = self.build({'file': 'perception-android-0.1.1.apk'}, name='other.apk')
        os.symlink(secret, folder / 'perception-android-0.1.1.apk')
        for name in ('perception-android-0.1.1.apk', '../secret.apk', 'other.apk', 'distill-android-0.3.1.apk',
                     'perception-android-0.1.1.apk/../x'):
            (folder / 'latest.json').write_text(json.dumps({'file': name, 'version': '1'}))
            response, body = self.request('GET', '/download/android')
            self.assertEqual(response.status, 404, name)
            self.assertNotEqual(body, b'secret')
        (folder / 'latest.json').write_text('not json')
        self.assertIsNone(android_build(self.repo))
        self.assertIsNone(json.loads(self.request('GET', '/api/settings')[1])['android'])

    def test_wrong_field_types_are_dropped(self):
        self.build({'file': 'perception-android-0.1.19.apk', 'version': 3, 'version_code': True, 'built': 'today'})
        android = json.loads(self.request('GET', '/api/settings')[1])['android']
        self.assertEqual((android['version'], android['version_code'], android['built']), (None, None, None))

    def test_the_settings_card(self):
        response, page = self.request('GET', '/')
        for marker in (b'id="android-card"', b'href="/download/android"', b'href="/download/android/aab"',
                       b'https://play.google.com/apps/internaltest/4700859580640807099', b'id="app-comma-keys"',
                       b'Let the app add phones to the comma', b'id="app-keys-form"', b'id="app-keys-password"',
                       b'cannot update each other', b'deletes the tests still on the phone'):
            self.assertIn(marker, page)


# -- the contract with the app -----------------------------------------------------------------------------
class AppContract(ServerTestCase):
    """The app's requests (SiteApi.kt, Http.kt: no Origin, Accept and Content-Type application/json, its own
    cookie) against the real handlers, every answer checked against the app's fixtures of the contract
    (app/android/app/src/test/resources/fixtures)."""

    def setUp(self):
        super().setUp()
        self.ssh = FakeSsh()
        self.app.comma_runner = self.ssh
        known = self.repo / 'known_hosts'
        known.write_text('made up\n')
        self.host_line, self.host_print = known_hosts_line(5)
        self.ssh.config = (0, 'hostname 192.168.200.208\nport 22\nuserknownhostsfile ' + str(known) + '\n')
        self.ssh.keygen = (0, self.host_line + '\n')

    def app_call(self, method, path, payload=None, cookie=None, origin=None):
        headers = {'Accept': 'application/json'}
        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload).encode('utf-8')
        if cookie is not None:
            headers['Cookie'] = cookie
        if origin is not None:
            headers['Origin'] = origin
        response, raw = self.request(method, path, body, headers, auth=False)
        self.assertTrue(response.getheader('Content-Type').startswith('application/json'), path)
        return response.status, json.loads(raw)

    def assertShape(self, expected, actual, where='answer'):
        """The same keys at every level; a value of the fixture's type (null allowed where the app reads a null);
        lists of objects entry by entry against the fixture's first entry."""
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, where)
            self.assertEqual(set(actual), set(expected), where)
            for key in expected:
                self.assertShape(expected[key], actual[key], where + '.' + key)
        elif isinstance(expected, list):
            self.assertIsInstance(actual, list, where)
            if expected and isinstance(expected[0], dict):
                for index, item in enumerate(actual):
                    self.assertShape(expected[0], item, where + '[' + str(index) + ']')
            elif expected:
                for item in actual:
                    self.assertShape(expected[0], item, where + '[]')
        elif expected is None or actual is None:
            return
        elif isinstance(expected, bool):
            self.assertIsInstance(actual, bool, where)
        elif isinstance(expected, (int, float)):
            self.assertTrue(isinstance(actual, (int, float)) and not isinstance(actual, bool), where)
        else:
            self.assertIsInstance(actual, type(expected), where)

    def test_the_app_from_sign_in_to_sign_out(self):
        # Before signing in: the state, and the API asks for it.
        status, state = self.app_call('GET', '/api/auth/state')
        self.assertShape(fixture('auth_state.json'), {key: state[key] for key in ('configured', 'signed_in')})
        self.assertEqual((status, state['configured'], state['signed_in'], state['api']), (200, True, False,
                                                                                            API_VERSION))
        for path in ('/api/app/v1', '/api/app/v1/tests'):
            self.assertEqual(self.app_call('GET', path), (401, fixture('error_login_required.json')), path)
        self.assertEqual(self.app_call('POST', '/api/app/v1/tests', fixture('test_upload_body.json')),
                         (401, fixture('error_login_required.json')))
        status, answer = self.app_call('POST', '/api/auth/login', {'user': ADMIN, 'password': PASSWORD})
        self.assertEqual((status, answer), (200, {'ok': True}))
        cookie = self.last_cookie
        status, state = self.app_call('GET', '/api/auth/state', cookie=cookie)
        self.assertEqual((state['signed_in'], state['api']), (True, API_VERSION))

        # GET /api/app/v1 before any calibration: app_v1_not_set.json.
        status, info = self.app_call('GET', '/api/app/v1', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertShape(fixture('app_v1_not_set.json'), info)
        expected = fixture('app_v1.json')
        self.assertEqual((info['api'], info['version'], info['comma']['python'], info['comma']['openpilot'],
                          info['label_range_m']),
                         (expected['api'], expected['version'], expected['comma']['python'],
                          expected['comma']['openpilot'], expected['label_range_m']))
        self.assertEqual((info['calibration']['status'], info['calibration']['version']), ('not_set', 0))
        self.assertEqual(info['comma']['host_key_fingerprints'], [self.host_print])
        self.assertTrue(all(FINGERPRINT_RE.fullmatch(item) for item in info['comma']['host_key_fingerprints']))

        # A test from a recording on the site, then the app's test: test_upload_body.json as the app sends it.
        write_recording(self.repo, '00000120--1f2e3d4c5b--0', 1.75)
        self.assertEqual(self.post('/api/radar/tests', {'segment': '00000120--1f2e3d4c5b--0', 'standstill': 0,
                                                        'tape_radar_m': 0.9, 'tape_comma_m': 2.4})[0].status, 200)
        status, reply = self.app_call('POST', '/api/app/v1/tests', fixture('test_upload_body.json'), cookie=cookie)
        self.assertEqual(status, 200, reply)
        expected_reply = fixture('test_post_reply.json')
        self.assertShape(expected_reply, reply)
        for key in ('client_id', 'target', 'route', 'reading_m', 'tape_radar_m', 'tape_comma_m', 'correction_m',
                    'kept'):
            self.assertEqual(reply['test'][key], expected_reply['test'][key], key)
        self.assertEqual((reply['calibration']['source'], reply['calibration']['status'],
                          reply['calibration']['tested_m'], reply['calibration']['radar_correction_m']),
                         ('tests', 'close_only', [0.9, 1.32], -0.89))
        # Sent again (the reply was lost): the same test.
        self.assertEqual(self.app_call('POST', '/api/app/v1/tests', fixture('test_upload_body.json'), cookie=cookie),
                         (200, reply))
        status, info = self.app_call('GET', '/api/app/v1', cookie=cookie)
        self.assertShape(fixture('app_v1.json'), info)
        self.assertEqual(info['calibration'], reply['calibration'])

        # GET /api/app/v1/tests: tests_list.json, the recording's without client_id, the app's with it.
        status, listed = self.app_call('GET', '/api/app/v1/tests', cookie=cookie)
        listing = fixture('tests_list.json')
        self.assertEqual(set(listed), set(listing))
        recording, mine = listed['tests']
        self.assertShape(listing['tests'][0], recording, 'recording test')
        self.assertShape(listing['tests'][1], mine, 'app test')
        self.assertEqual((mine['id'], mine['client_id']), (reply['test']['id'], CLIENT_ID))
        self.assertEqual(recording['route'], '00000120--1f2e3d4c5b')

        # Refusals, as the fixtures have them.
        self.assertEqual(self.app_call('POST', '/api/app/v1/tests',
                                       upload(client_id='00000000-0000-4000-8000-00000000000a', tape_comma_m=1.0),
                                       cookie=cookie), (400, fixture('error_invalid_param.json')))
        status, answer = self.app_call('POST', '/api/app/v1/tests', fixture('test_upload_body.json'), cookie=cookie,
                                       origin='http://evil.example')
        self.assertEqual((status, answer['error']), (403, 'cross_origin'))
        response, raw = self.request('POST', '/api/app/v1/tests', b'client_id=x',
                                     {'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': cookie},
                                     auth=False)
        self.assertEqual(response.status, 415)

        # The phone key: not allowed, then allowed and added, then the comma away.
        key, _ = phone_key()
        self.assertEqual(self.app_call('POST', '/api/app/v1/comma-key', {'public_key': key}, cookie=cookie),
                         (403, fixture('comma_key_not_allowed.json')))
        # The owner allows one phone in Settings, with his password.
        self.assertEqual(self.post('/api/settings/app', {'comma_keys': True, 'password': PASSWORD})[0].status, 200)
        self.assertEqual(self.app_call('POST', '/api/app/v1/comma-key', {'public_key': key}, cookie=cookie),
                         (200, fixture('comma_key_added.json')))
        self.assertEqual(self.app_call('POST', '/api/app/v1/comma-key', {'public_key': key}, cookie=cookie),
                         (403, fixture('comma_key_not_allowed.json')))
        self.assertEqual(self.post('/api/settings/app', {'comma_keys': True, 'password': PASSWORD})[0].status, 200)
        self.ssh.add = (255, '')
        self.assertEqual(self.app_call('POST', '/api/app/v1/comma-key', {'public_key': key}, cookie=cookie),
                         (503, fixture('comma_key_unreachable.json')))

        # Sign out: the session is gone.
        self.assertEqual(self.app_call('POST', '/api/auth/logout', {}, cookie=cookie), (200, {'ok': True}))
        self.assertEqual(self.app_call('GET', '/api/app/v1', cookie=cookie),
                         (401, fixture('error_login_required.json')))

    def test_every_fixture_is_checked(self):
        """A new fixture of the app gets a check here."""
        checked = {'app_v1.json', 'app_v1_not_set.json', 'auth_state.json', 'comma_key_added.json',
                   'comma_key_not_allowed.json', 'comma_key_unreachable.json', 'error_invalid_param.json',
                   'error_login_required.json', 'test_post_reply.json', 'test_upload_body.json', 'tests_list.json'}
        self.assertEqual({path.name for path in FIXTURES.glob('*.json')}, checked)

    @property
    def last_cookie(self):
        return self._cookie

    def request(self, method, path, body=None, headers=None, auth=True):
        response, data = super().request(method, path, body, headers, auth)
        cookie = response.getheader('Set-Cookie')
        if cookie and cookie.startswith('perception_web_') and 'Max-Age=0' not in cookie:
            self._cookie = cookie.split(';', 1)[0]
        return response, data


if __name__ == '__main__':
    unittest.main()
