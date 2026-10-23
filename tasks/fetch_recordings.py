"""Copy short recordings from the comma for the radar tests, then read their radar.

    python -m tasks.fetch_recordings <route>--<segment> [...]

Copies rlog.zst and, when the comma still has it, qcamera.ts of each one into outputs/recordings/ (web/comma.py:
read only on the comma), then runs perception.radar.index on them into outputs/radar/. The comma's address is
the one the site uses (web.comma.comma_host).
"""
from __future__ import annotations

import sys

from web.comma import RECORDING_RE, Comma, comma_host
from web.common import REPO, WebError
from web.radar import radar_dir, recordings_dir
from web.settings import Settings


def main(arguments: list[str]) -> int:
    recordings = [name for name in arguments if RECORDING_RE.fullmatch(name)]
    if not recordings or len(recordings) != len(arguments):
        print('usage: python -m tasks.fetch_recordings <route>--<segment> [...]', flush=True)
        return 2
    chosen, _ = Settings(REPO).drives_folder()
    distill = chosen['path'].parent if chosen else None
    comma = Comma(comma_host(REPO, distill))
    destination = recordings_dir(REPO)
    copied = []
    for done, name in enumerate(recordings, start=1):
        try:
            files = comma.fetch(name, destination)
        except WebError as exc:
            print(f'{name}: {exc.message}', flush=True)
        else:
            print(f'{name}: copied ' + ', '.join(files), flush=True)
            copied.append(destination / name)
        print(f'PROGRESS {done}/{len(recordings) + 1}', flush=True)
    if not copied:
        print('ERROR: nothing copied', flush=True)
        return 1
    from perception.radar.index import main as index

    code = index([str(folder) for folder in copied] + ['--out', str(radar_dir(REPO)), '--jobs', '2'])
    print(f'PROGRESS {len(recordings) + 1}/{len(recordings) + 1}', flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
