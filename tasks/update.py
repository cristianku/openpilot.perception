"""Update this clone from GitHub and put the new code to work, always the same way.

    python -m tasks.update

  0. refuses while an experiment of this clone runs (started from the site or by hand): the package sync
     would change the packages under it. Also while a build of its Android app runs (it holds
     outputs/android/.build.lock): the new code would change under it.
  1. git pull --ff-only of the current branch. Refuses when files of the repo are modified locally (it
     lists them) or the branch has diverged: nothing is changed then.
  2. uv sync --locked, with --extra PERCEPTION_UV_EXTRA when the repo .env sets it (v100 on a Tesla V100,
     written by install.sh). --locked: uv.lock is used as it is and never rewritten.
  3. with the systemd service perception-web installed: restarts it, so the site runs the new code at once.
     The jobs started from the site keep running (KillMode=process in the unit), this update too.
  4. the Android app (helpers/build_android.sh), after the restart (it takes minutes), when the update
     changed app/ or that script, or this server has no build of it yet (no APK and AAB in
     outputs/android/latest.json), or the last build is not the one of the code (made with local changes,
     or before a commit that changed app/), or, where Google Play can be published to (the key of the Play
     service account is on this server), the last build is not on Google Play yet (no tag
     perception-android-v<version> here or on GitHub). The new APK and AAB, then Google Play as the script
     decides (the internal track, when the commit is on GitHub and the version is not on Google Play yet;
     the first version of the app goes by hand, and the log says which AAB). Ended after ANDROID_TIMEOUT_S,
     Gradle with it. A failure is only a warning: the site works without the app.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
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
# What the Android app is built from (app/comma_listener.py goes into the APK too): a change there means a new
# build.
ANDROID_SOURCES = ('app/', 'helpers/build_android.sh')
ANDROID_OUT = REPO / 'outputs' / 'android'
# A build takes minutes (the tools set up again: more); one that hangs (Gradle, Google Play) is ended after this.
ANDROID_TIMEOUT_S = 45 * 60


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
    if android_build_running():
        found.append('a build of the Android app')
    return found


def android_build_running() -> bool:
    """True while helpers/build_android.sh of this clone runs: it holds outputs/android/.build.lock (flock)."""
    try:
        with open(ANDROID_OUT / '.build.lock', 'rb') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def android_build_reason(changed: list[str]) -> str:
    """Why the update builds the Android app, '' when the last build (outputs/android/latest.json) is still the
    one of the code: `changed` are the files the update changed. A build made with local changes, or before
    commits that changed app/ (committed on this server, not pulled), is built again: only a build of a
    commit can go to Google Play."""
    if any(name.startswith(ANDROID_SOURCES) for name in changed):
        return 'the update changed it'
    try:
        latest = json.loads((ANDROID_OUT / 'latest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'none on this server yet'
    if not isinstance(latest, dict):
        return 'none on this server yet'
    for field in ('file', 'aab'):
        name = latest.get(field)
        if not isinstance(name, str) or '/' in name or not (ANDROID_OUT / name).is_file():
            return 'no APK and AAB of the last build'
    version, commit = latest.get('version'), latest.get('commit')
    if not isinstance(version, str) or version.endswith('-dev'):
        return 'the last build had local changes'
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{4,40}', commit) \
            or not git('rev-parse', '--verify', '--quiet', commit + '^{commit}', check=False):
        return 'the commit of the last build is not in this clone'
    if git('diff', '--name-only', commit, 'HEAD', '--', *ANDROID_SOURCES, check=False):
        return 'the last build is older than app/'
    # Built but never published (HEAD was not pushed yet, GitHub or Google Play could not be reached, the upload
    # failed): built again, and the script publishes it when it can. Only where it can publish at all.
    if play_key().is_file() and on_google_play(version) is False:
        return 'version ' + version + ' is not on Google Play yet'
    return ''


def play_key() -> Path:
    """The key of the Google Play service account, where helpers/build_android.sh reads it."""
    keys = os.environ.get('PERCEPTION_ANDROID_KEYS') or str(Path.home() / '.config' / 'distill-android')
    return Path(keys) / 'play-service-account.json'


def on_google_play(version: str) -> bool | None:
    """True when the tag perception-android-v<version> (made by helpers/build_android.sh once Google Play has the
    version) is in this clone or on GitHub, False when neither has it, None when GitHub cannot be asked."""
    tag = 'refs/tags/perception-android-v' + version
    if git('rev-parse', '--verify', '--quiet', tag, check=False):
        return True
    try:
        result = subprocess.run(['git', 'ls-remote', '--exit-code', '--tags', 'origin', tag], cwd=REPO,
                                env=git_environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # --exit-code: 2 when origin answered without such a tag.
    return {0: True, 2: False}.get(result.returncode)


def stop_group(process: subprocess.Popen, grace_s: float = 30.0) -> None:
    """SIGTERM to every process of the group that `process` leads, SIGKILL to what is left after grace_s."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.poll()


def build_android() -> int | None:
    """helpers/build_android.sh, in a process group of its own so that all of it ends together, Gradle too: after
    ANDROID_TIMEOUT_S (None then), or when this update is stopped (from the site: SIGTERM)."""
    process = subprocess.Popen([str(REPO / 'helpers' / 'build_android.sh')], cwd=REPO, stdin=subprocess.DEVNULL,
                               start_new_session=True)

    def stopped(signum, frame):
        stop_group(process)
        say('Stopped: the build of the Android app was ended.')
        sys.exit(128 + signum)

    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        return process.wait(timeout=ANDROID_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        stop_group(process)
        return None
    finally:
        signal.signal(signal.SIGTERM, previous)


def git_environment() -> dict:
    environment = dict(os.environ)
    # A remote that wants a password fails instead of waiting for it.
    environment['GIT_TERMINAL_PROMPT'] = '0'
    return environment


def git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(['git', *arguments], cwd=REPO, env=git_environment(), capture_output=True, text=True,
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

    # 3. The web site, at once: the old one must not serve with the new code and packages on disk while the app
    # is built.
    changed = git('diff', '--name-only', before, after).splitlines() if before != after else []
    if not service_installed():
        say('== 3. the site does not run as a service: restart it to use the new code')
    else:
        if any(name.startswith(('deploy/', 'install.sh')) for name in changed):
            say('WARNING: the installer or the service file changed: run install.sh again to use them')
        say('== 3. restarting the site')
        subprocess.run(['systemctl', 'restart', SERVICE], stdin=subprocess.DEVNULL, check=False)
        time.sleep(2)
        if subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE],
                          stdin=subprocess.DEVNULL).returncode != 0:
            raise TaskError('the site does not start again after the update: check its log (journalctl -u '
                            + SERVICE + ')')
        say('Site restarted with the new code.')

    # 4. The Android app: the build script decides about Google Play (and says why not).
    reason = android_build_reason(changed)
    if reason:
        say('== 4. building the Android app (' + reason + ')')
        code = build_android()
        if code is None:
            say('WARNING: the build of the Android app took more than ' + str(ANDROID_TIMEOUT_S // 60)
                + ' minutes and was ended; the site works without it')
        elif code != 0:
            say('WARNING: the Android app was not built (above); the site works without it')
    else:
        say('== 4. the Android app did not change')
    say('Done.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except TaskError as exc:
        print('ERROR: ' + str(exc), flush=True)
        sys.exit(1)
