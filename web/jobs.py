"""The jobs started from the site: start, stop, list, log. Ported from openpilot.distill's web/jobs.py (same
author, MIT), without what only distill needs.

Every job has two files in outputs/web/jobs/:
  <job id>.json   id, action, label, group, argv, cwd, params, summary, started, pid, pgid,
                  finished, exit_code (+ stop_requested, killed when stopped)
  <job id>.log    the output of the job (stdout and stderr), with a "== ..." header and footer
The job id is the UTC start time plus the action: 20261023-154400-mono_baseline. An experiment writes its
results in outputs/experiments/<job id>/.

A job runs under the wrapper "python -m web.jobrun <job json>" in its own session: it outlives the site
(KillMode=process in the unit) and the wrapper writes the exit code itself, so a restart of the site never
loses a result. One running job per group; the code update runs only alone. A GPU job also needs the GPU
lock free (the job takes the lock itself with flock: the site never holds it).
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from web.actions import GROUP_NAMES, JobSpec
from web.common import REPO, WebError, inside, short_text
from web.jobrun import read_job_file, update_job_file
from web.status import results_dir

JOB_ID_RE = re.compile(r'^[0-9]{8}-[0-9]{6}-[a-z_]+$')
LOG_CHUNK_BYTES = 64 * 1024
HISTORY = 50
SCAN_LIMIT = 400
STOP_GRACE_SECONDS = 20
WRAPPER_READY_SECONDS = 5
PROGRESS_RE = re.compile(r'^PROGRESS (\d+)/(\d+)\s*$')
STATE_LABELS = {'running': 'running', 'ok': 'done', 'failed': 'failed', 'stopped': 'stopped',
                'unknown': 'ended (unknown result)'}
# Environment variables of the site that the jobs must not see.
PRIVATE_ENVIRONMENT = ('PERCEPTION_WEB_TOKEN',)


def job_id_for(action: str, moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).strftime('%Y%m%d-%H%M%S') + '-' + action


def read_tail(path: Path, size: int) -> str:
    try:
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - size))
            return handle.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def parse_progress(text: str) -> dict | None:
    """The last "PROGRESS a/b" line of a log."""
    found = None
    for line in text.replace('\r', '\n').split('\n'):
        match = PROGRESS_RE.match(line.strip())
        if match is not None:
            found = (int(match.group(1)), int(match.group(2)))
    if found is None:
        return None
    done, total = found
    percent = round(min(done, total) * 100 / total, 1) if total > 0 else None
    return {'done': done, 'total': total, 'percent': percent}


def process_command_line(pid: int) -> str | None:
    try:
        raw = Path('/proc/' + str(pid) + '/cmdline').read_bytes()
    except OSError:
        return None
    return raw.replace(b'\0', b' ').decode('utf-8', 'replace').strip() or None


class JobManager:
    def __init__(self, repo: Path, *, jobs_dir: Path | None = None, code_root: Path = REPO,
                 python: str = sys.executable, gpu_check=None, stop_grace: float = STOP_GRACE_SECONDS):
        """gpu_check(): '' when a GPU job may start, else the reason."""
        self.repo = repo
        self.jobs_dir = jobs_dir or repo / 'outputs' / 'web' / 'jobs'
        self.code_root = code_root
        self.python = python
        self.gpu_check = gpu_check
        self.stop_grace = stop_grace
        # Reentrant: start() holds it while running_groups() takes it again.
        self._lock = threading.RLock()
        self._processes = {}
        # Job files of finished jobs never change again: read once.
        self._finished = {}

    # -- files -------------------------------------------------------------------------------------
    def job_file(self, job_id: str) -> Path:
        return self.jobs_dir / (job_id + '.json')

    def log_file(self, job_id: str) -> Path:
        return self.jobs_dir / (job_id + '.log')

    def _job_ids(self) -> list[str]:
        if not self.jobs_dir.is_dir():
            return []
        ids = sorted((path.stem for path in self.jobs_dir.glob('*.json') if JOB_ID_RE.fullmatch(path.stem)),
                     reverse=True)
        return ids[:SCAN_LIMIT]

    def _load(self, job_id: str) -> dict | None:
        with self._lock:
            known = self._finished.get(job_id)
        if known is not None:
            return dict(known)
        job = read_job_file(self.job_file(job_id))
        if job is None:
            return None
        if job.get('exit_code') is not None:
            with self._lock:
                self._finished[job_id] = dict(job)
        return job

    # -- state of one job --------------------------------------------------------------------------
    def _is_job_process(self, pid, job_id: str) -> bool:
        """True when pid is still the wrapper of this job (a pid can be reused after a reboot)."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            return False
        command = process_command_line(pid)
        return command is not None and 'web.jobrun' in command and job_id in command

    def _state(self, job: dict) -> str:
        job_id = job.get('id', '')
        code = job.get('exit_code')
        if isinstance(code, int) and not isinstance(code, bool):
            if code == 0:
                return 'ok'
            return 'stopped' if job.get('stop_requested') else 'failed'
        with self._lock:
            process = self._processes.get(job_id)
        if process is not None and process.poll() is None:
            return 'running'
        if self._is_job_process(job.get('pid'), job_id):
            # Started before a restart of the site and still going.
            return 'running'
        if process is not None:
            # The wrapper just ended: its exit code may have been written a moment ago.
            again = read_job_file(self.job_file(job_id))
            if again is not None and isinstance(again.get('exit_code'), int):
                job['exit_code'] = again['exit_code']
                job['finished'] = again.get('finished')
                return self._state(job)
        return 'unknown'

    def _view(self, job: dict) -> dict:
        job_id = job.get('id', '')
        state = self._state(job)
        log = self.log_file(job_id)
        try:
            size = log.stat().st_size
        except OSError:
            size = 0
        view = {'id': job_id, 'action': job.get('action'), 'label': job.get('label'), 'group': job.get('group'),
                'summary': job.get('summary', ''), 'params': job.get('params', {}), 'started': job.get('started'),
                'finished': job.get('finished'), 'exit_code': job.get('exit_code'), 'state': state,
                'state_label': STATE_LABELS[state], 'stoppable': state == 'running', 'log_size': size,
                'progress': None, 'result': None}
        if state == 'running':
            view['progress'] = parse_progress(read_tail(log, LOG_CHUNK_BYTES))
        if (results_dir(self.repo) / job_id / 'report.json').is_file():
            view['result'] = job_id
        return view

    # -- lists -------------------------------------------------------------------------------------
    def list_jobs(self, history: int = HISTORY) -> list[dict]:
        """Running jobs and the last `history` finished ones, newest first."""
        result = []
        finished = 0
        for job_id in self._job_ids():
            job = self._load(job_id)
            if job is None:
                continue
            view = self._view(job)
            if view['state'] == 'running':
                result.append(view)
            elif finished < history:
                result.append(view)
                finished = finished + 1
        return result

    def get(self, job_id: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise WebError(404, 'unknown_job', 'Unknown job.')
        job = self._load(job_id)
        if job is None:
            raise WebError(404, 'unknown_job', 'Unknown job.')
        return self._view(job)

    def running_groups(self) -> set:
        groups = set()
        for job_id in self._job_ids():
            job = self._load(job_id)
            if job is not None and job.get('exit_code') is None and self._state(job) == 'running':
                groups.add(job.get('group'))
        return groups

    # -- start / stop ------------------------------------------------------------------------------
    @staticmethod
    def conflict_reason(group: str, running: set) -> str:
        """'' when a job of `group` may start with `running` groups busy, else why not."""
        if group == 'system':
            if running:
                names = ', '.join(GROUP_NAMES.get(other, other) for other in sorted(running))
                return 'The code can only be updated when nothing else is running (running: ' + names + ').'
            return ''
        if 'system' in running:
            return 'A code update is running: wait for it to finish.'
        if group in running:
            return 'There is already ' + GROUP_NAMES.get(group, group) + ' running: wait for it to finish.'
        return ''

    def start(self, spec: JobSpec) -> dict:
        with self._lock:
            reason = self.conflict_reason(spec.group, self.running_groups())
            if reason:
                raise WebError(409, 'busy', reason)
            if spec.needs_gpu and self.gpu_check is not None:
                reason = self.gpu_check()
                if reason:
                    raise WebError(409, 'gpu_busy', reason)

            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            started = time.time()
            job_id = job_id_for(spec.action, started)
            job_file = self.job_file(job_id)
            log_file = self.log_file(job_id)
            if job_file.exists() or log_file.exists():
                raise WebError(409, 'busy', 'A job has just started: try again in a second.')
            run_dir = results_dir(self.repo) / job_id
            radar_dir = self.repo / 'outputs' / 'radar'
            argv = [str(item).replace('{run_dir}', str(run_dir)).replace('{radar_dir}', str(radar_dir))
                    for item in spec.argv]
            if spec.prepare is not None:
                run_dir.mkdir(parents=True, exist_ok=True)
                spec.prepare(run_dir, job_id)

            job = {'id': job_id, 'action': spec.action, 'label': spec.label, 'group': spec.group, 'argv': argv,
                   'cwd': str(self.code_root), 'params': spec.params, 'summary': spec.summary, 'started': started,
                   'pid': None, 'pgid': None, 'finished': None, 'exit_code': None,
                   'log': str(log_file.relative_to(self.repo)) if inside(self.repo, log_file) else str(log_file)}
            update_job_file(job_file, job)

            environment = dict(os.environ)
            for name in PRIVATE_ENVIRONMENT:
                environment.pop(name, None)
            # Live logs: python children print at once instead of every 8 KiB.
            environment['PYTHONUNBUFFERED'] = '1'
            stamp = datetime.fromtimestamp(started, UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
            with log_file.open('ab') as log:
                log.write(('== ' + spec.label + ', started from the site on ' + stamp + '\n').encode('utf-8'))
                if spec.summary:
                    log.write(('== ' + spec.summary + '\n').encode('utf-8'))
                log.flush()
                try:
                    # A new session: the job outlives the site and is stopped as one process group.
                    process = subprocess.Popen([self.python, '-m', 'web.jobrun', str(job_file.resolve())],
                                               cwd=str(self.code_root), stdin=subprocess.DEVNULL, stdout=log,
                                               stderr=subprocess.STDOUT, env=environment, start_new_session=True)
                except OSError as exc:
                    update_job_file(job_file, {'exit_code': 127, 'finished': time.time()})
                    raise WebError(500, 'start_failed', 'Cannot start the job: ' + str(exc)) from exc
            update_job_file(job_file, {'pid': process.pid, 'pgid': process.pid})
            self._processes[job_id] = process
            self._wait_wrapper_ready(job_file, process)
        # Reaps the wrapper as soon as it ends, so it never stays a zombie.
        threading.Thread(target=process.wait, name='reap-' + job_id, daemon=True).start()
        return self.get(job_id)

    def _wait_wrapper_ready(self, job_file: Path, process, timeout: float = WRAPPER_READY_SECONDS) -> None:
        """Waits until the wrapper has its signal handlers (wrapper_ready in the job file): a stop sent
        before that would kill the wrapper and lose the exit code."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = read_job_file(job_file)
            if job is not None and job.get('wrapper_ready') is not None:
                return
            if process.poll() is not None:
                return
            time.sleep(0.02)

    def stop(self, job_id: str) -> dict:
        view = self.get(job_id)
        if view['state'] != 'running':
            raise WebError(409, 'not_running', 'The job is not running.')
        job = read_job_file(self.job_file(job_id))
        pgid, pid = job.get('pgid'), job.get('pid')
        # Only a process group whose leader is still the wrapper of this very job.
        if not self._is_job_process(pid, job_id) or pgid != pid:
            raise WebError(409, 'not_running', 'Cannot find the job process: it may have just finished.')
        update_job_file(self.job_file(job_id), {'stop_requested': time.time()})
        self._append_log(job_id, '\n== stopped from the site: SIGTERM to group ' + str(pgid) + '\n')
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return self.get(job_id)
        threading.Thread(target=self._finish_stop, args=(job_id, pgid, self.stop_grace), name='stop-' + job_id,
                         daemon=True).start()
        return self.get(job_id)

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _finish_stop(self, job_id: str, pgid: int, grace: float = STOP_GRACE_SECONDS) -> None:
        """SIGKILL to the group when SIGTERM was not enough within `grace` seconds."""
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not self._group_alive(pgid):
                self._record_stopped(job_id, -15, False)
                return
            time.sleep(0.5)
        self._append_log(job_id, '\n== did not stop within ' + str(int(grace)) + ' s: SIGKILL to the group\n')
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        time.sleep(1)
        self._record_stopped(job_id, -9, True)

    def _record_stopped(self, job_id: str, code: int, killed: bool) -> None:
        """Exit code of a stopped job whose wrapper could not write it (it died with the group)."""
        time.sleep(0.5)
        job = read_job_file(self.job_file(job_id))
        if job is None or job.get('exit_code') is not None:
            return
        update_job_file(self.job_file(job_id), {'exit_code': code, 'finished': time.time(), 'killed': killed})
        self._append_log(job_id, '\n== finished with code ' + str(code) + ' (stopped from the site)\n')

    def _append_log(self, job_id: str, text: str) -> None:
        try:
            with self.log_file(job_id).open('ab') as log:
                log.write(text.encode('utf-8'))
        except OSError:
            pass

    # -- log ---------------------------------------------------------------------------------------
    def log(self, job_id: str, offset: int) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise WebError(404, 'unknown_job', 'Unknown job.')
        path = self.log_file(job_id)
        if not path.is_file():
            raise WebError(404, 'unknown_job', 'Unknown job.')
        size = path.stat().st_size
        if offset < 0 or offset > size:
            offset = 0
        # The first call shows only the end of a long log.
        if offset == 0 and size > LOG_CHUNK_BYTES:
            offset = size - LOG_CHUNK_BYTES
        with path.open('rb') as source:
            source.seek(offset)
            data = source.read(LOG_CHUNK_BYTES)
        return {'id': job_id, 'text': data.decode('utf-8', 'replace'), 'offset': offset + len(data), 'size': size}


def describe(job: dict) -> str:
    """One line for the server log (journald): never parameters that could be long or odd."""
    return short_text(str(job.get('id')) + ' (' + str(job.get('label')) + ')', 160)
