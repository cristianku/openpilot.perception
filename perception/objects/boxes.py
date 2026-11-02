"""Map 2D detections from full frames/crops and deduplicate them.

Offline only: these boxes have no metric distance, velocity or right to control the car.
A detector's post-processor must first convert network coordinates back to the
original frame or crop. Do not pass a 2D box to the radar/depth association as
though it were a measured 3D position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

# D-FINE COCO labels are resolved by NAME, not numeric model-specific IDs.
COCO_ROAD_CLASSES = {
    'person': 'pedestrian',
    'bicycle': 'bicycle',
    'car': 'car',
    'motorbike': 'motorcycle',
    'motorcycle': 'motorcycle',
    'bus': 'bus',
    'truck': 'truck',
}


@dataclass(frozen=True)
class Box2D:
    category: str
    score: float
    xyxy: tuple[float, float, float, float]  # pixels in the original full frame
    source: str  # 'full' or 'crop'; never a provenance of metric distance


def centre_crop(width: int, height: int, side: int = 640,
                centre: tuple[float, float] | None = None) -> tuple[int, int, int, int]:
    """Square native-pixel crop (left, top, width, height), clamped to the frame.

    `centre` can be the calibrated vanishing point; frame centre is only a
    fallback. The caller must not claim a centred crop follows a curved road.
    """
    if min(width, height, side) <= 0:
        raise ValueError('frame dimensions and crop side must be positive')
    length = min(width, height, side)
    cx, cy = centre if centre is not None else (width / 2, height / 2)
    if not (math.isfinite(cx) and math.isfinite(cy)):
        raise ValueError('crop centre must be finite')
    left = max(0, min(width - length, round(cx - length / 2)))
    top = max(0, min(height - length, round(cy - length / 2)))
    return left, top, length, length


def project_boxes(
    detections: Iterable[tuple[str, float, tuple[float, float, float, float]]],
    frame_size: tuple[int, int],
    *,
    crop: tuple[int, int, int, int] | None = None,
    min_score: float = 0.3,
) -> list[Box2D]:
    """Convert detector (class-name, score, xyxy) in local pixels to full-frame boxes.

    `frame_size` is (width, height). When `crop` is supplied, coordinates are
    relative to that crop *after* resizing network output to crop pixels.
    Reject unknown COCO classes and invalid boxes rather than inventing labels.
    """
    width, height = frame_size
    if width <= 0 or height <= 0 or not 0 <= min_score <= 1:
        raise ValueError('invalid frame size or score threshold')
    if crop is None:
        left = top = 0
        local_width, local_height = width, height
        source = 'full'
    else:
        left, top, local_width, local_height = crop
        if (left < 0 or top < 0 or local_width <= 0 or local_height <= 0
                or left + local_width > width or top + local_height > height):
            raise ValueError('crop must fit inside the frame')
        source = 'crop'
    boxes = []
    for label, score, coords in detections:
        category = COCO_ROAD_CLASSES.get(label.lower()) if isinstance(label, str) else None
        if category is None or not math.isfinite(score) or not min_score <= score <= 1:
            continue
        if len(coords) != 4 or not all(math.isfinite(v) for v in coords):
            continue
        x0, y0, x1, y1 = coords
        # Clip to the actual local image first: out-of-crop coordinates must
        # never spill over into neighbouring parts of the full camera frame.
        x0, x1 = max(0., min(local_width, x0)), max(0., min(local_width, x1))
        y0, y1 = max(0., min(local_height, y0)), max(0., min(local_height, y1))
        if x1 <= x0 or y1 <= y0:
            continue
        boxes.append(Box2D(category, float(score),
                           (x0 + left, y0 + top, x1 + left, y1 + top), source))
    return boxes


def intersection_over_union(a: Box2D, b: Box2D) -> float:
    """IoU in original-frame pixel space, never a depth or trajectory metric."""
    ax0, ay0, ax1, ay1 = a.xyxy
    bx0, by0, bx1, by1 = b.xyxy
    intersection = max(0., min(ax1, bx1) - max(ax0, bx0)) * max(0., min(ay1, by1) - max(ay0, by0))
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return intersection / (area_a + area_b - intersection) if area_a + area_b > intersection else 0.


def merge_boxes(full: Iterable[Box2D], crop: Iterable[Box2D], *,
                iou_threshold: float = 0.5) -> list[Box2D]:
    """Class-wise greedy NMS across two camera passes; crop wins score ties.

    An overlapping pedestrian and bicycle are intentionally both kept. NMS
    avoids double-counting, not tracking objects between video frames.
    """
    if not 0 < iou_threshold < 1:
        raise ValueError('IoU threshold must be strictly between 0 and 1')
    ranked = sorted([*full, *crop], key=lambda b: (-b.score, b.source != 'crop', b.category, b.xyxy))
    kept = []
    for candidate in ranked:
        if all(candidate.category != existing.category or
               intersection_over_union(candidate, existing) <= iou_threshold for existing in kept):
            kept.append(candidate)
    return kept
