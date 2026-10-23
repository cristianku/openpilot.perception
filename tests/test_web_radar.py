"""web.radar and its pages: drives and recordings with radar, one drive as distances from the comma, the
calibration tests and the calibration they give. Synthetic series only."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import web.pictures
import web.radar
from web.common import WebError
from web.pictures import picture
from web.radar import (RadarIndex, RadarTests, drive_detail, frame_for, longest_still, route_of, segment_folders,
                       segment_number)

CALIBRATION = {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.89, 'radar_to_reference_m': 0.61}


def write_series(repo: Path, name: str, *, has_radar=True, readings=None, standstills=None, start=None,
                 start_from=None, duration=20.0) -> None:
    readings = readings if readings is not None else [2.25] * 200
    series = {
        'segment': name, 'commit': '1b6a472a', 'radar_in_schema': True, 'has_radar': has_radar,
        'start_unix': start, 'start_from': start_from, 'duration_s': duration,
        'measurements': len([r for r in readings if r is not None]) if has_radar else 0,
        'target_share': 0.66 if has_radar else 0.0, 'relative_speed_valid': 0,
        'reading_m': {'min': 2.25, 'median': 2.25, 'max': 2.5} if has_radar else None,
        'standstills': standstills if standstills is not None else (
            [{'from_s': 2.5, 'to_s': 20.0, 'reading_m': 2.25, 'target_share': 0.67, 'steady_share': 1.0}]
            if has_radar else []),
        'series': {'hz': 10, 't_s': [i / 10 for i in range(len(readings))], 'reading_m': readings,
                   'speed_mps': [0.0] * len(readings)},
        'frames': {'count': 3, 't_s': [0.04, 0.09, 0.14]}, 'video': 'qcamera.ts', 'stamp': {}}
    path = repo / 'outputs' / 'radar' / (name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(series))


class Names(unittest.TestCase):
    def test_routes_and_numbers(self):
        self.assertEqual(route_of('0000012e7dcc3fee2550'), '0000012e7dcc3fee25')
        self.assertEqual(segment_number('0000012e7dcc3fee2550'), 50)
        self.assertEqual(route_of('00000134--a5c8197883--0'), '00000134--a5c8197883')
        self.assertEqual(segment_number('00000134--a5c8197883--3'), 3)


class Drives(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / 'repo'
        self.drives = Path(self.directory.name) / 'data'
        for name in ('0000012e7dcc3fee2500', '0000012e7dcc3fee2501'):
            (self.drives / name).mkdir(parents=True)
            (self.drives / name / 'rlog.zst').write_bytes(b'log')
        for name in ('00000133--43b9cd9c08--0', '00000134--a5c8197883--0'):
            folder = self.repo / 'outputs' / 'recordings' / name
            folder.mkdir(parents=True)
            (folder / 'rlog.zst').write_bytes(b'log')
        write_series(self.repo, '0000012e7dcc3fee2500', has_radar=False, start=1_792_770_000.0, start_from='gps',
                     duration=60.0)
        write_series(self.repo, '0000012e7dcc3fee2501', has_radar=False, start=1_792_770_061.0, start_from='gps',
                     duration=60.0)
        write_series(self.repo, '00000133--43b9cd9c08--0', standstills=[], start=1_785_251_441.0,
                     start_from='comma clock')
        write_series(self.repo, '00000134--a5c8197883--0', start=1_785_251_511.0, start_from='comma clock')
        self.folders = segment_folders(self.repo, self.drives)

    def test_summary_newest_recording_first_then_drives(self):
        summary = RadarIndex(self.repo).drives(self.folders, CALIBRATION)
        self.assertEqual([d['route'] for d in summary['drives']],
                         ['00000134--a5c8197883', '00000133--43b9cd9c08', '0000012e7dcc3fee25'])
        recording = summary['drives'][0]
        self.assertEqual((recording['kind'], recording['has_radar'], recording['standstills']), ('recording', True, 1))
        self.assertEqual(recording['minimum_from_comma_m'], 2.86)
        drive = summary['drives'][2]
        self.assertEqual((drive['kind'], drive['segments'], drive['has_radar']), ('drive', 2, False))
        self.assertEqual(drive['start_unix'], 1_792_770_000.0)   # the segment 0 start, by GPS
        self.assertEqual(summary['with_radar'], 2)

    def test_summary_follows_the_calibration(self):
        index = RadarIndex(self.repo)
        self.assertEqual(index.drives(self.folders, CALIBRATION)['drives'][0]['minimum_from_comma_m'], 2.86)
        none = {'radar_to_camera_m': None, 'radar_correction_m': 0.0, 'radar_to_reference_m': None}
        self.assertIsNone(index.drives(self.folders, none)['drives'][0]['minimum_from_comma_m'])

    def test_summary_is_kept_until_something_changes(self):
        # GET /api/status asks for it every 4 s of every open tab: the series are read once, not every time.
        index = RadarIndex(self.repo)
        first = index.drives(self.folders, CALIBRATION)
        with mock.patch.object(web.radar, 'load_series', wraps=web.radar.load_series) as reads:
            self.assertIs(index.drives(self.folders, CALIBRATION), first)
            self.assertEqual(reads.call_count, 0)
            # Another calibration, or a series written since: read again.
            index.drives(self.folders, dict(CALIBRATION, radar_to_reference_m=0.5))
            self.assertEqual(reads.call_count, len(self.folders))
            write_series(self.repo, '00000135--0123456789--0')
            index.drives(self.folders, CALIBRATION)
            self.assertEqual(reads.call_count, 2 * len(self.folders))

    def test_detail(self):
        detail = drive_detail(self.repo, self.folders, '00000134--a5c8197883', CALIBRATION)
        self.assertEqual(detail['segments'][0]['standstills'][0]['reading_m'], 2.25)
        self.assertEqual(detail['segments'][0]['longest_still_s'], 19.9)
        self.assertEqual(detail['chart']['from_comma_m'][0], 2.86)
        with self.assertRaises(WebError):
            drive_detail(self.repo, self.folders, 'ffffffff--ffffffffff', CALIBRATION)

    def test_longest_still(self):
        # Moving, still 1.4 s, moving: never the 2 s a standstill needs.
        times = [i / 10 for i in range(40)]
        speeds = [3.0] * 10 + [0.0] * 15 + [3.0] * 15
        self.assertEqual(longest_still(times, speeds), 1.4)
        self.assertEqual(longest_still(times, [0.0] * 40), 3.9)
        self.assertEqual(longest_still([], []), 0.0)

    def test_frame_for(self):
        series = {'frames': {'t_s': [0.04, 0.09, 0.14]}}
        self.assertEqual(frame_for(series, 0.1), (1, 0.05))
        self.assertIsNone(frame_for({'frames': {'t_s': []}}, 1.0))


class Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tests = RadarTests(Path(self.directory.name))
        self.series = {'00000134--a5c8197883--0': {'standstills': [{'from_s': 2.5, 'to_s': 20.0, 'reading_m': 2.25}]},
                       '00000132--d64642fa1f--0': {'standstills': [{'from_s': 1.0, 'to_s': 16.0, 'reading_m': 1.75}]}}

    def add(self, segment, radar, comma):
        return self.tests.add({'segment': segment, 'standstill': 0, 'tape_radar_m': radar, 'tape_comma_m': comma},
                              self.series.get)

    def test_the_two_wall_tests(self):
        self.add('00000134--a5c8197883--0', 1.32, 2.82)
        self.add('00000132--d64642fa1f--0', 0.90, 2.40)
        summary = self.tests.summary()
        self.assertEqual((summary['kept'], summary['correction_m'], summary['radar_to_camera_m']), (2, -0.89, 1.5))
        self.assertEqual(summary['spread_m'], 0.08)
        self.assertEqual((summary['tested_from_m'], summary['tested_to_m']), (0.9, 1.32))
        first = summary['tests'][0]['id']
        self.tests.change(first, {'kept': False})
        self.assertEqual(self.tests.summary()['correction_m'], -0.85)
        self.tests.change(first, {'delete': True})
        self.assertEqual(len(self.tests.summary()['tests']), 1)

    def test_refused(self):
        for body in ({'segment': '00000134--a5c8197883--0', 'standstill': 3, 'tape_radar_m': 1.3, 'tape_comma_m': 2.8},
                     {'segment': '00000134--a5c8197883--0', 'standstill': 0, 'tape_radar_m': 2.8, 'tape_comma_m': 1.3},
                     {'segment': '00000134--a5c8197883--0', 'standstill': 0, 'tape_radar_m': 0.1, 'tape_comma_m': 1.3},
                     {'segment': '../etc', 'standstill': 0, 'tape_radar_m': 1.3, 'tape_comma_m': 2.8},
                     {'segment': '00000131--65b1397d4b--0', 'standstill': 0, 'tape_radar_m': 1.3, 'tape_comma_m': 2.8},
                     {'standstill': 0}):
            with self.assertRaises(WebError):
                self.tests.add(body, self.series.get)
        with self.assertRaises(WebError):
            self.tests.change('nothere00000', {'kept': True})
        self.assertEqual(self.tests.summary()['kept'], 0)


class Pictures(unittest.TestCase):
    """web.pictures: a kept picture is served as it is; ffmpeg runs for at most DECODERS frames at once."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / '0000011e0c8825628a01'
        self.folder.mkdir()
        (self.folder / 'fcamera.hevc').write_bytes(b'not a video')
        self.cache = Path(self.directory.name) / 'pictures'

    def test_kept_picture_without_ffmpeg(self):
        self.cache.mkdir()
        (self.cache / '0000011e0c8825628a01-0007.jpg').write_bytes(b'\xff\xd8kept')
        with mock.patch.object(web.pictures.subprocess, 'run') as run:
            self.assertEqual(picture(self.folder, 7, None, self.cache), b'\xff\xd8kept')
        run.assert_not_called()

    def test_busy_when_every_decoder_is_taken(self):
        taken = threading.BoundedSemaphore(1)
        taken.acquire()
        with mock.patch.object(web.pictures, '_decoders', taken), mock.patch.object(web.pictures, 'WAIT_S', 0.01), \
                mock.patch.object(web.pictures.subprocess, 'run') as run:
            with self.assertRaises(WebError) as busy:
                picture(self.folder, 8, None, self.cache)
        self.assertEqual((busy.exception.status, busy.exception.reason), (503, 'busy'))
        run.assert_not_called()

    def test_made_once_and_kept(self):
        made = mock.Mock(returncode=0, stdout=b'\xff\xd8made')
        with mock.patch.object(web.pictures.subprocess, 'run', return_value=made) as run:
            self.assertEqual(picture(self.folder, 9, None, self.cache), b'\xff\xd8made')
            self.assertEqual(picture(self.folder, 9, None, self.cache), b'\xff\xd8made')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(sorted(path.name for path in self.cache.iterdir()), ['0000011e0c8825628a01-0009.jpg'])
        # Every decoder is free again.
        self.assertTrue(all(web.pictures._decoders.acquire(blocking=False) for _ in range(web.pictures.DECODERS)))
        for _ in range(web.pictures.DECODERS):
            web.pictures._decoders.release()


if __name__ == '__main__':
    unittest.main()
