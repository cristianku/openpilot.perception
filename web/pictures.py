"""Pictures of the road at a moment of a segment, for the Radar page and the confirmations: one JPEG per frame,
made once with ffmpeg and kept in outputs/web/pictures/.

The full road video of openpilot.distill (fcamera.hevc, a bare HEVC stream at 20 frames per second) is decoded up
to the frame wanted (a few seconds for the end of a minute); the comma's small video (qcamera.ts, of the short
recordings copied for the radar tests) is sought by time.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from web.common import WebError

FRAME_LIMIT = 1300          # a minute is 1200 frames
PICTURE_RE = re.compile(r'^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]{1,4}$|^[0-9a-f]{18,24}$')
FFMPEG_TIMEOUT_S = 60
JPEG_QUALITY = '5'          # ffmpeg -q:v, 2 (best) to 31


def picture(folder: Path, frame: int, frame_t_s: float | None, cache: Path) -> bytes:
    """The JPEG of a frame of the segment in `folder` (its index in the road video; frame_t_s, its time from the
    first frame, for the small video)."""
    if not PICTURE_RE.fullmatch(folder.name) or not 0 <= frame < FRAME_LIMIT:
        raise WebError(404, 'not_found', 'No such picture.')
    target = cache / f'{folder.name}-{frame:04d}.jpg'
    if target.is_file():
        return target.read_bytes()
    full, small = folder / 'fcamera.hevc', folder / 'qcamera.ts'
    if full.is_file():
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'hevc', '-i', str(full),
                '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-q:v', JPEG_QUALITY, '-f', 'mjpeg', '-']
    elif small.is_file() and frame_t_s is not None:
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', f'{max(0.0, frame_t_s):.3f}', '-i', str(small),
                '-frames:v', '1', '-q:v', JPEG_QUALITY, '-f', 'mjpeg', '-']
    else:
        raise WebError(404, 'no_video', 'This recording has no video on this server.')
    try:
        result = subprocess.run(argv, capture_output=True, timeout=FFMPEG_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise WebError(500, 'no_picture', 'Cannot make the picture (ffmpeg).') from None
    if result.returncode != 0 or not result.stdout.startswith(b'\xff\xd8'):
        raise WebError(404, 'no_picture', 'This frame is not in the video.')
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    partial.write_bytes(result.stdout)
    partial.replace(target)
    return result.stdout
