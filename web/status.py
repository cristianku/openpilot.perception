"""What the site shows: the machine (CPU, RAM, disk, GPU), whether the GPU is free, the drives this server can
use and the results of the experiments. Read only: nothing here changes a file.

Drives: openpilot.perception has no drives of its own. It reads the data/ folder of an openpilot.distill clone
on the same server (segments downloaded from the comma and localized there). The candidates are found by
themselves (the clone this repo is a submodule of, the clones next to this one, PERCEPTION_DATA_DIR) and the
page names them, never with their path.

Results: outputs/experiments/<job id>/ holds experiment.json (what was run, written when it starts),
report.json (written by the experiment when it ends) and pictures/ (a few frames with their depth).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from web.common import inside, short_text

GPU_LOCK_FILE = '/run/lock/v100-gpu.lock'
# A segment of openpilot.distill: <16 hex route id><2+ hex segment number>, e.g. 0000011e0c8825628a01.
SEGMENT_RE = re.compile(r'^[0-9a-f]{18,24}$')
RESULT_ID_RE = re.compile(r'^[0-9]{8}-[0-9]{6}-[a-z_]+$')
PICTURE_RE = re.compile(r'^[0-9a-f]{18,24}-[0-9]{4}\.jpg$')
SOURCE_ID_RE = re.compile(r'^[0-9a-f]{12}$')
EXPERIMENT_KINDS = ('geometry_audit', 'mono_baseline')
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_PICTURES = 120


# -- machine -----------------------------------------------------------------------------------------
def _run(arguments: list[str], timeout: float = 5) -> str | None:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _number(text: str):
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# The last /proc/stat sample: CPU use is the busy share of the time between two samples.
_cpu_sample = {'times': None}
_cpu_lock = threading.Lock()


def parse_proc_stat(text: str) -> list[tuple[int, int]]:
    """(busy, total) jiffies of the whole CPU (first) and of every core, from /proc/stat."""
    rows = []
    for line in text.splitlines():
        if not line.startswith('cpu'):
            continue
        try:
            numbers = [int(value) for value in line.split()[1:]]
        except ValueError:
            continue
        if len(numbers) < 4:
            continue
        # idle + iowait are not busy; guest time is already inside user and nice.
        idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
        total = sum(numbers[:8])
        rows.append((total - idle, total))
    return rows


def cpu_usage() -> dict | None:
    """{'total': %, 'cores': [%, ...]} since the previous call; None on the first call or without /proc."""
    try:
        current = parse_proc_stat(Path('/proc/stat').read_text())
    except OSError:
        return None
    with _cpu_lock:
        previous = _cpu_sample['times']
        _cpu_sample['times'] = current
    if previous is None or len(previous) != len(current):
        return None
    percents = []
    for (busy, total), (old_busy, old_total) in zip(current, previous, strict=True):
        elapsed = total - old_total
        percents.append(round(max(0.0, min(100.0, (busy - old_busy) * 100 / elapsed)), 1) if elapsed > 0 else 0.0)
    return {'total': percents[0], 'cores': percents[1:]}


def memory_info() -> dict | None:
    """RAM from /proc/meminfo (inside the LXC it is the container's own memory)."""
    try:
        text = Path('/proc/meminfo').read_text()
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        name, separator, rest = line.partition(':')
        parts = rest.split()
        if separator and parts and parts[0].isdigit():
            values[name.strip()] = int(parts[0]) * 1024
    if 'MemTotal' not in values or 'MemAvailable' not in values:
        return None
    total, available = values['MemTotal'], values['MemAvailable']
    return {'total_gb': round(total / 1e9, 1), 'available_gb': round(available / 1e9, 1),
            'used_percent': round((total - available) * 100 / total, 1)}


def gpu_info() -> dict | None:
    output = _run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                   '--format=csv,noheader,nounits'])
    if not output or not output.strip():
        return None
    fields = output.strip().splitlines()[0].split(',')
    if len(fields) < 5:
        return None
    info = {'name': fields[0].strip(), 'utilization': _number(fields[1]), 'memory_used_mb': _number(fields[2]),
            'memory_total_mb': _number(fields[3]), 'temperature': _number(fields[4]), 'processes': 0}
    apps = _run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'])
    if apps:
        info['processes'] = len([line for line in apps.splitlines() if line.strip()])
    return info


def system_info(repo: Path) -> dict:
    info = {'cpus': os.cpu_count(), 'cpu': cpu_usage(), 'memory': memory_info(), 'disk': None, 'gpu': gpu_info()}
    try:
        usage = shutil.disk_usage(repo)
        info['disk'] = {'free_gb': round(usage.free / 1e9, 1), 'total_gb': round(usage.total / 1e9, 1),
                        'used_percent': round((usage.total - usage.free) * 100 / usage.total, 1)}
    except OSError:
        pass
    return info


def gpu_lock_held(lock_file: str = GPU_LOCK_FILE) -> bool | None:
    """True when another process holds the GPU lock that every GPU job of this server takes (flock).
    Takes it for an instant and lets it go: call it right before a GPU job starts, never periodically."""
    try:
        handle = os.open(lock_file, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return None
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    finally:
        os.close(handle)


def gpu_busy_reason(lock_file: str = GPU_LOCK_FILE) -> str:
    """'' when a GPU job can start now, else why not, in words for the page."""
    gpu = gpu_info()
    if gpu is None:
        return 'This server has no NVIDIA GPU (or its driver does not answer).'
    held = gpu_lock_held(lock_file)
    if held is None:
        return 'Cannot check whether the GPU is free.'
    if held:
        return 'The GPU is busy with another job (a training or an evaluation of this server): try again later.'
    if gpu['processes']:
        return 'The GPU is busy (' + str(gpu['processes']) + ' processes on it): try again later.'
    return ''


# -- drives ------------------------------------------------------------------------------------------
def _is_distill(folder: Path) -> bool:
    try:
        text = (folder / 'pyproject.toml').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return False
    return re.search(r'^name\s*=\s*"openpilot-distill"', text, re.MULTILINE) is not None


def source_id(path: Path) -> str:
    """The name the page uses for a drives folder: a hash of its path, so the path never leaves the server."""
    return hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]


def source_candidates(repo: Path, configured: str = '') -> list[dict]:
    """The drives folders this server could use, best first: {'id', 'name', 'path'} (path stays on the server)."""
    found = []

    def add(path: Path, name: str) -> None:
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            return
        if path.is_dir() and all(item['path'] != path for item in found):
            found.append({'id': source_id(path), 'name': name, 'path': path})

    if configured:
        add(Path(configured), 'Set up for this server')
    # This repo as the perception_repo submodule of openpilot.distill.
    if _is_distill(repo.parent):
        add(repo.parent / 'data', 'openpilot.distill (this repo is part of it)')
    try:
        siblings = sorted(repo.parent.iterdir())
    except OSError:
        siblings = []
    for folder in siblings:
        if folder.is_dir() and folder != repo and _is_distill(folder):
            add(folder / 'data', 'openpilot.distill' + ('' if folder.name == 'openpilot.distill'
                                                         else ' (' + short_text(folder.name, 40) + ')'))
    return found


def segment_rows(folder: Path) -> list[dict]:
    """Every segment of a drives folder: name, route, and whether the experiments can use it (localized)."""
    rows = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return rows
    for entry in entries:
        if not SEGMENT_RE.fullmatch(entry.name) or not entry.is_dir():
            continue
        video = (entry / 'fcamera.hevc').is_file()
        localized = (entry / 'localizer.safetensors').is_file()
        rows.append({'name': entry.name, 'route': entry.name[:18], 'video': video, 'ready': video and localized,
                     'log': (entry / 'rlog.zst').is_file()})
    return rows


def drives_summary(rows: list[dict]) -> dict:
    ready = [row for row in rows if row['ready']]
    return {'segments': len(rows), 'ready': len(ready), 'routes': len({row['route'] for row in rows}),
            'ready_routes': len({row['route'] for row in ready})}


def spread_sample(names: list[str], count: int) -> list[str]:
    """`count` names spread evenly over a sorted list (all of them when there are not more), so a sample
    covers every drive instead of the first minutes of one."""
    names = sorted(names)
    if count >= len(names):
        return names
    if count <= 1:
        return names[:count]
    picked = []
    for index in range(count):
        name = names[round(index * (len(names) - 1) / (count - 1))]
        if name not in picked:
            picked.append(name)
    return picked


# -- results -----------------------------------------------------------------------------------------
def _read_json(path: Path, limit: int = MAX_REPORT_BYTES):
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def results_dir(repo: Path) -> Path:
    return repo / 'outputs' / 'experiments'


class ResultReader:
    """The summaries of the experiment reports. A report never changes once written: each is read once
    (they hold every point, megabytes) and only its summary is kept."""

    def __init__(self, repo: Path):
        self.repo = repo
        self._lock = threading.Lock()
        self._summaries = {}

    def _summary(self, folder: Path):
        report = folder / 'report.json'
        try:
            stamp = report.stat().st_mtime
        except OSError:
            return None
        with self._lock:
            known = self._summaries.get(folder.name)
        if known is not None and known[0] == stamp:
            return known[1]
        data = _read_json(report)
        summary = data.get('summary') if isinstance(data, dict) else None
        if not isinstance(summary, dict):
            summary = None
        with self._lock:
            self._summaries[folder.name] = (stamp, summary)
        return summary

    def results(self) -> list[dict]:
        """Every experiment that has its experiment.json, newest first (a running one has no report yet)."""
        base = results_dir(self.repo)
        found = []
        try:
            folders = sorted(base.iterdir(), reverse=True)
        except OSError:
            return found
        for folder in folders:
            if not RESULT_ID_RE.fullmatch(folder.name) or not inside(base, folder) or not folder.is_dir():
                continue
            meta = _read_json(folder / 'experiment.json', 256 * 1024)
            if not isinstance(meta, dict) or meta.get('kind') not in EXPERIMENT_KINDS:
                continue
            found.append({'id': folder.name, 'kind': meta['kind'], 'label': short_text(meta.get('label', ''), 120),
                          'started': meta.get('started'), 'segments': meta.get('segments'),
                          'routes': meta.get('routes'), 'note': short_text(meta.get('note', ''), 300),
                          'summary': self._summary(folder), 'pictures': pictures(self.repo, folder.name)})
        return found


def pictures(repo: Path, result_id: str) -> list[str]:
    folder = results_dir(repo) / result_id / 'pictures'
    try:
        names = sorted(entry.name for entry in folder.iterdir() if PICTURE_RE.fullmatch(entry.name))
    except OSError:
        return []
    return names[:MAX_PICTURES]


def picture_path(repo: Path, result_id: str, name: str) -> Path | None:
    """The file of a picture of a result, only when both names are valid and it really is inside outputs/."""
    if not RESULT_ID_RE.fullmatch(result_id) or not PICTURE_RE.fullmatch(name):
        return None
    base = results_dir(repo)
    path = base / result_id / 'pictures' / name
    if not inside(base, path) or not path.is_file():
        return None
    return path


class Cache:
    """One value, computed again at most every `seconds`."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._lock = threading.Lock()
        self._value = None
        self._at = 0.0

    def get(self, compute):
        with self._lock:
            if self._value is not None and time.monotonic() - self._at < self.seconds:
                return self._value
        value = compute()
        with self._lock:
            self._value = value
            self._at = time.monotonic()
        return value

    def clear(self) -> None:
        with self._lock:
            self._value = None
