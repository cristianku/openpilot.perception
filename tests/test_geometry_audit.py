"""perception.audit.geometry_audit: the geometry on synthetic poses, where the answer is known."""
import math
import unittest

import numpy as np

from perception.audit import geometry_audit as audit


def yaw(degrees):
    a = math.radians(degrees)
    return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])


class Geometry(unittest.TestCase):
    def setUp(self):
        self.K = audit.camera_matrix(1344, 760, 1141.5)

    def test_device_to_view_axes(self):
        # forward in the device frame is the optical axis, right is image x, down is image y
        np.testing.assert_allclose(audit.VIEW_FROM_DEVICE @ [1, 0, 0], [0, 0, 1])
        np.testing.assert_allclose(audit.VIEW_FROM_DEVICE @ [0, 1, 0], [1, 0, 0])
        np.testing.assert_allclose(audit.VIEW_FROM_DEVICE @ [0, 0, 1], [0, 1, 0])

    def test_quaternion(self):
        np.testing.assert_allclose(audit.quat_to_rot(np.array([1.0, 0, 0, 0])), np.eye(3))
        half = math.radians(10) / 2
        np.testing.assert_allclose(audit.quat_to_rot(np.array([math.cos(half), 0, 0, math.sin(half)])), yaw(10),
                                   atol=1e-12)

    def test_relative_pose_moves_points_between_cameras(self):
        # camera b is 2 m forward of a and turned 3 degrees; a world point must agree in both
        world_from_a, p_a = np.eye(3), np.zeros(3)
        world_from_b, p_b = yaw(3), np.array([0.0, 0.0, 2.0])
        R_ba, t_ba = audit.relative_pose(world_from_a, p_a, world_from_b, p_b)
        X_world = np.array([1.0, -0.5, 30.0])
        X_a = world_from_a.T @ (X_world - p_a)
        X_b = world_from_b.T @ (X_world - p_b)
        np.testing.assert_allclose(R_ba @ X_a + t_ba, X_b, atol=1e-12)
        self.assertAlmostEqual(audit.rotation_angle_deg(R_ba), 3.0, places=9)

    def test_triangulation_and_third_view(self):
        rng = np.random.default_rng(0)
        X_a = np.column_stack([rng.uniform(-10, 10, 50), rng.uniform(-2, 2, 50), rng.uniform(5, 80, 50)])
        R_ba, t_ba = yaw(1).T, np.array([0.05, 0.0, -1.5])
        pts_a = audit.project(self.K, X_a)
        pts_b = audit.project(self.K, X_a @ R_ba.T + t_ba)
        np.testing.assert_allclose(audit.triangulate(self.K, R_ba, t_ba, pts_a, pts_b), X_a, rtol=1e-6)

    def test_scale_is_invisible_to_projections(self):
        # review 2, C4: twice the translation and twice the points give the same pixels
        X = np.array([[1.0, 0.5, 20.0], [-3.0, 1.0, 40.0]])
        R, t = yaw(2), np.array([0.0, 0.0, -1.0])
        np.testing.assert_allclose(audit.project(self.K, X @ R.T + t), audit.project(self.K, (2 * X) @ R.T + 2 * t))

    def test_camera_matrix_after_resize(self):
        K = audit.scaled_camera_matrix(self.K, 0.5, 0.5)
        np.testing.assert_allclose(K, audit.camera_matrix(672, 380, 570.75))
        # a non-uniform resize gives non-symmetric focal lengths
        K = audit.scaled_camera_matrix(self.K, 640 / 1344, 384 / 760)
        self.assertNotAlmostEqual(K[0, 0], K[1, 1])
        np.testing.assert_allclose([K[0, 2], K[1, 2]], [320.0, 192.0])

    def test_poses_interpolate_through_the_short_way(self):
        t = np.array([0.0, 1.0])
        states = np.zeros((2, 10))
        states[:, 3:7] = [[1.0, 0, 0, 0], [-1.0, 0, 0, 0]]  # the same rotation, opposite signs
        R, _ = audit.Poses(t, states).at(0.5)
        np.testing.assert_allclose(R, audit.VIEW_FROM_DEVICE.T, atol=1e-12)


if __name__ == '__main__':
    unittest.main()
