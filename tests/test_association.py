"""perception.association: the radar labels only the vehicle straight ahead in the ego lane."""
import unittest

from perception.association import AssociationConfig, Detection, associate, lane_centre_y, path_curvature
from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample

CONFIG = AssociationConfig(radar_to_reference_m=1.5)
RADAR = OemLeadSample(t_ns=1, distance_m=28.5, relative_speed_mps=-1.0)  # x = 30 m in the calibrated frame
STRAIGHT = 0.0


def detection(object_id, category, x, y=0.0, score=0.9):
    return Detection(object_id=object_id, category=category, score=score, position_m=(x, y, 0.0))


class Associated(unittest.TestCase):
    def test_nearest_vehicle_in_the_lane(self):
        result = associate([detection(1, 'car', 31.0), detection(2, 'truck', 60.0),
                            detection(3, 'car', 80.0, y=3.5)], RADAR, True, STRAIGHT, CONFIG)
        self.assertTrue(result.associated)
        self.assertEqual(result.object_id, 1)
        self.assertEqual(result.label_x_m, 30.0)
        self.assertEqual(result.vision_x_m, 31.0)
        self.assertIs(result.provenance, Provenance.OEM_RADAR)

    def test_far_objects_do_not_block(self):
        result = associate([detection(1, 'car', 30.5), detection(2, 'pedestrian', 70.0),
                            detection(3, 'car', 60.0, y=-3.5)], RADAR, True, STRAIGHT, CONFIG)
        self.assertEqual(result.object_id, 1)

    def test_lane_follows_a_gentle_curve(self):
        curvature = 0.002                                 # right curve, R = 500 m: lane centre 0.9 m right at 30 m
        result = associate([detection(1, 'car', 30.0, y=1.0)], RADAR, True, curvature, CONFIG)
        self.assertEqual(result.object_id, 1)


class NotAssociated(unittest.TestCase):
    def check(self, detections, reason, radar=RADAR, calibrated=True, curvature=STRAIGHT):
        result = associate(detections, radar, calibrated, curvature, CONFIG)
        self.assertFalse(result.associated)
        self.assertIsNone(result.object_id)
        self.assertEqual(result.reason, reason)
        return result

    def test_never_to_a_pedestrian_or_a_bicycle(self):
        self.check([detection(1, 'pedestrian', 30.0)], 'non-vehicle near the radar distance')
        self.check([detection(1, 'bicycle', 30.0)], 'non-vehicle near the radar distance')

    def test_non_vehicle_in_front_of_the_car(self):
        self.check([detection(1, 'bicycle', 20.0), detection(2, 'car', 30.0)], 'non-vehicle near the radar distance')

    def test_cyclist_at_the_lane_edge(self):
        self.check([detection(1, 'bicycle', 29.0, y=2.2), detection(2, 'car', 30.0)],
                   'non-vehicle near the radar distance')

    def test_never_to_a_car_in_another_lane(self):
        self.check([detection(1, 'car', 30.0, y=3.5)], 'vehicle in the next lane near the radar distance')
        self.check([detection(1, 'car', 30.0, y=-3.4)], 'vehicle in the next lane near the radar distance')

    def test_neighbour_near_the_radar_distance_blocks_the_lane_car(self):
        # the radar could be following the neighbour (a car cutting in), not the car in the lane
        self.check([detection(1, 'car', 33.0), detection(2, 'car', 29.0, y=3.0)],
                   'vehicle in the next lane near the radar distance')

    def test_on_a_curve_the_straight_ahead_car_is_in_the_next_lane(self):
        curvature = 0.003                                 # right curve: lane centre 0.94 m right at 25 m
        radar = OemLeadSample(t_ns=1, distance_m=23.5, relative_speed_mps=None)
        self.assertAlmostEqual(lane_centre_y(curvature, 25.0), 0.9375)
        self.check([detection(1, 'car', 25.0, y=-1.0)], 'vehicle in the next lane near the radar distance',
                   radar=radar, curvature=curvature)

    def test_road_ahead_curving(self):
        self.check([detection(1, 'car', 30.0, y=4.5)], 'road ahead curving', curvature=0.01)

    def test_no_car_motion(self):
        self.check([detection(1, 'car', 30.0)], 'no car motion', curvature=None)

    def test_low_confidence(self):
        self.check([detection(1, 'car', 30.0, score=0.2)], 'no vehicle in the lane')

    def test_two_vehicles_too_close(self):
        self.check([detection(1, 'car', 30.0), detection(2, 'car', 33.0, y=1.0)],
                   'two vehicles in the lane too close to tell apart')

    def test_outside_the_gate(self):
        result = self.check([detection(1, 'car', 45.0)], 'nearest vehicle outside the radar gate')
        self.assertEqual(result.vision_x_m, 45.0)

    def test_no_calibration_or_no_radar(self):
        self.check([detection(1, 'car', 30.0)], 'no calibration', calibrated=False)
        self.check([detection(1, 'car', 30.0)], 'no radar measurement', radar=None)


class Curvature(unittest.TestCase):
    def test_sign_and_low_speed(self):
        self.assertAlmostEqual(path_curvature(20.0, 0.04), 0.002)     # turning right
        self.assertAlmostEqual(path_curvature(20.0, -0.04), -0.002)
        self.assertAlmostEqual(path_curvature(0.5, 0.03), 0.01)       # divided by 3 m/s, not 0.5
        self.assertGreater(lane_centre_y(0.002, 30.0), 0.0)


if __name__ == '__main__':
    unittest.main()
