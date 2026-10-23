"""perception.sources.rlog (pinned schema, log files) and perception.radar.series (the radar of a segment).
The logs are synthetic, built with the pinned schema itself; nothing here comes from a real drive."""
from __future__ import annotations

import bz2
import tempfile
import unittest
from pathlib import Path

import zstandard

from perception.radar.series import _standstills, from_comma, radar_series
from perception.sources import rlog

MS = 1_000_000


def schema_or_skip(test: unittest.TestCase):
    try:
        return rlog.load_schema()
    except rlog.SchemaError as exc:
        test.skipTest(f'log schema not available offline: {exc}')


class Schema(unittest.TestCase):
    def test_files_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(rlog.SchemaError):
                rlog.ensure_schema(Path(directory), fetch=lambda url: b'not the pinned file')
            with self.assertRaises(rlog.SchemaError):
                rlog.ensure_schema(Path(directory), fetch=lambda url: (_ for _ in ()).throw(OSError('offline')))

    def test_pinned_schema_has_the_radar_fields(self):
        schema = schema_or_skip(self)
        names = schema.Event.new_message().init('carState').schema.fieldnames
        for field in ('stockRadarLeadDistance', 'stockRadarLeadValid', 'stockRadarMeasurementTimeNanos'):
            self.assertIn(field, names)

    def test_decompress(self):
        data = b'\x00\x01 some capnp bytes'
        self.assertEqual(rlog.decompress(zstandard.ZstdCompressor().compress(data)), data)
        self.assertEqual(rlog.decompress(bz2.compress(data)), data)
        self.assertEqual(rlog.decompress(data), data)


class Series(unittest.TestCase):
    def write_log(self, directory: Path, events) -> Path:
        path = directory / '00000134--a5c8197883--0' / 'rlog.zst'
        path.parent.mkdir(parents=True)
        path.write_bytes(zstandard.ZstdCompressor().compress(b''.join(event.to_bytes() for event in events)))
        return path

    def test_a_wall_test(self):
        schema = schema_or_skip(self)
        events = []
        start = 1_000 * MS
        init = schema.Event.new_message(logMonoTime=start - 50 * MS, valid=True)
        init.init('initData').gitCommit = '1b6a472ae61d2d61866902cb48a5b3ab8bd11d07'
        init.initData.wallTimeNanos = 1_785_251_511 * 1_000_000_000
        events.append(init)
        gps = schema.Event.new_message(logMonoTime=start + 500 * MS, valid=True)
        fix = gps.init('gpsLocationExternal')
        fix.hasFix = True
        fix.unixTimestampMillis = 1_792_780_000_500
        events.append(gps)
        for index in range(1000):                       # 10 s of carState at 100 Hz
            t = start + index * 10 * MS
            event = schema.Event.new_message(logMonoTime=t, valid=True)
            car = event.init('carState')
            car.vEgo = 0.6 if index < 200 else 0.0          # creeping for 2 s, then standing still
            if index >= 50:                                 # a target from 0.5 s on, measured 10 ms before
                car.stockRadarLeadValid = True
                car.stockRadarLeadDistance = 2.25 if index >= 200 else 3.0
                car.stockRadarMeasurementTimeNanos = t - 10 * MS
            events.append(event)
        for index in range(200):                        # 10 s of road frames at 20 Hz
            frame = schema.Event.new_message(logMonoTime=start + index * 50 * MS, valid=True)
            frame.init('narrowRoadEncodeIdx').timestampEof = start + index * 50 * MS - 8 * MS
            events.append(frame)
        with tempfile.TemporaryDirectory() as directory:
            series = radar_series(self.write_log(Path(directory), events))
        self.assertEqual((series['segment'], series['commit'], series['has_radar']),
                         ('00000134--a5c8197883--0', '1b6a472a', True))
        self.assertEqual(series['start_from'], 'gps')
        self.assertAlmostEqual(series['start_unix'], 1_792_780_000.0, places=1)
        self.assertAlmostEqual(series['target_share'], 0.95, places=2)
        self.assertEqual(series['reading_m'], {'min': 2.25, 'median': 2.25, 'max': 3.0})
        self.assertEqual(series['frames']['count'], 200)
        self.assertEqual(series['series']['hz'], 10)
        self.assertEqual(len(series['standstills']), 1)
        still = series['standstills'][0]
        self.assertEqual(still['reading_m'], 2.25)
        self.assertGreaterEqual(still['to_s'] - still['from_s'], 7.5)
        self.assertEqual(series['relative_speed_valid'], 0)

    def test_an_old_drive_has_no_radar(self):
        schema = schema_or_skip(self)
        events = []
        for index in range(300):
            event = schema.Event.new_message(logMonoTime=(index + 1) * 10 * MS, valid=True)
            event.init('carState').vEgo = 10.0
            events.append(event)
        with tempfile.TemporaryDirectory() as directory:
            series = radar_series(self.write_log(Path(directory), events))
        self.assertFalse(series['has_radar'])
        self.assertIsNone(series['reading_m'])
        self.assertIsNone(series['start_from'])
        self.assertEqual(series['standstills'], [])


class Helpers(unittest.TestCase):
    def test_from_comma(self):
        self.assertAlmostEqual(from_comma(2.25, -0.89, 1.50), 2.86)

    def test_standstills_need_a_steady_reading(self):
        times = [index / 10 for index in range(50)]
        speeds = [0.0] * 50
        self.assertEqual(_standstills(times, speeds, [2.25] * 50)[0]['reading_m'], 2.25)
        # The reading jumps between steps: not steady enough to calibrate with.
        self.assertEqual(_standstills(times, speeds, [2.25, 2.5, 2.75, 3.0, 3.25] * 10), [])
        # Too short, or no target most of the time.
        self.assertEqual(_standstills(times[:15], speeds[:15], [2.25] * 15), [])
        self.assertEqual(_standstills(times, speeds, [2.25] * 10 + [None] * 40), [])


if __name__ == '__main__':
    unittest.main()
