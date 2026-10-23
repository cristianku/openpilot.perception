"""What the site can start: the experiments of docs/review-decisions.md and the code update. Every parameter
from the page is checked here; the job runs a fixed command line (no shell) on the chosen drives.

  geometry_audit  Experiment 0 (perception.audit.geometry_audit), CPU only.
  mono_baseline   Experiment 1 (perception.experiments.mono_baseline), on the GPU under the server's GPU lock.
  update          tasks/update.py: the latest code from GitHub, the libraries, a restart of the site.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from web.common import WebError
from web.status import GPU_LOCK_FILE

# How many segments each size takes, spread over every drive; None = all of them.
SIZES = {'quick': 10, 'standard': 30, 'all': None}
SIZE_LABELS = {'quick': 'quick look', 'standard': 'standard', 'all': 'every drive'}
GROUP_NAMES = {'geometry_audit': 'a geometry audit', 'mono_baseline': 'a depth baseline', 'system': 'a code update',
               'radar': 'a reading of the radar'}


@dataclass
class Action:
    label: str
    group: str
    needs_gpu: bool = False
    needs_drives: bool = False


ACTIONS = {
    'geometry_audit': Action('Geometry audit', 'geometry_audit', needs_drives=True),
    'mono_baseline': Action('Depth baseline', 'mono_baseline', needs_gpu=True, needs_drives=True),
    'update': Action('Update the code', 'system'),
    'radar_index': Action('Read the radar', 'radar'),
    'fetch_recordings': Action('Get recordings from the comma', 'radar'),
}
# Recordings copied in one go (the radar tests are a minute each).
MAX_FETCH = 6


@dataclass
class JobSpec:
    """A validated job, ready to run. "{run_dir}" in argv becomes outputs/experiments/<job id>."""
    action: str
    label: str
    group: str
    argv: list
    needs_gpu: bool = False
    params: dict = field(default_factory=dict)
    summary: str = ''
    # Called with the folder of the job's results before it starts (experiment.json).
    prepare: Callable[[Path, str], None] | None = None


def audit_jobs() -> int:
    """Segments audited at a time: half the cores, at most 8 (each one decodes a video and runs SIFT)."""
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def size_param(params) -> str:
    if not isinstance(params, dict) or set(params) - {'size'}:
        raise WebError(400, 'invalid_param', 'Send {"size": "quick" | "standard" | "all"}.')
    size = params.get('size', 'standard')
    if size not in SIZES:
        raise WebError(400, 'invalid_param', 'size must be quick, standard or all.')
    return size


def build_job(action_id: str, params, segments: list[Path] | None = None, python: str = sys.executable) -> JobSpec:
    """The job of an action. `segments`: the folders it runs on, already chosen (web/server.py)."""
    action = ACTIONS.get(action_id)
    if action is None:
        raise WebError(404, 'unknown_action', 'Unknown action.')
    if action_id == 'update':
        if params:
            raise WebError(400, 'invalid_param', 'Send {}.')
        return JobSpec('update', action.label, action.group, [python, '-m', 'tasks.update'],
                       summary='latest code, libraries, restart of the site')
    if action_id == 'radar_index':
        folders = [str(folder) for folder in segments or []]
        return JobSpec('radar_index', action.label, action.group,
                       [python, '-m', 'perception.radar.index', *folders, '--out', '{radar_dir}'],
                       summary=str(len(folders)) + (' segment' if len(folders) == 1 else ' segments') + ' to read')
    if action_id == 'fetch_recordings':
        from web.comma import RECORDING_RE

        names = params.get('recordings') if isinstance(params, dict) and set(params) == {'recordings'} else None
        if not isinstance(names, list) or not 1 <= len(names) <= MAX_FETCH or not all(
                isinstance(name, str) and RECORDING_RE.fullmatch(name) for name in names):
            raise WebError(400, 'invalid_param', 'Send {"recordings": ["<route>--<segment>", ...]} (at most '
                           + str(MAX_FETCH) + ').')
        return JobSpec('fetch_recordings', action.label, action.group,
                       [python, '-m', 'tasks.fetch_recordings', *names], params={'recordings': names},
                       summary=str(len(names)) + (' recording' if len(names) == 1 else ' recordings'))
    size = size_param(params)
    folders = [str(folder) for folder in segments or []]
    routes = len({Path(folder).name[:18] for folder in folders})
    summary = (str(len(folders)) + ' segments from ' + str(routes) + (' drive' if routes == 1 else ' drives')
               + ' (' + SIZE_LABELS[size] + ')')
    if action_id == 'geometry_audit':
        argv = [python, '-m', 'perception.audit.geometry_audit', *folders, '--jobs', str(audit_jobs()),
                '--out', '{run_dir}/report.json']
    else:
        # flock -n: refused at once when another job holds the GPU (the site checked it a moment ago).
        argv = ['flock', '-n', GPU_LOCK_FILE, python, '-m', 'perception.experiments.mono_baseline', *folders,
                '--out', '{run_dir}/report.json', '--pictures', '{run_dir}/pictures']
    return JobSpec(action_id, action.label, action.group, argv, needs_gpu=action.needs_gpu, params={'size': size},
                   summary=summary)
