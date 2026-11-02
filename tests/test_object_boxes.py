"""CPU-only tests: no detector checkpoint, radar, driving system, or GPU."""
import unittest

from perception.objects.boxes import Box2D, centre_crop, intersection_over_union, merge_boxes, project_boxes


class CropTests(unittest.TestCase):
    def test_native_crop_on_comma_frame(self):
        self.assertEqual(centre_crop(1344, 760), (352, 60, 640, 640))

    def test_edge_and_small_frame(self):
        self.assertEqual(centre_crop(1344, 760, centre=(0, 0)), (0, 0, 640, 640))
        self.assertEqual(centre_crop(320, 240), (40, 0, 240, 240))
        with self.assertRaises(ValueError):
            centre_crop(640, 480, centre=(float('nan'), 5))


class ProjectionTests(unittest.TestCase):
    def test_crop_offset_and_clipping(self):
        boxes = project_boxes([('person', .8, (-10, 25, 700, 100))], (1344, 760),
                              crop=(352, 60, 640, 640))
        self.assertEqual(boxes, [Box2D('pedestrian', .8, (352, 85, 992, 160), 'crop')])

    def test_full_frame_labels_and_unknown(self):
        boxes = project_boxes([('motorbike', .8, (2, 3, 20, 50)),
                               ('van', .99, (5, 5, 30, 30)),
                               ('car', .2, (5, 5, 30, 30))], (100, 100))
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].category, 'motorcycle')

    def test_bad_boxes_and_crop_do_not_escape(self):
        observations = [('car', .8, (20, 10, 2, 30)),
                        ('car', float('nan'), (2, 2, 20, 30)),
                        ('car', .8, (float('inf'), 2, 10, 10)),
                        ('car', .7, (650, 2, 700, 15))]
        self.assertEqual(project_boxes(observations, (1344, 760), crop=(352, 60, 640, 640)), [])
        with self.assertRaises(ValueError):
            project_boxes([], (100, 100), crop=(90, 0, 20, 20))


class MergeTests(unittest.TestCase):
    def test_nms_removes_same_car_two_passes(self):
        full = Box2D('car', .9, (100, 100, 200, 200), 'full')
        crop = Box2D('car', .9, (102, 102, 201, 201), 'crop')
        self.assertEqual(merge_boxes([full], [crop]), [crop])
        self.assertGreater(intersection_over_union(full, crop), .9)

    def test_different_classes_not_suppressed(self):
        bicycle = Box2D('bicycle', .9, (100, 100, 200, 200), 'full')
        person = Box2D('pedestrian', .7, (100, 100, 200, 200), 'crop')
        self.assertEqual(len(merge_boxes([bicycle], [person])), 2)

    def test_no_overlap_and_invalid_threshold(self):
        a = Box2D('car', .5, (0, 0, 10, 10), 'full')
        b = Box2D('car', .8, (20, 20, 30, 30), 'crop')
        self.assertEqual(len(merge_boxes([a], [b])), 2)
        with self.assertRaises(ValueError):
            merge_boxes([], [], iou_threshold=0)


if __name__ == '__main__':
    unittest.main()
