"""perception.association: when the single radar target may label a detection, and when not."""
import unittest

from perception.association import AssociationConfig, Detection, associate
from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample

CONFIG = AssociationConfig(radar_to_reference_m=1.5)
RADAR = OemLeadSample(t_ns=1, distance_m=28.5, relative_speed_mps=-1.0)  # x = 30 m in the calibrated frame


def detection(object_id, category, x, y=0.0, score=0.9):
    return Detection(object_id=object_id, category=category, score=score, position_m=(x, y, 0.0))


class Associated(unittest.TestCase):
    def test_nearest_vehicle_in_the_path(self):
        result = associate([detection(1, 'car', 31.0), detection(2, 'truck', 60.0), detection(3, 'car', 29.0, y=4.0)],
                           RADAR, True, CONFIG)
        self.assertTrue(result.associated)
        self.assertEqual(result.object_id, 1)
        self.assertEqual(result.label_x_m, 30.0)
        self.assertEqual(result.vision_x_m, 31.0)
        self.assertIs(result.provenance, Provenance.OEM_RADAR)

    def test_pedestrian_beyond_the_radar_distance_does_not_block(self):
        result = associate([detection(1, 'car', 30.5), detection(2, 'pedestrian', 70.0)], RADAR, True, CONFIG)
        self.assertEqual(result.object_id, 1)


class NotAssociated(unittest.TestCase):
    def check(self, detections, reason, radar=RADAR, calibrated=True):
        result = associate(detections, radar, calibrated, CONFIG)
        self.assertFalse(result.associated)
        self.assertIsNone(result.object_id)
        self.assertEqual(result.reason, reason)
        return result

    def test_never_to_a_pedestrian_or_a_bicycle(self):
        self.check([detection(1, 'pedestrian', 30.0)], 'non-vehicle in the path near the radar distance')
        self.check([detection(1, 'bicycle', 30.0)], 'non-vehicle in the path near the radar distance')

    def test_non_vehicle_in_front_of_the_car(self):
        self.check([detection(1, 'bicycle', 20.0), detection(2, 'car', 30.0)],
                   'non-vehicle in the path near the radar distance')

    def test_vehicle_outside_the_path(self):
        self.check([detection(1, 'car', 30.0, y=3.5)], 'no vehicle in the path')

    def test_low_confidence(self):
        self.check([detection(1, 'car', 30.0, score=0.2)], 'no vehicle in the path')

    def test_two_vehicles_too_close(self):
        self.check([detection(1, 'car', 30.0), detection(2, 'car', 33.0, y=1.0)],
                   'two vehicles in the path too close to tell apart')

    def test_outside_the_gate(self):
        result = self.check([detection(1, 'car', 45.0)], 'nearest vehicle outside the radar gate')
        self.assertEqual(result.vision_x_m, 45.0)

    def test_no_calibration_or_no_radar(self):
        self.check([detection(1, 'car', 30.0)], 'no calibration', calibrated=False)
        self.check([detection(1, 'car', 30.0)], 'no radar measurement', radar=None)


if __name__ == '__main__':
    unittest.main()
