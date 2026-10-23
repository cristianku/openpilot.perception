"""web.radar and its pages: drives and recordings with radar, one drive as distances from the comma, the
calibration tests and the calibration they give. Synthetic series only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web.common import WebError
from web.radar import RadarIndex, RadarTests, drive_detail, frame_for, route_of, segment_folders, segment_number

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

    def test_detail(self):
        detail = drive_detail(self.repo, self.folders, '00000134--a5c8197883', CALIBRATION)
        self.assertEqual(detail['segments'][0]['standstills'][0]['reading_m'], 2.25)
        self.assertEqual(detail['chart']['from_comma_m'][0], 2.86)
        with self.assertRaises(WebError):
            drive_detail(self.repo, self.folders, 'ffffffff--ffffffffff', CALIBRATION)

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


if __name__ == '__main__':
    unittest.main()
