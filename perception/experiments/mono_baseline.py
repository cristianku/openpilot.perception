"""Experiment 1 of docs/review-decisions.md: the frozen monocular baseline (Depth Anything V2 Metric VKITTI
Small, perception.models.depth_anything), inference only, on the road camera.

    flock -n /run/lock/v100-gpu.lock uv run python -m perception.experiments.mono_baseline \\
        data/<segment> [...] --out report.json [--pictures outputs/perception/experiment-1]

There is no radar in the logs yet, so this is a proxy check, reported as such: the depth of the model at
static points against their depth triangulated from two frames 0.5 s apart with the localizer poses. Those
references rely on the localizer's metric scale (not checked yet, Experiment 0) and on RANSAC to drop moving
points; the model's depth is the median of a 5x5 window around each keypoint (keypoints sit on corners,
where depth jumps); they are kept only with enough parallax (≥ 1.5°), a consistent reprojection (< 1 px in the second
frame) and a triangulation uncertainty ≤ 5 % (σZ/Z ≈ Z·0.5 px / (f·baseline across the ray)).

  B0: the zero-shot output.
  B1: the same output times one scale factor, fitted on the development drives only (every other route) and
      measured on the others: s = exp(median(log(z_reference) − log(z_model))).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from perception.audit import geometry_audit as geo
from perception.metrics import BANDS, band_of

K_FRAMES = 10               # 0.5 s between the two frames
STRIDE = 30                 # a pair every 1.5 s
MIN_SPEED_MPS = 5.0
MIN_PARALLAX_DEG = 1.5
MAX_REPROJECTION_PX = 1.0
MAX_RELATIVE_SIGMA = 0.05
PIXEL_SIGMA = 0.5


def reference_points(gray_a, gray_b, K, R_ba, t_ba):
    """Static points of frame a with their triangulated depth (pixels (N, 2), depth (N,))."""
    import cv2

    kp_a, de_a = geo._features(gray_a)
    kp_b, de_b = geo._features(gray_b)
    matches = geo._matches(de_a, de_b)
    if len(matches) < 30:
        return np.zeros((0, 2)), np.zeros(0)
    pts_a = np.float64([kp_a[i].pt for i, _ in matches])
    pts_b = np.float64([kp_b[j].pt for _, j in matches])
    E, mask = cv2.findEssentialMat(pts_a, pts_b, K, method=cv2.RANSAC, prob=0.999, threshold=1.0)
    if E is None or E.shape != (3, 3):
        return np.zeros((0, 2)), np.zeros(0)
    inliers = mask.ravel().astype(bool)
    pts_a, pts_b = pts_a[inliers], pts_b[inliers]
    X_a = geo.triangulate(K, R_ba, t_ba, pts_a, pts_b)
    X_b = X_a @ R_ba.T + t_ba
    centre_b_in_a = -R_ba.T @ t_ba
    keep = (X_a[:, 2] > 1.0) & (X_b[:, 2] > 1.0)
    rays_a, rays_b = X_a, X_a - centre_b_in_a
    cosine = np.sum(rays_a * rays_b, axis=1) / (np.linalg.norm(rays_a, axis=1) * np.linalg.norm(rays_b, axis=1))
    parallax = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    reprojection = np.full(len(X_a), np.inf)
    reprojection[keep] = np.linalg.norm(geo.project(K, X_b[keep]) - pts_b[keep], axis=1)
    # Baseline across the ray: the part of the camera motion perpendicular to the viewing direction.
    unit = rays_a / np.linalg.norm(rays_a, axis=1, keepdims=True)
    across = np.linalg.norm(centre_b_in_a - (unit @ centre_b_in_a)[:, None] * unit, axis=1)
    sigma = X_a[:, 2] * PIXEL_SIGMA / (K[0, 0] * np.maximum(across, 1e-6))
    keep &= (parallax >= MIN_PARALLAX_DEG) & (reprojection < MAX_REPROJECTION_PX) & (sigma <= MAX_RELATIVE_SIGMA)
    keep &= (X_a[:, 2] >= BANDS[0][1]) & (X_a[:, 2] <= BANDS[-1][2])
    return pts_a[keep], X_a[keep, 2]


def segment_pairs(folder: Path, model, pictures: Path | None) -> dict:
    import cv2
    from safetensors.numpy import load_file

    from perception.models import depth_anything as da

    localization = load_file(str(folder / 'localizer.safetensors'))
    frame_t = localization['frame_t']
    poses = geo.Poses(localization['t'], localization['states'])
    video = folder / 'fcamera.hevc'
    width, height = geo.video_size(video)
    K = geo.camera_matrix(width, height, geo.NARROW_FOCAL[(width, height)])
    starts = [a for a in range(STRIDE // 2, len(frame_t) - K_FRAMES, STRIDE)
              if poses.speed(frame_t[a]) >= MIN_SPEED_MPS]
    if not starts:
        return {'segment': folder.name, 'route': folder.name[:18], 'points': []}
    frames = geo.read_frames(video, [i for a in starts for i in (a, a + K_FRAMES)], width, height, colour=True)
    seen_width, _ = da.network_input_size(model, frames[starts[0]])
    points = []
    for n, a in enumerate(starts):
        b = a + K_FRAMES
        R_a, p_a = poses.at(frame_t[a])
        R_b, p_b = poses.at(frame_t[b])
        R_ba, t_ba = geo.relative_pose(R_a, p_a, R_b, p_b)
        gray_a, gray_b = (cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY) for i in (a, b))
        pixels, reference = reference_points(gray_a, gray_b, K, R_ba, t_ba)
        if not len(reference):
            continue
        depth = da.predict(model, frames[a])
        u = np.clip(np.round(pixels[:, 0]).astype(int), 2, width - 3)
        v = np.clip(np.round(pixels[:, 1]).astype(int), 2, height - 3)
        # Keypoints sit on corners, where depth jumps: the median of a 5x5 window, not the single pixel.
        windows = np.stack([depth[v + dv, u + du] for dv in range(-2, 3) for du in range(-2, 3)], axis=1)
        for z_ref, z_model, uu, vv in zip(reference, np.median(windows, axis=1), u, v, strict=True):
            points.append([a, float(z_ref), float(z_model), int(uu), int(vv)])
        if pictures is not None and n % 10 == 0:
            pictures.mkdir(parents=True, exist_ok=True)
            colour = cv2.applyColorMap(np.uint8(np.clip(depth / da.MAX_DEPTH_M * 255, 0, 255)), cv2.COLORMAP_TURBO)
            cv2.imwrite(str(pictures / f'{folder.name}-{a:04d}.jpg'), np.vstack([frames[a], colour]))
    return {'segment': folder.name, 'route': folder.name[:18], 'size': [width, height],
            'focal_px': float(K[0, 0]), 'focal_seen_px': float(K[0, 0] * seen_width / width), 'points': points}


def errors(reference: np.ndarray, model: np.ndarray) -> dict | None:
    if not len(reference):
        return None
    ratio = model / reference
    return {'points': int(len(reference)), 'absrel': float(np.mean(np.abs(model - reference) / reference)),
            'mae_m': float(np.mean(np.abs(model - reference))),
            'median_ratio_model_over_reference': float(np.median(ratio)),
            'delta_1.25': float(np.mean(np.maximum(ratio, 1 / ratio) < 1.25))}


def by_band(reference: np.ndarray, model: np.ndarray) -> dict:
    bands = np.array([band_of(z) for z in reference])
    return {name: errors(reference[bands == name], model[bands == name]) for name, _, _ in BANDS}


def summarize(segments: list[dict]) -> dict:
    routes = sorted({s['route'] for s in segments})
    development = set(routes[0::2])

    def gather(chosen):
        rows = [p for s in segments if s['route'] in chosen for p in s['points']]
        if not rows:
            return np.zeros(0), np.zeros(0)
        array = np.asarray(rows)
        return array[:, 1], array[:, 2]

    dev_ref, dev_model = gather(development)
    test_ref, test_model = gather(set(routes) - development)
    scale = float(np.exp(np.median(np.log(dev_ref) - np.log(dev_model)))) if len(dev_ref) else math.nan
    all_ref, all_model = gather(set(routes))
    return {
        'kind': 'proxy: static points triangulated with the localizer poses (scale not yet checked), no radar',
        'segments': len(segments), 'routes': len(routes), 'development_routes': len(development),
        'focal_seen_px': segments[0].get('focal_seen_px') if segments else None,
        'B0_all': errors(all_ref, all_model), 'B0_all_by_band': by_band(all_ref, all_model),
        'B1_scale_from_development': scale,
        'B0_held_out': errors(test_ref, test_model), 'B0_held_out_by_band': by_band(test_ref, test_model),
        'B1_held_out': errors(test_ref, test_model * scale),
        'B1_held_out_by_band': by_band(test_ref, test_model * scale),
    }


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m perception.experiments.mono_baseline', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('segments', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, help='JSON report (every point, then the summary)')
    parser.add_argument('--pictures', type=Path, help='folder for a few frame + depth pictures (not for git)')
    parser.add_argument('--device', default='cuda')
    options = parser.parse_args(arguments)
    from perception.models import depth_anything as da

    model = da.load(options.device)
    segments = []
    for folder in options.segments:
        try:
            segments.append(segment_pairs(folder, model, options.pictures))
            print(f'{folder.name}: {len(segments[-1]["points"])} reference points', flush=True)
        except (OSError, ValueError) as exception:
            print(f'{folder.name}: skipped ({exception})', flush=True)
    report = {'summary': summarize(segments), 'segments': segments}
    print(json.dumps(report['summary'], indent=1))
    if options.out:
        options.out.write_text(json.dumps(report))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
