"""tasks/update.py: when the code update builds the Android app again, that the site is restarted before the build,
and that a build that hangs is ended whole. A temporary git clone with a local "origin"; nothing is built or pushed
anywhere, no service is restarted."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tasks import update


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


@unittest.skipIf(shutil.which('git') is None, 'git is not installed')
class AndroidBuildReason(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.origin = base / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(self.origin)], check=True)
        self.repo = base / 'clone'
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        for key, value in (('user.name', 'Test'), ('user.email', 'test@example.invalid'), ('commit.gpgsign', 'false'),
                           ('tag.gpgsign', 'false')):
            git(self.repo, 'config', key, value)
        (self.repo / 'app').mkdir()
        (self.repo / 'app' / 'comma_listener.py').write_text('print(1)\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'first')
        git(self.repo, 'remote', 'add', 'origin', str(self.origin))
        git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.out = self.repo / 'outputs' / 'android'
        self.out.mkdir(parents=True)
        self.keys = base / 'keys'
        self.keys.mkdir()
        for patcher in (mock.patch.object(update, 'REPO', self.repo),
                        mock.patch.object(update, 'ANDROID_OUT', self.out),
                        mock.patch.dict(os.environ, {'PERCEPTION_ANDROID_KEYS': str(self.keys)})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def built(self, version='0.1.1', commit=None):
        for name in ('perception-android-' + version + '.apk', 'perception-android-' + version + '.aab'):
            (self.out / name).write_bytes(b'x')
        (self.out / 'latest.json').write_text(json.dumps({
            'version': version, 'file': 'perception-android-' + version + '.apk',
            'aab': 'perception-android-' + version + '.aab', 'commit': commit or git(self.repo, 'rev-parse', '--short',
                                                                                       'HEAD')}))

    def test_the_build_of_the_code(self):
        self.assertEqual(update.android_build_reason([]), 'none on this server yet')
        self.assertEqual(update.android_build_reason(['app/android/x.kt']), 'the update changed it')
        self.built('0.1.1-dev')
        self.assertEqual(update.android_build_reason([]), 'the last build had local changes')
        self.built()
        # Without the key of Google Play nothing is published here: the build stays as it is.
        self.assertEqual(update.android_build_reason([]), '')

    def test_built_but_not_on_google_play(self):
        self.built()
        (self.keys / 'play-service-account.json').write_text('{}')
        self.assertEqual(update.android_build_reason([]), 'version 0.1.1 is not on Google Play yet')
        # Tagged on GitHub (published by another clone) or here: on Google Play.
        git(self.repo, 'tag', 'perception-android-v0.1.1')
        self.assertEqual(update.android_build_reason([]), '')
        git(self.repo, 'push', '-q', 'origin', 'refs/tags/perception-android-v0.1.1')
        git(self.repo, 'tag', '-d', 'perception-android-v0.1.1')
        self.assertEqual(update.android_build_reason([]), '')
        # GitHub cannot be asked: not built again for it (the build could not publish either).
        self.built('0.1.2')
        git(self.repo, 'remote', 'set-url', 'origin', str(self.origin) + '-gone')
        self.assertIsNone(update.on_google_play('0.1.2'))
        self.assertEqual(update.android_build_reason([]), '')


class BuildTimeout(unittest.TestCase):
    def test_a_build_that_hangs_is_ended_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'helpers').mkdir()
            marker = repo / 'child.pid'
            script = repo / 'helpers' / 'build_android.sh'
            # A build that never ends, with a child of its own (Gradle).
            script.write_text('#!/bin/sh\nsleep 300 &\necho $! > "' + str(marker) + '"\nwait\n')
            script.chmod(0o755)
            with mock.patch.object(update, 'REPO', repo), mock.patch.object(update, 'ANDROID_TIMEOUT_S', 1):
                started = time.monotonic()
                self.assertIsNone(update.build_android())
            self.assertLess(time.monotonic() - started, 30)
            child = int(marker.read_text())
            for _ in range(50):
                if not Path('/proc/' + str(child)).exists() or \
                        Path('/proc/' + str(child) + '/stat').read_text().split()[2] == 'Z':
                    break
                time.sleep(0.1)
            else:
                self.fail('the child of the build is still running')

    def test_the_answer_of_the_build(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'helpers').mkdir()
            script = repo / 'helpers' / 'build_android.sh'
            script.write_text('#!/bin/sh\nexit 3\n')
            script.chmod(0o755)
            with mock.patch.object(update, 'REPO', repo):
                self.assertEqual(update.build_android(), 3)


class Order(unittest.TestCase):
    def test_the_site_is_restarted_before_the_app_is_built(self):
        steps = []

        def run(command, **options):
            if command[:2] == ['systemctl', 'restart']:
                steps.append('restart')
            return subprocess.CompletedProcess(command, 0, '', '')

        answers = {('rev-parse', '--is-inside-work-tree'): 'true', ('branch', '--show-current'): 'main',
                   ('status', '--porcelain', '--untracked-files=no'): ''}

        def fake_git(*arguments, check=True):
            if arguments[0] == 'rev-parse' and arguments[1:] == ('HEAD',):
                return 'a' * 40 if 'pulled' not in steps else 'b' * 40
            if arguments[0] == 'pull':
                steps.append('pulled')
                return ''
            if arguments[:2] == ('diff', '--name-only'):
                return 'app/android/x.kt'
            return answers.get(arguments, '')

        with mock.patch.object(update, 'load_env'), mock.patch.object(update, 'uv_executable', return_value='uv'), \
                mock.patch.object(update, 'busy_jobs', return_value=[]), \
                mock.patch.object(update, 'git', side_effect=fake_git), \
                mock.patch.object(update, 'service_installed', return_value=True), \
                mock.patch.object(update.subprocess, 'run', side_effect=run), \
                mock.patch.object(update.time, 'sleep'), \
                mock.patch.object(update, 'build_android', side_effect=lambda: steps.append('build') or 0), \
                mock.patch.object(update.sys, 'argv', ['update']), mock.patch('builtins.print'):
            self.assertEqual(update.main(), 0)
        self.assertEqual(steps, ['pulled', 'restart', 'build'])


if __name__ == '__main__':
    unittest.main()
