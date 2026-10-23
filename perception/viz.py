"""What to show for each object: vision distance, radar measurement if any, difference, validity and
where each value comes from. `object_rows` gives the rows (for a table or a web page), `draw` puts
them on the image (needs OpenCV), `table` prints them."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from perception.association import Association, Detection
from perception.provenance import Provenance


@dataclass(frozen=True)
class ObjectRow:
    object_id: int
    category: str
    estimated_m: float                 # vision x, calibrated frame
    estimate_source: Provenance
    radar_m: float | None              # radar distance in the same frame, only for the paired object
    radar_source: Provenance | None
    difference_m: float | None         # estimated - radar
    valid: bool                        # the radar value is a usable label for this object
    note: str                          # 'paired with the radar', or why not


def object_rows(detections: list[Detection], association: Association) -> list[ObjectRow]:
    rows = []
    for detection in detections:
        paired = association.associated and detection.object_id == association.object_id
        radar_m = association.label_x_m if paired else None
        rows.append(ObjectRow(
            object_id=detection.object_id,
            category=detection.category,
            estimated_m=detection.x_m,
            estimate_source=detection.provenance,
            radar_m=radar_m,
            radar_source=association.provenance if paired else None,
            difference_m=detection.x_m - radar_m if radar_m is not None else None,
            valid=paired,
            note='paired with the radar' if paired else (
                'radar: ' + association.reason if not association.associated else 'not the radar target'),
        ))
    return rows


def _synthetic(rows: list[ObjectRow]) -> bool:
    return any(Provenance.SYNTHETIC in (row.estimate_source, row.radar_source) for row in rows)


def _metres(value: float | None) -> str:
    return '-' if value is None else f'{value:.1f} m'


def table(rows: list[ObjectRow]) -> str:
    lines = ['SYNTHETIC TEST DATA'] if _synthetic(rows) else []
    lines.append(f'{"id":>3} {"class":<10} {"estimated":>10} {"radar":>9} {"diff":>8} {"valid":<5} source')
    for row in rows:
        source = str(row.estimate_source) + ('+' + str(row.radar_source) if row.radar_source else '')
        lines.append(f'{row.object_id:>3} {row.category:<10} {_metres(row.estimated_m):>10} {_metres(row.radar_m):>9} '
                     f'{_metres(row.difference_m):>8} {"yes" if row.valid else "no":<5} {source}  ({row.note})')
    return '\n'.join(lines)


def draw(image: np.ndarray, detections: list[Detection], rows: list[ObjectRow]) -> np.ndarray:
    """A copy of the (H, W, 3) uint8 image with each detection's box and its row."""
    import cv2

    out = image.copy()
    by_id = {row.object_id: row for row in rows}
    for detection in detections:
        row = by_id.get(detection.object_id)
        if row is None or detection.box is None:
            continue
        x0, y0, x1, y1 = (int(round(v)) for v in detection.box)
        color = (60, 200, 60) if row.valid else (200, 200, 200)
        cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
        text = [f'{row.category} vision {_metres(row.estimated_m)}']
        if row.radar_m is not None:
            text.append(f'radar {_metres(row.radar_m)}  diff {row.difference_m:+.1f} m')
        for i, line in enumerate(text):
            cv2.putText(out, line, (x0, max(14, y0 - 6 - 16 * (len(text) - 1 - i))), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, color, 1, cv2.LINE_AA)
    if _synthetic(rows):
        cv2.putText(out, 'SYNTHETIC TEST DATA', (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
    return out
