"""web.comma: the comma's recordings in route-counter order, the address, the copy of a short recording.
ssh and scp are faked: nothing here talks to a device."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from web.comma import Comma, comma_host, parse_listing
from web.common import WebError

LISTING = """0000012e--7dcc3fee25--0 9500000 300000
0000012e--7dcc3fee25--1 9400000 300000
00000134--a5c8197883--0 2355147 180000
00000133--43b9cd9c08--0 3461800 0
boot 0 0
not-a-recording 1 1
"""


class Listing(unittest.TestCase):
    def test_newest_counter_first_whatever_the_clock(self):
        recordings = parse_listing(LISTING)
        self.assertEqual([r['route'] for r in recordings],
                         ['00000134--a5c8197883', '00000133--43b9cd9c08', '0000012e--7dcc3fee25'])
        self.assertEqual(recordings[2]['segments'], [0, 1])
        self.assertTrue(recordings[0]['short'] and recordings[0]['has_video'])
        self.assertFalse(recordings[1]['has_video'])


class Host(unittest.TestCase):
    def test_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, distill = Path(directory) / 'p', Path(directory) / 'd'
            repo.mkdir()
            distill.mkdir()
            self.assertEqual(comma_host(repo, distill, environ={}), 'comma')
            (distill / '.env').write_text('COMMA_HOST=192.168.1.20\n')
            self.assertEqual(comma_host(repo, distill, environ={}), '192.168.1.20')
            (repo / '.env').write_text('PERCEPTION_COMMA_HOST=comma@car.local\n')
            self.assertEqual(comma_host(repo, distill, environ={}), 'comma@car.local')
            self.assertEqual(comma_host(repo, distill, environ={'PERCEPTION_COMMA_HOST': '-oProxyCommand=x'}),
                             'comma@car.local')  # never an ssh option

    def test_invalid_host_refused(self):
        with self.assertRaises(WebError):
            Comma('-oProxyCommand=evil')


class Fetch(unittest.TestCase):
    def test_copy_and_missing(self):
        calls = []

        def runner(argv, **options):
            calls.append(argv)
            if argv[0] == 'scp':
                if argv[-2].endswith('qcamera.ts'):
                    return subprocess.CompletedProcess(argv, 1, '', 'no such file')
                Path(argv[-1]).write_bytes(b'log')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with tempfile.TemporaryDirectory() as directory:
            copied = Comma('comma', runner=runner).fetch('00000134--a5c8197883--0', Path(directory))
            self.assertEqual(copied, ['rlog.zst'])
            folder = Path(directory) / '00000134--a5c8197883--0'
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['rlog.zst'])
            self.assertTrue(all('BatchMode=yes' in argv for argv in calls))
            with self.assertRaises(WebError):
                Comma('comma', runner=runner).fetch('../../etc', Path(directory))

    def test_unreachable(self):
        def runner(argv, **options):
            raise subprocess.TimeoutExpired(argv, 10)

        with self.assertRaises(WebError) as refused:
            Comma('comma', runner=runner).recordings()
        self.assertEqual(refused.exception.status, 503)


if __name__ == '__main__':
    unittest.main()
