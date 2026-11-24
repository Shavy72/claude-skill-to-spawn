"""#479: Kein Test liest tmux-Sitzungen des Rechners — Prüf-Test für den Hausmeister.

Ursache: :func:`hausmeister._keine_spec_sitzung` fragt ``tmux list-sessions`` ab. Lief auf dem
Rechner zufällig eine echte Sitzung ``spec-11``, blieb in Tests ein Ordner, der weg sollte
(``tmux-Sitzung spec-11 läuft``). Die Abhilfe sitzt an EINER Stelle: der autouse-Fixture
``_eigenes_tmux`` in ``tests/conftest.py`` (eigener tmux-Server je Test).

Beleg hier mit einer tmux-Attrappe im PATH, die wie echtes tmux den Server über ``$TMUX`` bzw.
``$TMUX_TMPDIR`` wählt: Zeigt beides auf den Rechner, meldet sie ``spec-11`` (die echte Sitzung);
zeigt es in den Temp-Ordner des Testlaufs, gibt es keinen Server.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from to_spawn import hausmeister

ATTRAPPE = """#!/bin/sh
# tmux-Attrappe (#479): Server-Wahl wie echtes tmux — erst $TMUX, dann $TMUX_TMPDIR (Standard /tmp).
if [ -n "$TMUX" ]; then
    echo spec-11
    exit 0
fi
case "${{TMUX_TMPDIR:-/tmp}}" in
    "{basis}"/*)
        echo "no server running on ${{TMUX_TMPDIR}}/tmux-0/default" >&2
        exit 1
        ;;
esac
echo spec-11
exit 0
"""


@pytest.fixture
def tmux_attrappe(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legt die Attrappe als ``tmux`` vorn in den PATH."""
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    datei = bin_ordner / "tmux"
    basis = str(tmp_path_factory.getbasetemp().resolve())
    datei.write_text(ATTRAPPE.format(basis=basis), encoding="utf-8")
    datei.chmod(datei.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_ordner}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.mark.braucht("posix")
def test_hausmeister_sieht_echte_spec_sitzung_nicht(tmux_attrappe: None) -> None:
    """Auf dem Rechner „läuft“ spec-11 — der Hausmeister im Test darf das nicht sehen."""
    hausmeister._keine_spec_sitzung(11)  # wirft _Bleibt, sobald echtes tmux gelesen wird


def test_tmux_umgebung_zeigt_in_den_testlauf(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Die Fixture-Stelle selbst: kein ``$TMUX`` (Sitzung des Rechners), Socket-Ordner im Testlauf."""
    assert "TMUX" not in os.environ
    socket_ordner = Path(os.environ["TMUX_TMPDIR"]).resolve()
    assert socket_ordner.is_relative_to(tmp_path_factory.getbasetemp().resolve())
