"""Experiment 0 of docs/review-decisions.md: how well the localizer poses explain the motion the road camera
sees, before a multi-frame network relies on them. No training, no GPU.

    uv run python -m perception.audit.geometry_audit <segment dir> [<segment dir> ...] [--out report.json]

A segment dir is one of openpilot.distill's data/<segment>/ folders: fcamera.hevc and localizer.safetensors
(frame_t, the 100 Hz states: position ECEF 0:3, quaternion ecef_from_device 3:7, velocity 7:10).

For triplets of frames (a, b = a + k, c = a + 2k), every `stride` frames:
  1. SIFT matches a↔b (ratio test), static ones kept by RANSAC on the essential matrix. The coverage of the
     image (cells of a 4×4 grid with inliers) says whether the pair is observable at all.
  2. Rotation and direction of translation of the essential matrix against the localizer's relative pose.
     The essential matrix has no scale: a wrong scale cannot show here (review 2, C4).
  3. Third view: the a↔b inliers triangulated with the localizer poses, projected into c through the
     localizer pose, against identity (no motion) and rotation only, on the same tracked points (b↔c).
  4. Timing: on pairs with a changing rotation, the shift of the frame times (±60 ms) that best explains the
     rotations. With a constant yaw rate a shift is invisible: the curve says whether it was observable.
The metric scale is not checked: it needs an external measurement (a known distance), not these logs.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

# Pixels of the road camera by sensor: openpilot's DEVICE_CAMERAS (narrow_road), principal point at the centre.
NARROW_FOCAL = {(1928, 1208): 2648.0, (1344, 760): 1141.5}
# Device frame (x forward, y right, z down) to the optical frame (x right, y down, z forward).
VIEW_FROM_DEVICE = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
GRID = 4
MIN_SPEED_MPS = 3.0


def camera_matrix(width: int, height: int, focal: float) -> np.ndarray:
    return np.array([[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]])


def scaled_camera_matrix(K: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    """K of the same camera after a resize by (scale_x, scale_y): what the image really used needs."""
    return np.diag([scale_x, scale_y, 1.0]) @ K


def quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Rotation of a unit quaternion [w, x, y, z] (openpilot's convention)."""
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def ecef_from_view(q_ecef_from_device: np.ndarray) -> np.ndarray:
    return quat_to_rot(q_ecef_from_device) @ VIEW_FROM_DEVICE.T


def relative_pose(R_a: np.ndarray, p_a: np.ndarray, R_b: np.ndarray, p_b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(R_ba, t_ba) with X_b = R_ba X_a + t_ba, for cameras a, b given as world_from_camera R and position p."""
    R_ba = R_b.T @ R_a
    t_ba = R_b.T @ (p_a - p_b)
    return R_ba, t_ba


def rotation_angle_deg(R: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R) - 1.0) / 2.0))))


def angle_between_deg(u: np.ndarray, v: np.ndarray) -> float:
    cosine = float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v)))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def project(K: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Pixels of points (N, 3) in camera coordinates."""
    uvw = X @ K.T
    return uvw[:, :2] / uvw[:, 2:3]


def triangulate(K: np.ndarray, R_ba: np.ndarray, t_ba: np.ndarray, pts_a: np.ndarray, pts_b: np.ndarray) -> np.ndarray:
    """Points (N, 3) in camera a from their pixels in a and b (linear DLT)."""
    P_a = K @ np.hstack([np.eye(3), np.zeros((3, 1))])
    P_b = K @ np.hstack([R_ba, t_ba.reshape(3, 1)])
    points = []
    for (ua, va), (ub, vb) in zip(pts_a, pts_b, strict=True):
        A = np.stack([ua * P_a[2] - P_a[0], va * P_a[2] - P_a[1], ub * P_b[2] - P_b[0], vb * P_b[2] - P_b[1]])
        X = np.linalg.svd(A)[2][-1]
        points.append(X[:3] / X[3])
    return np.asarray(points).reshape(-1, 3)


class Poses:
    """The camera pose at any time, from the 100 Hz states of the localizer (shifted by `shift_s` if asked)."""

    def __init__(self, t: np.ndarray, states: np.ndarray):
        self.t = t
        self.position = states[:, 0:3]
        quaternions = states[:, 3:7].copy()
        # One hemisphere, so interpolation never goes the long way round.
        for i in range(1, len(quaternions)):
            if np.dot(quaternions[i], quaternions[i - 1]) < 0:
                quaternions[i] = -quaternions[i]
        self.quaternion = quaternions
        self.velocity = states[:, 7:10]

    def at(self, time_s: float) -> tuple[np.ndarray, np.ndarray]:
        position = np.array([np.interp(time_s, self.t, self.position[:, i]) for i in range(3)])
        q = np.array([np.interp(time_s, self.t, self.quaternion[:, i]) for i in range(4)])
        return ecef_from_view(q), position

    def speed(self, time_s: float) -> float:
        return float(np.linalg.norm([np.interp(time_s, self.t, self.velocity[:, i]) for i in range(3)]))


def read_frames(video: Path, indices: list[int], width: int, height: int,
                colour: bool = False) -> dict[int, np.ndarray]:
    """Frames of an HEVC file by index (decode order = display order: no B-frames), grey or BGR (colour).
    ffmpeg decodes the stream once on the CPU; only the wanted frames are kept, and it stops after the last."""
    wanted = set(indices)
    last = max(wanted)
    channels = 3 if colour else 1
    size = width * height * channels
    frames: dict[int, np.ndarray] = {}
    process = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(video), '-f', 'rawvideo', '-pix_fmt',
                                'bgr24' if colour else 'gray', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for index in range(last + 1):
            raw = process.stdout.read(size)
            if len(raw) < size:
                break
            if index in wanted:
                shape = (height, width, 3) if colour else (height, width)
                frames[index] = np.frombuffer(raw, np.uint8).reshape(shape)
    finally:
        process.kill()
        process.wait()
    if len(frames) != len(wanted):
        raise ValueError(f'{video}: {len(frames)} of {len(wanted)} frames decoded')
    return frames


def video_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                          '-of', 'csv=p=0', str(video)], capture_output=True, text=True, check=True).stdout
    width, height = out.strip().split(',')[:2]
    return int(width), int(height)


def _features(image: np.ndarray):
    import cv2

    return cv2.SIFT_create(nfeatures=4000).detectAndCompute(image, None)


def _matches(descriptors_a, descriptors_b, ratio: float = 0.75) -> list[tuple[int, int]]:
    import cv2

    if descriptors_a is None or descriptors_b is None or len(descriptors_a) < 2 or len(descriptors_b) < 2:
        return []
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(descriptors_a, descriptors_b, k=2)
    return [(m.queryIdx, m.trainIdx) for m, n in (p for p in pairs if len(p) == 2) if m.distance < ratio * n.distance]


def audit_triplet(images, frame_t, poses: Poses, K: np.ndarray, a: int, k: int) -> dict:
    """The checks of the module docstring for frames a, a + k, a + 2k."""
    import cv2

    b, c = a + k, a + 2 * k
    height, width = images[a].shape
    result = {'frame': a, 'k': k, 'speed_mps': poses.speed(frame_t[a])}
    (kp_a, de_a), (kp_b, de_b), (kp_c, de_c) = (_features(images[i]) for i in (a, b, c))
    ab = _matches(de_a, de_b)
    result['matches'] = len(ab)
    if len(ab) < 30:
        return result | {'observable': False, 'why': 'too few matches'}
    pts_a = np.float64([kp_a[i].pt for i, _ in ab])
    pts_b = np.float64([kp_b[j].pt for _, j in ab])
    E, mask = cv2.findEssentialMat(pts_a, pts_b, K, method=cv2.RANSAC, prob=0.999, threshold=1.0)
    if E is None or E.shape != (3, 3):
        return result | {'observable': False, 'why': 'no essential matrix'}
    _, R_e, t_e, mask = cv2.recoverPose(E, pts_a, pts_b, K, mask=mask)
    inliers = mask.ravel().astype(bool)
    cells = {(int(u * GRID / width), int(v * GRID / height)) for u, v in pts_a[inliers]}
    result['inliers'] = int(inliers.sum())
    result['grid_cells'] = len(cells)
    # Strict (review 2): enough inliers spread over the image. Usable: a looser set, to see if conclusions hold.
    result['observable'] = result['inliers'] >= 100 and len(cells) >= GRID * GRID // 2
    result['usable'] = result['inliers'] >= 50

    R_a, p_a = poses.at(frame_t[a])
    R_b, p_b = poses.at(frame_t[b])
    R_c, p_c = poses.at(frame_t[c])
    R_ba, t_ba = relative_pose(R_a, p_a, R_b, p_b)
    result['localizer_rotation_deg'] = rotation_angle_deg(R_ba)
    result['localizer_translation_m'] = float(np.linalg.norm(t_ba))
    result['rotation_error_deg'] = rotation_angle_deg(R_e @ R_ba.T)
    if result['speed_mps'] >= MIN_SPEED_MPS:
        result['translation_direction_error_deg'] = angle_between_deg(t_e.ravel(), t_ba)

        # Third view: points tracked a -> b -> c through the b keypoints.
        bc = dict(_matches(de_b, de_c))
        triplets = [(pa, pb, kp_c[bc[j]].pt) for (pa, pb, ok, (_, j)) in zip(pts_a, pts_b, inliers, ab, strict=True)
                    if ok and j in bc]
        if len(triplets) >= 20:
            ta, tb, tc = (np.float64([t[i] for t in triplets]) for i in range(3))
            X_a = triangulate(K, R_ba, t_ba, ta, tb)
            R_ca, t_ca = relative_pose(R_a, p_a, R_c, p_c)
            X_c = X_a @ R_ca.T + t_ca
            front = (X_a[:, 2] > 1.0) & (X_c[:, 2] > 1.0) & (X_a[:, 2] < 500.0)
            R_cb, _ = relative_pose(R_b, p_b, R_c, p_c)
            rotation_only = project(K, (np.hstack([tb, np.ones((len(tb), 1))]) @ np.linalg.inv(K).T) @ R_cb.T)
            result['third_view_points'] = int(front.sum())
            if front.sum() >= 10:
                full = np.linalg.norm(project(K, X_c[front]) - tc[front], axis=1)
                result['third_view_px'] = {
                    'identity': float(np.median(np.linalg.norm(tb[front] - tc[front], axis=1))),
                    'rotation_only': float(np.median(np.linalg.norm(rotation_only[front] - tc[front], axis=1))),
                    'full_pose': float(np.median(full)),
                    'full_pose_p90': float(np.percentile(full, 90)),
                }
    result['_R_essential'] = R_e.tolist()
    return result


def timing_curve(records: list[dict], frame_t: np.ndarray, poses: Poses, shifts_ms) -> list[tuple[float, float]]:
    """Median rotation error of the observable pairs against a shift of the frame times."""
    curve = []
    for shift_ms in shifts_ms:
        errors = []
        for record in records:
            if not record.get('observable'):
                continue
            a, b = record['frame'], record['frame'] + record['k']
            R_a, p_a = poses.at(frame_t[a] + shift_ms / 1000.0)
            R_b, p_b = poses.at(frame_t[b] + shift_ms / 1000.0)
            R_ba, _ = relative_pose(R_a, p_a, R_b, p_b)
            errors.append(rotation_angle_deg(np.asarray(record['_R_essential']) @ R_ba.T))
        curve.append((float(shift_ms), float(np.median(errors)) if errors else math.nan))
    return curve


def audit_segment(folder: Path, k: int = 2, stride: int = 20) -> dict:
    from safetensors.numpy import load_file

    localization = load_file(str(folder / 'localizer.safetensors'))
    frame_t = localization['frame_t']
    poses = Poses(localization['t'], localization['states'])
    video = folder / 'fcamera.hevc'
    width, height = video_size(video)
    if (width, height) not in NARROW_FOCAL:
        raise ValueError(f'{video}: unknown road camera size {width}x{height}')
    K = camera_matrix(width, height, NARROW_FOCAL[(width, height)])
    starts = list(range(stride // 2, len(frame_t) - 2 * k, stride))
    images = read_frames(video, [i + d for i in starts for d in (0, k, 2 * k)], width, height)
    records = [audit_triplet(images, frame_t, poses, K, a, k) for a in starts]
    curve = timing_curve(records, frame_t, poses, range(-60, 61, 5))
    for record in records:
        record.pop('_R_essential', None)
    return {'segment': folder.name, 'size': [width, height], 'focal_px': NARROW_FOCAL[(width, height)], 'k': k,
            'pairs': records, 'timing_curve_ms_deg': curve}


def stats(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return None
    return {'n': len(values), 'median': float(np.median(values)), 'p90': float(np.percentile(values, 90))}


def summarize_pairs(pairs: list[dict]) -> dict:
    moving = [p for p in pairs if 'translation_direction_error_deg' in p]
    third = [p['third_view_px'] for p in moving if 'third_view_px' in p]
    result = {
        'pairs': len(pairs),
        'rotation_error_deg': stats([p['rotation_error_deg'] for p in pairs]),
        'localizer_rotation_deg': stats([p['localizer_rotation_deg'] for p in pairs]),
        'translation_direction_error_deg': stats([p['translation_direction_error_deg'] for p in moving]),
        'third_view_px': {name: stats([t[name] for t in third]) for name in ('identity', 'rotation_only', 'full_pose')},
    }
    full, rotation = result['third_view_px']['full_pose'], result['third_view_px']['rotation_only']
    if full and rotation:
        result['third_view_gain_vs_rotation_only'] = 1.0 - full['median'] / rotation['median']
    by_speed = {}
    for name, low, high in (('3-10 m/s', 3, 10), ('10-20 m/s', 10, 20), ('20+ m/s', 20, 99)):
        chosen = [p['third_view_px']['full_pose'] for p in moving
                  if 'third_view_px' in p and low <= p['speed_mps'] < high]
        by_speed[name] = stats(chosen)
    result['third_view_full_pose_px_by_speed'] = by_speed
    return result


def summarize(segments: list[dict]) -> dict:
    pairs = [p for s in segments for p in s['pairs']]
    summary = {
        'segments': len(segments), 'triplets': len(pairs),
        'strict': summarize_pairs([p for p in pairs if p.get('observable')]),
        'usable': summarize_pairs([p for p in pairs if p.get('usable')]),
        'scale': 'not checked: needs an external measurement (a known distance)',
    }
    # The timing shift where the median error is lowest, per segment, and whether it sits on the border.
    best = []
    for segment in segments:
        curve = [(shift, error) for shift, error in segment['timing_curve_ms_deg'] if math.isfinite(error)]
        if curve:
            shift, error = min(curve, key=lambda item: item[1])
            spread = max(e for _, e in curve) - min(e for _, e in curve)
            best.append({'segment': segment['segment'], 'best_shift_ms': shift, 'at_border': abs(shift) >= 60,
                         'curve_spread_deg': spread})
    summary['timing'] = best
    return summary


def _audit_one(folder: Path, k: int, stride: int):
    import cv2

    cv2.setNumThreads(2)
    try:
        return audit_segment(folder, k, stride), None
    except (OSError, ValueError, subprocess.CalledProcessError) as exception:
        return None, str(exception)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m perception.audit.geometry_audit', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('segments', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, help='JSON report (every pair, then the summary)')
    parser.add_argument('--k', type=int, default=2, help='frames between a and b (default 2: 100 ms)')
    parser.add_argument('--stride', type=int, default=20, help='frames between triplets (default 20: 1 s)')
    parser.add_argument('--jobs', type=int, default=1, help='segments audited at the same time (CPU)')
    options = parser.parse_args(arguments)
    from concurrent.futures import ProcessPoolExecutor

    segments = []
    with ProcessPoolExecutor(max_workers=max(1, options.jobs)) as pool:
        futures = {folder: pool.submit(_audit_one, folder, options.k, options.stride) for folder in options.segments}
        for done, (folder, future) in enumerate(futures.items(), start=1):
            segment, problem = future.result()
            if segment is None:
                print(f'{folder.name}: skipped ({problem})', flush=True)
            else:
                segments.append(segment)
                print(f'{folder.name}: {len(segment["pairs"])} triplets', flush=True)
            # Read by the site (web/jobs.py) for its progress bar.
            print(f'PROGRESS {done}/{len(futures)}', flush=True)
    report = {'summary': summarize(segments), 'segments': segments}
    print(json.dumps(report['summary'], indent=1))
    if options.out:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
