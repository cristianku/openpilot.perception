"""The site's own records, in one SQLite database: outputs/web/perception.sqlite (600, WAL). It holds what the
site writes itself and must never lose: the radar calibration tests (from recordings and from the app), the tests
waiting for their recording, every version of the calibration, the phones the app added to the comma and every
change of the owner's permission for it.
settings.json keeps the settings themselves.

    store = Store.at(repo / 'outputs' / 'web')      # one per database file in a process
    rows = store.rows('SELECT ... WHERE id = ?', (test_id,))
    with store.write() as db:                       # one transaction
        db.execute('INSERT ...', values)

Writes are serialised: one lock per database in this process, and SQLite's own write lock (BEGIN IMMEDIATE, a busy
timeout of 10 s) against another process. Each is one transaction, rolled back whole on an error. The schema
version is in meta (schema_version); a database written by a newer site is never opened.

Schema 1 (SCHEMA below):
  meta                 key, value: schema_version
  radar_tests          one calibration test, in the order they were saved (seq): id (12 hex), source
                       "recording" (a standstill of a recording on this server: segment, from_s, to_s) or "app"
                       (captured live on a phone: client_id, made_unix, capture, git_commit, note; route is the
                       comma's recording during the capture, or null), target, reading_m, the two tapes,
                       correction_m, kept, created (when the site saved it)
  radar_drafts         tape-only tests waiting for their recording: id, created, target, tapes, marker_counter
  radar_calibrations   every version of the calibration, append-only: version, at, the two values, source,
                       test_ids (JSON list)
  comma_keys           every phone key the app added to the comma, append-only: at, fingerprint, comment, host
  imports              the JSON files of earlier versions imported here: file, sha256, at, entries, skipped
Schema 2 (SCHEMA_2):
  comma_keys           + outcome, one row per step of an addition: "sending" (written before the key goes to the
                       comma, so no key reaches it without a row), then "added", "already" (the comma had it) or
                       "failed" (+ detail, the error code: after comma_unreachable the comma may have it all the
                       same); the rows of schema 1 are "added"
  app_permissions      every change of "Let the app add phones to the comma", append-only: at, allowed (1 on,
                       0 off), until (when an "on" ends by itself), why ("owner" in Settings, "used" once a phone
                       key is on the comma); the last row is the permission in use

The JSON files of earlier versions next to the database (radar_tests.json, radar_drafts.json,
radar_calibrations.json) are imported the first time they are seen: their usable entries go in (one already here,
by id, is skipped), the file is renamed <name>.imported, and imports records it with its SHA-256, so a file whose
rename failed is never imported twice. A file that cannot be read or holds no list is left as it is and reported
(printed, and in Store.problems for the page): what it holds is refused for writing until it is fixed or removed,
and it is tried again at every use, as is a file that comes back (written by an older site still running).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from web.common import WebError

DB_NAME = 'perception.sqlite'
SCHEMA_VERSION = 2
BUSY_TIMEOUT_S = 10.0
SEGMENT_NAME_RE = re.compile(r'^[0-9a-f]{18,24}$')
RECORDING_NAME_RE = re.compile(r'^([0-9a-f]{8}--[0-9a-f]{10})--([0-9]{1,4})$')
TARGETS = ('wall', 'car')
SOURCES = ('recording', 'app')
CALIBRATION_SOURCES = ('tests', 'typed', 'none')
COUNTER_MAX = 0xffffffff
LEGACY_TESTS = 'radar_tests.json'
LEGACY_DRAFTS = 'radar_drafts.json'
LEGACY_CALIBRATIONS = 'radar_calibrations.json'

SCHEMA = (
    """CREATE TABLE radar_tests (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL CHECK (source IN ('recording', 'app')),
        segment TEXT,
        route TEXT,
        target TEXT NOT NULL CHECK (target IN ('wall', 'car')),
        from_s REAL,
        to_s REAL,
        reading_m REAL NOT NULL,
        tape_radar_m REAL NOT NULL,
        tape_comma_m REAL NOT NULL,
        correction_m REAL NOT NULL,
        kept INTEGER NOT NULL CHECK (kept IN (0, 1)),
        created REAL,
        client_id TEXT UNIQUE,
        made_unix REAL,
        capture TEXT,
        git_commit TEXT,
        note TEXT,
        CHECK (source = 'app' OR segment IS NOT NULL),
        CHECK (source = 'recording' OR client_id IS NOT NULL))""",
    """CREATE TABLE radar_drafts (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        created REAL,
        target TEXT NOT NULL CHECK (target IN ('wall', 'car')),
        tape_radar_m REAL NOT NULL,
        tape_comma_m REAL NOT NULL,
        marker_counter INTEGER)""",
    """CREATE TABLE radar_calibrations (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL,
        at REAL,
        radar_to_camera_m REAL,
        radar_correction_m REAL,
        source TEXT,
        test_ids TEXT NOT NULL DEFAULT '[]')""",
    'CREATE INDEX radar_calibrations_version ON radar_calibrations (version)',
    """CREATE TABLE comma_keys (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        fingerprint TEXT NOT NULL,
        comment TEXT,
        host TEXT NOT NULL)""",
    """CREATE TABLE imports (
        file TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        at REAL NOT NULL,
        entries INTEGER NOT NULL,
        skipped INTEGER NOT NULL,
        PRIMARY KEY (file, sha256))""",
    # The two logs are never changed, only added to.
    *(f"""CREATE TRIGGER {table}_append_only_{action} BEFORE {action.upper()} ON {table}
          BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
      for table in ('radar_calibrations', 'comma_keys') for action in ('update', 'delete')),
)
KEY_OUTCOMES = ('sending', 'added', 'already', 'failed')
PERMISSION_REASONS = ('owner', 'used')
SCHEMA_2 = (
    "ALTER TABLE comma_keys ADD COLUMN outcome TEXT NOT NULL DEFAULT 'added' "
    "CHECK (outcome IN ('sending', 'added', 'already', 'failed'))",
    'ALTER TABLE comma_keys ADD COLUMN detail TEXT',
    """CREATE TABLE app_permissions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
        until REAL,
        why TEXT NOT NULL CHECK (why IN ('owner', 'used')),
        CHECK (allowed = 0 OR until IS NOT NULL))""",
    *(f"""CREATE TRIGGER app_permissions_append_only_{action} BEFORE {action.upper()} ON app_permissions
          BEGIN SELECT RAISE(ABORT, 'app_permissions is append-only'); END""" for action in ('update', 'delete')),
)
# MIGRATIONS[n] takes a database from schema n to n + 1.
MIGRATIONS = (SCHEMA, SCHEMA_2)


def is_number(value) -> bool:
    """A real number: never a bool, NaN or an infinity (json.loads reads NaN from a file edited by hand, and the
    page's JSON can never carry it)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number_or_none(value):
    return value if is_number(value) else None


def route_of(name: str) -> str:
    """The drive (route) a segment belongs to: distill names are <route 18 hex><segment 2>, comma names
    <counter>--<tag>--<segment>."""
    match = RECORDING_NAME_RE.fullmatch(name)
    if match:
        return match.group(1)
    return name[:18]


# -- the JSON files of earlier versions -----------------------------------------------------------------
def legacy_test(entry) -> tuple | None:
    """A test of radar_tests.json as a row, or None when it lacks what the means need (as web/radar.py read it)."""
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not isinstance(entry.get('segment'), str):
        return None
    segment = entry['segment']
    if (SEGMENT_NAME_RE.fullmatch(segment) or RECORDING_NAME_RE.fullmatch(segment)) is None \
            or entry.get('route') not in (None, route_of(segment)) \
            or not all(is_number(entry.get(key)) for key in ('reading_m', 'tape_radar_m', 'tape_comma_m')):
        return None
    reading, radar, comma = (float(entry[key]) for key in ('reading_m', 'tape_radar_m', 'tape_comma_m'))
    # Saved before the parked-car test existed: in front of a wall.
    target = entry.get('target') if entry.get('target') in TARGETS else 'wall'
    return (entry['id'], 'recording', segment, route_of(segment), target, number_or_none(entry.get('from_s')),
            number_or_none(entry.get('to_s')), reading, radar, comma, round(radar - reading, 2),
            1 if entry.get('kept', True) is True else 0, number_or_none(entry.get('created')))


def legacy_draft(entry) -> tuple | None:
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or entry.get('target') not in TARGETS \
            or not all(is_number(entry.get(key)) for key in ('tape_radar_m', 'tape_comma_m')):
        return None
    marker = entry.get('marker_counter')
    marker = marker if isinstance(marker, int) and not isinstance(marker, bool) and 0 <= marker <= COUNTER_MAX \
        else None
    return (entry['id'], number_or_none(entry.get('created')), entry['target'], float(entry['tape_radar_m']),
            float(entry['tape_comma_m']), marker)


def legacy_calibration(entry) -> tuple | None:
    if not isinstance(entry, dict):
        return None
    version = entry.get('version')
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        return None
    ids = entry.get('test_ids')
    ids = [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []
    source = entry.get('source') if entry.get('source') in CALIBRATION_SOURCES else None
    return (version, number_or_none(entry.get('at')), number_or_none(entry.get('radar_to_camera_m')),
            number_or_none(entry.get('radar_correction_m')), source, json.dumps(ids))


def _import_tests(db: sqlite3.Connection, entries: list) -> tuple[int, int]:
    imported = skipped = 0
    for entry in entries:
        row = legacy_test(entry)
        # OR IGNORE: one already here by id is skipped (a file written again by an older site).
        if row is not None and db.execute(
                'INSERT OR IGNORE INTO radar_tests (id, source, segment, route, target, from_s, to_s, reading_m, '
                'tape_radar_m, tape_comma_m, correction_m, kept, created) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', row).rowcount:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


def _import_drafts(db: sqlite3.Connection, entries: list) -> tuple[int, int]:
    imported = skipped = 0
    for entry in entries:
        row = legacy_draft(entry)
        if row is not None and db.execute(
                'INSERT OR IGNORE INTO radar_drafts (id, created, target, tape_radar_m, tape_comma_m, marker_counter) '
                'VALUES (?, ?, ?, ?, ?, ?)', row).rowcount:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


def _import_calibrations(db: sqlite3.Connection, entries: list) -> tuple[int, int]:
    imported = skipped = 0
    for entry in entries:
        row = legacy_calibration(entry)
        # The same version at the same time is the same entry (a file written again by an older site).
        if row is None or db.execute('SELECT 1 FROM radar_calibrations WHERE version = ? AND at IS ?',
                                     (row[0], row[1])).fetchone():
            skipped += 1
            continue
        db.execute('INSERT INTO radar_calibrations (version, at, radar_to_camera_m, radar_correction_m, source, '
                   'test_ids) VALUES (?, ?, ?, ?, ?, ?)', row)
        imported += 1
    return imported, skipped


LEGACY = {
    LEGACY_TESTS: ('radar tests', _import_tests),
    LEGACY_DRAFTS: ('radar tests waiting for their recording', _import_drafts),
    LEGACY_CALIBRATIONS: ('history of the radar calibration', _import_calibrations),
}


class Store:
    _stores: dict[str, Store] = {}
    _stores_lock = threading.Lock()

    @classmethod
    def at(cls, folder: Path) -> Store:
        """The store of folder/perception.sqlite, opened (created, migrated, the old JSON files imported) the
        first time; the same object for every caller of this process afterwards."""
        key = os.path.abspath(folder / DB_NAME)
        with cls._stores_lock:
            store = cls._stores.get(key)
            if store is None or not store._same_file():
                store = cls(Path(key))
                cls._stores[key] = store
            return store

    def __init__(self, path: Path):
        self.path = path
        self.folder = path.parent
        self._lock = threading.RLock()
        # Old JSON files that could not be imported: file name -> the message for the page.
        self.problems: dict[str, str] = {}
        self._db = self._connect()
        self._identity = self._file_identity()
        try:
            self._migrate()
            self.import_legacy()
        except BaseException:
            self._db.close()
            raise

    # -- the database file ---------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise WebError(409, 'file_is_a_link', 'The database of this site (outputs/web/' + DB_NAME + ') is a '
                           'link: the site never writes through one.')
        try:
            self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Created 600 before SQLite opens it: its -wal and -shm files take the same mode.
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            os.chmod(self.path, 0o600)
            db = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S, isolation_level=None,
                                 check_same_thread=False)
        except (OSError, sqlite3.Error) as exc:
            raise WebError(500, 'cannot_open', 'Cannot open the database of this site (' + type(exc).__name__
                           + ').') from None
        db.row_factory = sqlite3.Row
        try:
            mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA trusted_schema=OFF')
        except sqlite3.Error as exc:
            db.close()
            raise WebError(500, 'cannot_open', 'Cannot open the database of this site (' + type(exc).__name__
                           + ').') from None
        if str(mode).lower() != 'wal':
            db.close()
            raise WebError(500, 'cannot_open', 'The database of this site cannot use its journal (WAL).')
        return db

    def _file_identity(self):
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        return info.st_dev, info.st_ino

    def _same_file(self) -> bool:
        """False once the file was deleted or replaced under this process (then it is opened again)."""
        return self._identity is not None and self._file_identity() == self._identity

    def _migrate(self) -> None:
        with self.write() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            try:
                version = int(row[0]) if row else 0
            except ValueError:
                version = SCHEMA_VERSION + 1
            if version > SCHEMA_VERSION:
                raise WebError(500, 'store_too_new', 'The database of this site (outputs/web/' + DB_NAME + ') was '
                               'written by a newer version of the site: update the code.')
            for step in range(version, SCHEMA_VERSION):
                for statement in MIGRATIONS[step]:
                    db.execute(statement)
            db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                       'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (str(SCHEMA_VERSION),))

    def schema_version(self) -> int:
        return int(self.rows("SELECT value FROM meta WHERE key = 'schema_version'")[0]['value'])

    # -- reads and writes ----------------------------------------------------------------------------
    def rows(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            try:
                return [dict(row) for row in self._db.execute(sql, params).fetchall()]
            except sqlite3.Error as exc:
                raise WebError(500, 'unreadable', 'Cannot read the database of this site (' + type(exc).__name__
                               + ').') from None

    @contextmanager
    def write(self):
        """One transaction (BEGIN IMMEDIATE: SQLite's write lock at once), committed at the end of the block and
        rolled back whole on any error. A write inside another one joins it."""
        with self._lock:
            if self._db.in_transaction:
                yield self._db
                return
            try:
                self._db.execute('BEGIN IMMEDIATE')
            except sqlite3.Error as exc:
                raise WebError(503, 'busy', 'The database of this site is busy (' + type(exc).__name__ + '): try '
                               'again.') from None
            try:
                yield self._db
            except BaseException as exc:
                self._db.execute('ROLLBACK')
                if isinstance(exc, sqlite3.Error):
                    raise WebError(500, 'cannot_save', 'Cannot save it on this server (database: '
                                   + type(exc).__name__ + ').') from None
                raise
            try:
                self._db.execute('COMMIT')
            except sqlite3.Error as exc:
                self._db.execute('ROLLBACK')
                raise WebError(500, 'cannot_save', 'Cannot save it on this server (database: ' + type(exc).__name__
                               + ').') from None

    # -- the JSON files of earlier versions ----------------------------------------------------------
    def import_legacy(self, name: str | None = None) -> None:
        """Imports every old JSON file next to the database (or only `name`) that is there."""
        for file_name in LEGACY if name is None else (name,):
            with self._lock:
                try:
                    message = self._import(file_name)
                except WebError as exc:
                    message = exc.message
                if message is None:
                    self.problems.pop(file_name, None)
                    continue
                if self.problems.get(file_name) != message:
                    print('Store: ' + message, flush=True)
                self.problems[file_name] = message

    def check(self, name: str) -> None:
        """Before a read of what the old file `name` holds: imported when it is (back) there, its problem gone
        when it was fixed or removed."""
        if name in self.problems or os.path.lexists(self.folder / name):
            self.import_legacy(name)

    def require(self, name: str) -> None:
        """Before a write of what the old file `name` holds: imported when it is there; refused while it cannot be
        read, so the new records never go ahead of what it holds."""
        self.check(name)
        message = self.problems.get(name)
        if message is not None:
            raise WebError(500, 'unreadable', message + ' Nothing was saved.')

    def _import(self, name: str) -> str | None:
        """None when `name` is imported or not there; otherwise why it was left as it is."""
        what, importer = LEGACY[name]
        path = self.folder / name
        if not os.path.lexists(path):
            return None
        damaged = ('The ' + what + ' of an earlier version of the site (outputs/web/' + name + ') cannot be read: '
                   'they were not imported and the file is kept as it is. Fix or remove it.')
        if path.is_symlink() or not path.is_file():
            return damaged
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8')
            # json.loads as the old site read these files (NaN included: such an entry is skipped below).
            entries = json.loads(text) if text.strip() else []
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return damaged
        if not isinstance(entries, list):
            return damaged
        digest = hashlib.sha256(raw).hexdigest()
        with self.write() as db:
            done = db.execute('SELECT 1 FROM imports WHERE file = ? AND sha256 = ?', (name, digest)).fetchone()
            if done is None:
                imported, skipped = importer(db, entries)
                db.execute('INSERT INTO imports (file, sha256, at, entries, skipped) VALUES (?, ?, ?, ?, ?)',
                           (name, digest, round(time.time(), 1), imported, skipped))
                print('Store: ' + name + ' imported (' + str(imported) + ' entries'
                      + (', ' + str(skipped) + ' skipped: kept in the renamed file' if skipped else '') + ')',
                      flush=True)
        target = self._free_name(path)
        try:
            os.rename(path, target)
        except FileNotFoundError:
            # Another process imported and renamed it at the same moment.
            pass
        except OSError as exc:
            print('Store: ' + name + ' is imported but could not be renamed (' + type(exc).__name__ + '): it is '
                  'never imported twice.', flush=True)
        return None

    @staticmethod
    def _free_name(path: Path) -> Path:
        target = path.with_name(path.name + '.imported')
        number = 1
        while os.path.lexists(target):
            target = path.with_name(path.name + '.imported.' + str(number))
            number += 1
        return target
