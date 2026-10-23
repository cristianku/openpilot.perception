"""perception.dataset: frames with and without radar, misaligned times, depth targets."""
import unittest

import numpy as np

from fakes import MS, calibration, car_state, encode_idx, schemas
from perception.association import AssociationConfig, Detection
from perception.dataset import SegmentAdapter, depth_targets
from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample
from perception.sources.comma_log import read_events

S = 1_000_000_000
CONFIG = AssociationConfig(radar_to_reference_m=1.5)


def frames(n, start=S, period=50 * MS):
    """Frames of fcamera; frame time = timestampEof - 8 ms."""
    return [encode_idx('narrowRoadEncodeIdx', i, start + i * period + 8 * MS) for i in range(n)]


def radar(struct, times):
    return [car_state(struct, t + 3 * MS, stockRadarLeadValid=True, stockRadarLeadDistance=28.5,
                      stockRadarMeasurementTimeNanos=t, stockRadarRelativeSpeedValid=True,
                      stockRadarLeadRelativeSpeed=0.5) for t in times]


class Adapter(unittest.TestCase):
    def setUp(self):
        self.s = schemas()

    def test_vision_only_without_the_schema(self):
        events = [calibration(S - S // 2), *frames(4), car_state(self.s.CarStateOld, S)]
        adapter = SegmentAdapter(read_events(events))
        self.assertEqual(len(adapter), 4)
        self.assertFalse(adapter.radar.available)
        self.assertTrue(all(s.radar is None and s.calibration is not None for s in adapter.samples))
        self.assertIn('vision-only', adapter.samples[0].radar_status)

    def test_radar_aligned_to_frames(self):
        # radar every 20 ms from S, but a gap between 60 and 140 ms
        times = [S + t * MS for t in (0, 20, 40, 60, 140, 160)]
        events = [calibration(S - S // 2), *frames(4), *radar(self.s.CarStateNew, times)]
        adapter = SegmentAdapter(read_events(events))
        statuses = [s.radar_status for s in adapter.samples]
        self.assertEqual(statuses, ['aligned', 'aligned', 'no radar measurement within 25 ms', 'aligned'])
        # frame 3 (150 ms) is 10 ms from both 140 and 160: the earlier measurement wins
        self.assertEqual([s.radar_skew_ns for s in adapter.samples], [0, 10 * MS, None, 10 * MS])

    def test_load_image_is_the_callers(self):
        events = [*frames(1)]
        adapter = SegmentAdapter(read_events(events), load_image=lambda frame: np.zeros((2, 2, 3), np.uint8))
        self.assertEqual(adapter[0]['image'].shape, (2, 2, 3))
        self.assertNotIn('image', SegmentAdapter(read_events(events))[0])

    def test_unknown_camera(self):
        with self.assertRaises(ValueError):
            SegmentAdapter(read_events([]), camera='rearcam')


class DepthTargets(unittest.TestCase):
    def setUp(self):
        # frame 0 at S, before the first calibration (S + 1 s); frame 1 at S + 2 s, calibrated
        events = [encode_idx('narrowRoadEncodeIdx', 0, S + 8 * MS), calibration(S + S),
                  encode_idx('narrowRoadEncodeIdx', 1, S + 2 * S + 8 * MS),
                  *radar(schemas().CarStateNew, [S, S + 2 * S])]
        self.adapter = SegmentAdapter(read_events(events))
        self.detections = [Detection(1, 'pedestrian', 0.9, (12.0, 3.0, 0.0)),  # on the pavement
                           Detection(2, 'car', 0.9, (31.0, 0.2, 0.0)),
                           Detection(3, 'car', 0.9, (55.0, 0.1, 0.0))]

    def test_only_the_paired_detection_has_a_target(self):
        sample = self.adapter.samples[1]
        self.assertIsNotNone(sample.calibration)
        target, valid, association = depth_targets(sample, self.detections, CONFIG)
        self.assertEqual(valid.tolist(), [False, True, False])
        self.assertEqual(target[1], 30.0)
        self.assertTrue(np.isnan(target[0]) and np.isnan(target[2]))
        self.assertEqual(association.object_id, 2)

    def test_no_target_before_calibration(self):
        sample = self.adapter.samples[0]
        self.assertIsNone(sample.calibration)
        target, valid, association = depth_targets(sample, self.detections, CONFIG)
        self.assertFalse(valid.any())
        self.assertEqual(association.reason, 'no calibration')

    def test_synthetic_radar_is_not_a_label_unless_asked(self):
        sample = self.adapter.samples[1]
        synthetic = OemLeadSample(t_ns=sample.radar.t_ns, distance_m=28.5, relative_speed_mps=None,
                                  provenance=Provenance.SYNTHETIC)
        sample = type(sample)(sample.frame, sample.calibration, synthetic, 0, 'aligned')
        self.assertFalse(depth_targets(sample, self.detections, CONFIG)[1].any())
        self.assertTrue(depth_targets(sample, self.detections, CONFIG, allow_synthetic=True)[1][1])


if __name__ == '__main__':
    unittest.main()
