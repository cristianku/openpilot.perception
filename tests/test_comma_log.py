"""perception.sources.comma_log on synthetic events."""
import math
import unittest

from fakes import MS, Event, calibration, car_state, encode_idx, schemas
from perception.sources import comma_log

S = 1_000_000_000


class Frames(unittest.TestCase):
    def test_frames_of_both_cameras(self):
        events = [
            encode_idx('narrowRoadEncodeIdx', 1, S + 50 * MS),
            encode_idx('narrowRoadEncodeIdx', 0, S),
            encode_idx('wideRoadEncodeIdx', 0, S + 1 * MS),
        ]
        log = comma_log.read_events(events)
        self.assertEqual([(f.index, f.t_ns) for f in log.frames['fcamera']], [(0, S - 8 * MS), (1, S + 42 * MS)])
        self.assertEqual([(f.camera, f.index, f.frame_id) for f in log.frames['ecamera']], [('ecamera', 0, 1000)])

    def test_unusable_frames_are_left_out(self):
        events = [
            encode_idx('narrowRoadEncodeIdx', 0, S, valid=False),
            encode_idx('narrowRoadEncodeIdx', 1, S + 50 * MS, kind='qcameraH264'),
            encode_idx('narrowRoadEncodeIdx', 2, 0),
            encode_idx('narrowRoadEncodeIdx', 3, S + 150 * MS),
        ]
        self.assertEqual([f.index for f in comma_log.read_events(events).frames['fcamera']], [3])


class Calibrations(unittest.TestCase):
    def test_calibration_in_force(self):
        events = [
            calibration(S, status='uncalibrated'),
            calibration(S + 1 * S, rpy=(0.0, 0.02, 0.01)),
            calibration(S + 2 * S, rpy=(0.0, math.nan, 0.0)),
            calibration(S + 3 * S, rpy=(0.0, 0.03, 0.01), height=()),
        ]
        log = comma_log.read_events(events)
        self.assertIsNone(log.calibration_at(S + S // 2))
        self.assertEqual(log.calibration_at(S + 2 * S).rpy_calib, (0.0, 0.02, 0.01))
        self.assertAlmostEqual(log.calibration_at(S + 2 * S).height_m, 1.22)
        latest = log.calibration_at(S + 10 * S)
        self.assertEqual(latest.rpy_calib, (0.0, 0.03, 0.01))
        self.assertIsNone(latest.height_m)


class CarStates(unittest.TestCase):
    def test_only_valid_car_states(self):
        new = schemas().CarStateNew
        valid = car_state(new, S)
        invalid = Event('carState', S + 10 * MS, new.new_message(), valid=False)
        log = comma_log.read_events([valid, invalid])
        self.assertEqual([t for t, _ in log.car_states], [S])


if __name__ == '__main__':
    unittest.main()
