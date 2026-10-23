"""Pictures of the road at a moment of a segment, for the Radar page and the confirmations: one JPEG per frame,
made once with ffmpeg and kept in outputs/web/pictures/.

The full road video of openpilot.distill (fcamera.hevc, a bare HEVC stream at 20 frames per second) is decoded up
to the frame wanted (a few seconds for the end of a minute); the comma's small video (qcamera.ts, of the short
recordings copied for the radar tests) is sought by time.

A late frame of a minute costs ffmpeg a few seconds and several cores, and a page stepping through a drive asks for
one picture after another: at most DECODERS run at once, the others wait their turn (a busy answer after
WAIT_S), and two requests for the same frame make it once.
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path

from web.common import WebError

FRAME_LIMIT = 1300          # a minute is 1200 frames
PICTURE_RE = re.compile(r'^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]{1,4}$|^[0-9a-f]{18,24}$')
FFMPEG_TIMEOUT_S = 60
JPEG_QUALITY = '5'          # ffmpeg -q:v, 2 (best) to 31
DECODERS = 2
WAIT_S = 20.0
_decoders = threading.BoundedSemaphore(DECODERS)
_frame_locks: dict[str, threading.Lock] = {}
_frame_locks_guard = threading.Lock()


def _frame_lock(name: str) -> threading.Lock:
    with _frame_locks_guard:
        if len(_frame_locks) > 256:
            # Only the ones nobody holds are forgotten.
            for key in [key for key, lock in _frame_locks.items() if not lock.locked()]:
                del _frame_locks[key]
        return _frame_locks.setdefault(name, threading.Lock())


def picture(folder: Path, frame: int, frame_t_s: float | None, cache: Path) -> bytes:
    """The JPEG of a frame of the segment in `folder` (its index in the road video; frame_t_s, its time from the
    first frame, for the small video)."""
    if not PICTURE_RE.fullmatch(folder.name) or not 0 <= frame < FRAME_LIMIT:
        raise WebError(404, 'not_found', 'No such picture.')
    target = cache / f'{folder.name}-{frame:04d}.jpg'
    if target.is_file():
        return target.read_bytes()
    # The same frame asked twice: the second one waits and finds the first one's picture.
    with _frame_lock(target.name):
        if target.is_file():
            return target.read_bytes()
        return _make(folder, frame, frame_t_s, target)


def _make(folder: Path, frame: int, frame_t_s: float | None, target: Path) -> bytes:
    full, small = folder / 'fcamera.hevc', folder / 'qcamera.ts'
    if full.is_file():
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'hevc', '-i', str(full),
                '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-q:v', JPEG_QUALITY, '-f', 'mjpeg', '-']
    elif small.is_file() and frame_t_s is not None:
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', f'{max(0.0, frame_t_s):.3f}', '-i', str(small),
                '-frames:v', '1', '-q:v', JPEG_QUALITY, '-f', 'mjpeg', '-']
    else:
        raise WebError(404, 'no_video', 'This recording has no video on this server.')
    if not _decoders.acquire(timeout=WAIT_S):
        raise WebError(503, 'busy', 'The server is making other pictures: try again in a moment.')
    try:
        result = subprocess.run(argv, capture_output=True, timeout=FFMPEG_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise WebError(500, 'no_picture', 'Cannot make the picture (ffmpeg).') from None
    finally:
        _decoders.release()
    if result.returncode != 0 or not result.stdout.startswith(b'\xff\xd8'):
        raise WebError(404, 'no_picture', 'This frame is not in the video.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Its own temporary name: another thread may be writing the same frame (see _frame_lock).
    partial = target.with_name(target.name + '.' + str(threading.get_ident()) + '.part')
    partial.write_bytes(result.stdout)
    partial.replace(target)
    return result.stdout
