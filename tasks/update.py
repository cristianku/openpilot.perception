"""Update this clone from GitHub and put the new code to work, always the same way.

    python -m tasks.update

  0. refuses while an experiment of this clone runs (started from the site or by hand): the package sync
     would change the packages under it.
  1. git pull --ff-only of the current branch. Refuses when files of the repo are modified locally (it
     lists them) or the branch has diverged: nothing is changed then.
  2. uv sync --locked, with --extra PERCEPTION_UV_EXTRA when the repo .env sets it (v100 on a Tesla V100,
     written by install.sh). --locked: uv.lock is used as it is and never rewritten.
  3. with the systemd service perception-web installed: restarts it, so the site runs the new code. The
     jobs started from the site keep running (KillMode=process in the unit).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVICE = 'perception-web'
# The experiments, by a fragment of their command line.
BUSY_JOBS = (
    (re.compile(r'perception[.]audit[.]geometry_audit'), 'a geometry audit'),
    (re.compile(r'perception[.]experiments[.]'), 'an experiment'),
)


class TaskError(Exception):
    pass


def say(text: str) -> None:
    print(text, flush=True)


def load_env() -> None:
    """KEY=value lines of the repo .env into the environment (the environment wins)."""
    try:
        lines = (REPO / '.env').read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        name, separator, value = line.partition('=')
        name = name.strip()
        if separator and name and not name.startswith('#') and name not in os.environ:
            os.environ[name] = value.strip().strip('"\'')


def busy_jobs() -> list[str]:
    """The experiments that use this clone, in words, from the command lines in /proc."""
    found = []
    for entry in Path('/proc').iterdir() if Path('/proc').is_dir() else ():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
        except OSError:
            continue
        for pattern, label in BUSY_JOBS:
            if pattern.search(command) and label not in found:
                found.append(label)
                break
    return found


def git(*arguments: str, check: bool = True) -> str:
    environment = dict(os.environ)
    # A remote that wants a password fails instead of waiting for it.
    environment['GIT_TERMINAL_PROMPT'] = '0'
    result = subprocess.run(['git', *arguments], cwd=REPO, env=environment, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    if check and result.returncode != 0:
        raise TaskError('git ' + arguments[0] + ' failed: ' + (result.stderr.strip() or 'unknown error'))
    return result.stdout.strip()


def uv_executable() -> str:
    for candidate in (shutil.which('uv'), str(Path.home() / '.local' / 'bin' / 'uv')):
        if candidate and Path(candidate).is_file():
            return candidate
    raise TaskError('uv not found on this server: run install.sh again')


def service_installed() -> bool:
    if shutil.which('systemctl') is None:
        return False
    result = subprocess.run(['systemctl', 'list-unit-files', SERVICE + '.service'], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    return any(line.startswith(SERVICE + '.service') for line in result.stdout.splitlines())


def main() -> int:
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    if len(sys.argv) > 1:
        raise TaskError('invalid option: ' + sys.argv[1])
    load_env()
    uv = uv_executable()

    # 0. Jobs that use this clone.
    busy = busy_jobs()
    if busy:
        raise TaskError('running now: ' + ', '.join(busy) + ': update when it has finished')
    if git('rev-parse', '--is-inside-work-tree', check=False) != 'true':
        raise TaskError('this folder is not a git clone: nothing to update')
    branch = git('branch', '--show-current')
    if not branch:
        raise TaskError('this clone is not on a branch (a submodule checkout?): nothing to update')

    # 1. git pull.
    modified = git('status', '--porcelain', '--untracked-files=no')
    if modified:
        say('Repo files modified locally:')
        say(modified)
        raise TaskError('the repo files have local changes (above): not overwriting them')
    before = git('rev-parse', 'HEAD')
    say('== 1. downloading the new code (branch ' + branch + ')')
    try:
        git('pull', '--ff-only')
    except TaskError as exc:
        raise TaskError(str(exc) + ' (diverged branch or network?): nothing changed') from None
    after = git('rev-parse', 'HEAD')
    say('Already up to date: ' + git('log', '--oneline', '-1') if before == after
        else git('log', '--oneline', before + '..' + after))

    # 2. Packages of .venv.
    command = [uv, 'sync', '--locked']
    extra = os.environ.get('PERCEPTION_UV_EXTRA', '')
    if extra:
        command += ['--extra', extra]
    say('== 2. updating the packages' + (' (extra ' + extra + ')' if extra else ''))
    if subprocess.run(command, cwd=REPO, stdin=subprocess.DEVNULL).returncode != 0:
        raise TaskError('package update failed: the code is updated but the packages are not '
                        '(uv.lock out of sync with pyproject.toml?)')

    # 3. The web site.
    if not service_installed():
        say('== 3. the site does not run as a service: restart it to use the new code')
        say('Done.')
        return 0
    if before != after and any(name.startswith(('deploy/', 'install.sh'))
                               for name in git('diff', '--name-only', before, after).splitlines()):
        say('WARNING: the installer or the service file changed: run install.sh again to use them')
    say('== 3. restarting the site')
    subprocess.run(['systemctl', 'restart', SERVICE], stdin=subprocess.DEVNULL, check=False)
    time.sleep(2)
    if subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE], stdin=subprocess.DEVNULL).returncode != 0:
        raise TaskError('the site does not start again after the update: check its log (journalctl -u '
                        + SERVICE + ')')
    say('Site restarted with the new code.')
    say('Done.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except TaskError as exc:
        print('ERROR: ' + str(exc), flush=True)
        sys.exit(1)
