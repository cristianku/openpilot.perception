"""What the parts of perception-web share: refusals shown to the user, safe paths and texts, private files,
strict JSON. Adapted from openpilot.distill's web/common.py, web/settings.py and web/server.py (same author,
MIT)."""
from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class WebError(Exception):
    """A refusal shown to the user: HTTP status, short machine reason, message for the user."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


def inside(base: Path, path: Path) -> bool:
    """True when path, with every symlink resolved, is base itself or below it."""
    try:
        resolved = path.resolve()
        base_resolved = base.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == base_resolved or resolved.is_relative_to(base_resolved)


def short_text(value, limit: int = 200) -> str:
    """Text from a file or a request made safe to show or print: printable characters only, bounded."""
    text = str(value)
    if len(text) > limit:
        text = text[:limit] + '…'
    return ''.join(character if character.isprintable() else '?' for character in text)


def read_private(path: Path) -> str:
    """The text of a file, '' when it does not exist. Any other read error is refused, so a file
    that cannot be read is never overwritten."""
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''
    except (OSError, UnicodeDecodeError) as exc:
        raise WebError(500, 'unreadable', 'Cannot read the saved settings on this server (' + type(exc).__name__
                       + ').') from None


def write_private(path: Path, text: str) -> None:
    """Writes a file readable by its owner only (600), all at once: a temporary file in the same
    folder, flushed to disk, then renamed over the old one. A link is never written through."""
    if path.is_symlink():
        raise WebError(409, 'file_is_a_link', 'The file that keeps this setting on the server is a link: '
                       'change it by hand there.')
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    except OSError as exc:
        raise WebError(500, 'cannot_save', 'Cannot save it on this server (' + type(exc).__name__ + ').') from None
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise WebError(500, 'cannot_save', 'Cannot save it on this server (' + type(exc).__name__ + ').') from None


def strict_json_loads(text: str):
    """json.loads without NaN/Infinity and without repeated keys."""
    def reject_constant(name):
        raise ValueError('constant ' + name + ' not allowed')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('repeated key')
            result[key] = value
        return result

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)


def render_problem(status: int, message: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><title>Perception</title>'
            '<style>body{margin:0;padding:24px 16px;'
            'font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0b0f16;color:#e8ecf3}'
            'main{max-width:640px;margin:0 auto;background:#161b24;'
            'border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:20px}p{margin:0 0 8px}'
            '</style></head><body><main>'
            '<p>' + html.escape(message) + '</p><p>HTTP code ' + str(status) + '.</p></main></body></html>')
