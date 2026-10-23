"""perception.losses, perception.metrics, perception.viz and perception.geometry."""
import math
import unittest

import numpy as np

from perception.association import Association, Detection
from perception.metrics import DistanceErrors, band_of
from perception.provenance import Provenance
from perception.radar.oem_lead import OemLeadSample
from perception.viz import object_rows, table

try:
    import torch

    from perception.losses import masked_depth_loss
except ImportError:
    torch = None


@unittest.skipIf(torch is None, 'torch not installed')
class Loss(unittest.TestCase):
    def test_only_valid_targets_count(self):
        predicted = torch.tensor([10.0, 20.0, 40.0], requires_grad=True)
        target = torch.tensor([11.0, math.nan, 30.0])
        valid = torch.tensor([True, True, False])  # the NaN target is dropped even if marked valid
        loss = masked_depth_loss(predicted, target, valid, kind='l1')
        self.assertAlmostEqual(loss.item(), 1.0)
        loss.backward()
        self.assertEqual(predicted.grad.tolist(), [-1.0, 0.0, 0.0])

    def test_no_valid_target_is_zero_with_a_gradient_path(self):
        predicted = torch.tensor([10.0, math.nan], requires_grad=True)
        loss = masked_depth_loss(predicted, torch.tensor([math.nan, 5.0]), torch.tensor([False, False]))
        self.assertEqual(loss.item(), 0.0)
        loss.backward()
        self.assertEqual(predicted.grad.tolist(), [0.0, 0.0])

    def test_kinds(self):
        predicted, target, valid = torch.tensor([20.0]), torch.tensor([10.0]), torch.tensor([True])
        self.assertAlmostEqual(masked_depth_loss(predicted, target, valid).item(), math.log(2.0), places=6)
        self.assertAlmostEqual(masked_depth_loss(predicted, target, valid, kind='smooth_l1').item(), 9.5)
        with self.assertRaises(ValueError):
            masked_depth_loss(predicted, target, valid, kind='l2')


class Metrics(unittest.TestCase):
    def test_only_associated_genuine_pairs_are_the_headline(self):
        errors = DistanceErrors()
        self.assertTrue(errors.add(32.0, 30.0, Provenance.OEM_RADAR, associated=True))
        self.assertTrue(errors.add(18.0, 20.0, Provenance.OEM_RADAR, associated=True))
        self.assertFalse(errors.add(99.0, 30.0, Provenance.OEM_RADAR, associated=False))
        self.assertFalse(errors.add(30.0, math.nan, Provenance.OEM_RADAR, associated=True))  # no label
        self.assertTrue(errors.add(50.0, 10.0, Provenance.SYNTHETIC, associated=True))
        measured = errors.measured()
        self.assertEqual((measured['eligible'], measured['predicted'], measured['missing']), (2, 2, 0))
        self.assertAlmostEqual(measured['mae_m'], 2.0)
        self.assertAlmostEqual(measured['absrel'], (2 / 30 + 2 / 20) / 2)
        self.assertAlmostEqual(measured['rmse_m'], 2.0)
        self.assertEqual(errors.summary()['synthetic']['eligible'], 1)
        self.assertIn('distance/mae_m', errors.scalars())
        self.assertIn('distance/10-30 m/within_2m', errors.scalars())

    def test_a_missing_prediction_is_a_failure_not_dropped(self):
        # review 2, I2: one good and one NaN prediction on two eligible labels
        errors = DistanceErrors()
        errors.add(31.0, 30.0, Provenance.OEM_RADAR, associated=True)
        errors.add(math.nan, 25.0, Provenance.OEM_RADAR, associated=True)
        errors.add(None, 40.0, Provenance.OEM_RADAR, associated=True)
        measured = errors.measured()
        self.assertEqual((measured['eligible'], measured['predicted'], measured['missing']), (3, 1, 2))
        self.assertAlmostEqual(measured['mae_m'], 1.0)            # over the predicted one
        self.assertAlmostEqual(measured['within_1m'], 1 / 3)      # over all the eligible labels
        self.assertAlmostEqual(measured['within_5m'], 1 / 3)

    def test_disjoint_bands(self):
        self.assertEqual([band_of(x) for x in (1.9, 2.0, 9.99, 10.0, 59.9, 60.0, 80.0, 80.1)],
                         ['out of range', '2-10 m', '2-10 m', '10-30 m', '30-60 m', '60-80 m', '60-80 m',
                          'out of range'])
        errors = DistanceErrors()
        errors.add(9.0, 8.0, Provenance.OEM_RADAR, associated=True)
        errors.add(70.0, 65.0, Provenance.OEM_RADAR, associated=True)
        bands = errors.measured()['bands']
        self.assertEqual(set(bands), {'2-10 m', '60-80 m'})
        self.assertAlmostEqual(bands['60-80 m']['mae_m'], 5.0)

    def test_synthetic_only_has_no_headline(self):
        errors = DistanceErrors()
        errors.add(50.0, 10.0, Provenance.SYNTHETIC, associated=True)
        self.assertIsNone(errors.measured())
        self.assertEqual(errors.scalars(), {})


class Visualisation(unittest.TestCase):
    def setUp(self):
        self.detections = [Detection(1, 'car', 0.9, (31.0, 0.2, 0.0), box=(100, 80, 160, 130)),
                           Detection(2, 'pedestrian', 0.9, (12.0, 3.0, 0.0), box=(20, 60, 40, 120))]

    def test_rows(self):
        radar = OemLeadSample(1, 28.5, None)
        rows = object_rows(self.detections, Association('associated', 1, radar, 30.0, 31.0))
        car, pedestrian = rows
        self.assertEqual((car.radar_m, car.difference_m, car.valid), (30.0, 1.0, True))
        self.assertIs(car.radar_source, Provenance.OEM_RADAR)
        self.assertIs(car.estimate_source, Provenance.VISION)
        self.assertEqual((pedestrian.radar_m, pedestrian.difference_m, pedestrian.valid), (None, None, False))
        self.assertEqual(pedestrian.note, 'not the radar target')
        self.assertNotIn('SYNTHETIC', table(rows))

    def test_without_association_every_row_says_why(self):
        rows = object_rows(self.detections, Association('no calibration'))
        self.assertTrue(all(row.note == 'radar: no calibration' and not row.valid for row in rows))

    def test_synthetic_data_is_labelled(self):
        radar = OemLeadSample(1, 28.5, None, provenance=Provenance.SYNTHETIC)
        rows = object_rows(self.detections, Association('associated', 1, radar, 30.0, 31.0))
        self.assertTrue(table(rows).startswith('SYNTHETIC TEST DATA'))
        try:
            from perception.viz import draw
            image = draw(np.zeros((160, 240, 3), np.uint8), self.detections, rows)
        except ImportError:
            self.skipTest('OpenCV not installed')
        self.assertEqual(image.shape, (160, 240, 3))
        self.assertGreater(int(image.sum()), 0)


class Geometry(unittest.TestCase):
    def setUp(self):
        try:
            from perception.geometry import calibrated_from_camera, to_calibrated
        except ImportError:
            self.skipTest('openpilot not installed')
        self.rotation, self.to_calibrated = calibrated_from_camera, to_calibrated

    def test_identity_calibration(self):
        try:
            rotation = self.rotation('fcamera', (0, 0, 0), (0, 0, 0))
        except ImportError:
            self.skipTest('openpilot not installed')
        np.testing.assert_allclose(rotation, np.eye(3), atol=1e-12)

    def test_yaw_moves_a_point_sideways(self):
        try:
            point = self.to_calibrated([30.0, 0.0, 0.0], 'fcamera', (0, 0, 0.05), (0, 0, 0))
            wide = self.to_calibrated([30.0, 0.0, 0.0], 'ecamera', (0, 0, 0.05), (0, 0, -0.05))
        except ImportError:
            self.skipTest('openpilot not installed')
        self.assertAlmostEqual(np.linalg.norm(point), 30.0)
        self.assertGreater(abs(point[1]), 1.0)
        np.testing.assert_allclose(wide, [30.0, 0.0, 0.0], atol=1e-9)  # the two yaws cancel out

    def test_unknown_camera(self):
        with self.assertRaises(ValueError):
            self.rotation('rearcam', (0, 0, 0), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
