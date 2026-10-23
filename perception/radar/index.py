"""The radar of many segments, read once and kept for the site: one JSON per segment (perception.radar.series),
written again only when the segment's log changed.

    python -m perception.radar.index <segment folder> [...] --out outputs/radar [--jobs N]

A segment folder holds rlog.zst (or rlog.bz2, rlog) and, when there is video, fcamera.hevc or qcamera.ts.
Every finished segment prints "PROGRESS done/total" for the site's progress bar; a segment that cannot be read
is written as {"error": ...} so it is not tried again until its log changes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

LOG_NAMES = ('rlog.zst', 'rlog.bz2', 'rlog')


def log_of(folder: Path) -> Path | None:
    for name in LOG_NAMES:
        if (folder / name).is_file():
            return folder / name
    return None


VIDEO_NAMES = ('fcamera.hevc', 'qcamera.ts')


def video_of(folder: Path) -> str | None:
    return next((name for name in VIDEO_NAMES if (folder / name).is_file()), None)


def stamp_of(log: Path) -> dict:
    """What identifies a version of a segment: its log (resolved path, size, modification time) and which
    video it has (a recording's small video can arrive after its log)."""
    stat = log.stat()
    return {'log': str(log.resolve()), 'size': stat.st_size, 'mtime': int(stat.st_mtime),
            'video': video_of(log.parent)}


def cached(out: Path, folder: Path) -> dict | None:
    """The kept series of a segment when it is still the one of its current log, otherwise None."""
    log = log_of(folder)
    path = out / (folder.name + '.json')
    if log is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if data.get('stamp') == stamp_of(log) else None


def index_one(folder: Path, out: Path) -> str:
    from perception.radar.series import radar_series

    log = log_of(folder)
    if log is None:
        return f'{folder.name}: no log'
    if cached(out, folder) is not None:
        return f'{folder.name}: already read'
    stamp = stamp_of(log)
    try:
        data = radar_series(log)
        data['segment'] = folder.name
    except Exception as exc:  # a damaged log must not stop the others
        data = {'segment': folder.name, 'error': f'{type(exc).__name__}: {exc}'[:300]}
    data['stamp'] = stamp
    data['video'] = stamp['video']
    out.mkdir(parents=True, exist_ok=True)
    temporary = out / (folder.name + '.json.part')
    temporary.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
    os.replace(temporary, out / (folder.name + '.json'))
    if 'error' in data:
        return f'{folder.name}: cannot read ({data["error"]})'
    return f'{folder.name}: ' + (f'radar in {round(100 * data["target_share"])}% of the time'
                                 if data['has_radar'] else 'no radar')


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m perception.radar.index', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('segments', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    options = parser.parse_args(arguments)
    # The schema is downloaded once, before the workers start.
    from perception.sources.rlog import load_schema

    load_schema()
    todo = [folder for folder in options.segments if cached(options.out, folder) is None]
    print(f'{len(options.segments) - len(todo)} segments already read, {len(todo)} to read', flush=True)
    if not todo:
        print(f'PROGRESS {len(options.segments)}/{len(options.segments)}', flush=True)
        return 0
    with ProcessPoolExecutor(max_workers=max(1, options.jobs)) as pool:
        for done, line in enumerate(pool.map(index_one, todo, [options.out] * len(todo)), start=1):
            print(line, flush=True)
            print(f'PROGRESS {done}/{len(todo)}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
