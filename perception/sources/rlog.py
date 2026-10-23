"""Reading comma logs without openpilot: the Cap'n Proto schema of the software that recorded them, at pinned
commits, and the events of an rlog or qlog (.zst, .bz2 or plain).

The schema is the one of the software on the author's comma that logs the car's radar in carState:
cristianku/openpilot (psa-torque-sunny-testing) at SCHEMA_COMMIT for cereal, with cristianku/opendbc at
CAR_COMMIT for car.capnp (the stockRadar* fields, perception.radar.oem_lead). The five files are downloaded
from GitHub on first use, checked against their SHA-256 and kept in ~/.cache/openpilot-perception (or
OPENPILOT_PERCEPTION_CACHE); nothing of them is copied into this repository. Logs of other versions read with it
too: Cap'n Proto keeps unknown fields out and gives defaults for missing ones, so a drive recorded before the
radar fields existed simply never has a valid target.

pycapnp keeps one schema per file id in a process: load_schema() loads it once and every reader shares it.
"""
from __future__ import annotations

import bz2
import hashlib
import io
import os
import threading
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path

SCHEMA_COMMIT = '1b6a472ae61d2d61866902cb48a5b3ab8bd11d07'
CAR_COMMIT = '6810f033b9e60738365cfecb8504c980dfaad137'
_RAW = 'https://raw.githubusercontent.com'
# (file in the cache, URL, SHA-256). The paths keep the layout the imports expect: log.capnp imports
# "/car.capnp" and "/include/c++.capnp" from the import root, the cache folder.
SCHEMA_FILES = (
    ('log.capnp', f'{_RAW}/cristianku/openpilot/{SCHEMA_COMMIT}/openpilot/cereal/log.capnp',
     'be3b81471c18ad7ffbac3890042cd93823c69832fd88130ac9400bd02f8b049b'),
    ('custom.capnp', f'{_RAW}/cristianku/openpilot/{SCHEMA_COMMIT}/openpilot/cereal/custom.capnp',
     '439f46f148c6e72571bdc232e29b42ab6bb361c304cff9eaba0afeab66885f19'),
    ('deprecated.capnp', f'{_RAW}/cristianku/openpilot/{SCHEMA_COMMIT}/openpilot/cereal/deprecated.capnp',
     'b3aa5c13de40001ed7376cef858500fb003f7698f96131cd0db18d878fa1af0d'),
    ('include/c++.capnp', f'{_RAW}/cristianku/openpilot/{SCHEMA_COMMIT}/openpilot/cereal/include/c++.capnp',
     'fb306076cd38c27af1aed20fac6395e9a46fbe5b5df6a248b5f1b6845a079c44'),
    ('car.capnp', f'{_RAW}/cristianku/opendbc/{CAR_COMMIT}/opendbc/car/car.capnp',
     'b4a6502704b0315b8277fb7fe125c9780ec3aae47d4ba3dfa9b3d665160b545a'),
)

_lock = threading.Lock()
_schema = None


class SchemaError(RuntimeError):
    """The pinned schema cannot be had (no network on first use, or a file that does not match)."""


def schema_dir() -> Path:
    base = Path(os.environ.get('OPENPILOT_PERCEPTION_CACHE', Path.home() / '.cache' / 'openpilot-perception'))
    return base / 'schema' / f'{SCHEMA_COMMIT[:12]}-{CAR_COMMIT[:12]}'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_schema(directory: Path | None = None, fetch=None) -> Path:
    """The folder with the five schema files, downloaded and checked when missing. `fetch(url) -> bytes` is
    for tests."""
    directory = directory or schema_dir()
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=60).read())
    for name, url, sha in SCHEMA_FILES:
        path = directory / name
        if path.is_file() and _sha256(path.read_bytes()) == sha:
            continue
        try:
            data = fetch(url)
        except OSError as exc:
            raise SchemaError(f'cannot download the log schema ({name}): {exc}') from exc
        if _sha256(data) != sha:
            raise SchemaError(f'{name}: the downloaded file does not match its pinned SHA-256')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.part')
        temporary.write_bytes(data)
        temporary.replace(path)
    return directory


def load_schema(directory: Path | None = None):
    """The loaded log schema (module with Event), the same object for the whole process."""
    global _schema
    with _lock:
        if _schema is None:
            import capnp

            capnp.remove_import_hook()
            root = ensure_schema(directory)
            _schema = capnp.load(str(root / 'log.capnp'), imports=[str(root)])
        return _schema


def decompress(data: bytes) -> bytes:
    """The bytes of a log, whether zstd, bzip2 or not compressed."""
    if data[:4] == b'\x28\xb5\x2f\xfd':
        import zstandard

        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    if data[:3] == b'BZh':
        return bz2.decompress(data)
    return data


def read_events(path: str | Path, schema=None) -> Iterator:
    """The events of a log file, in file order."""
    schema = schema or load_schema()
    data = decompress(Path(path).read_bytes())
    yield from schema.Event.read_multiple_bytes(data)


def only(events: Iterable, kinds: set[str]) -> Iterator:
    """The events of the given kinds (event.which())."""
    for event in events:
        if event.which() in kinds:
            yield event
