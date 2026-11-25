"""#68 P7f: Text aus Unterprozessen wird überall gleich gelesen — UTF-8, nie die Locale.

git, claude und die Skill-CLI schreiben UTF-8. Ohne Angabe dekodiert ``subprocess`` unter
Windows mit cp1252 („ü“ wird „Ã¼“, ein 0x8f bricht den Lese-Thread ab). Die Lesart lebt an
einer Stelle: :data:`to_spawn.konsole.UNTERPROZESS_TEXT`.
"""

from __future__ import annotations

import subprocess
import sys

from to_spawn import konsole

_TEXT = "würde Ünter/.claude-handoff.md — ß"


def test_unterprozess_text_liest_utf8_unabhaengig_von_locale() -> None:
    kind = f"import sys; sys.stdout.buffer.write({_TEXT!r}.encode('utf-8') + bytes([0xC2, 0x8F]))"
    aus = subprocess.run(
        [sys.executable, "-c", kind],
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=30,
        check=True,
    )
    assert aus.stdout == _TEXT + "\x8f"


def test_unterprozess_text_bricht_bei_kaputten_bytes_nicht_ab() -> None:
    kind = "import sys; sys.stdout.buffer.write(bytes([0x8F, 0xFF]) + b'ok')"
    aus = subprocess.run(
        [sys.executable, "-c", kind],
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=30,
        check=True,
    )
    assert aus.stdout is not None and aus.stdout.endswith("ok")
