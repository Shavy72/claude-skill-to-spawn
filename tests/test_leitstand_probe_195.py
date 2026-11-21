"""#195: ``_probe_frei`` liefert unter Windows ``None``, wenn ``sperre.halte`` beim Öffnen
mit EACCES scheitert (Datei wird gerade gelöscht) — auch wenn die Ausnahme als
``SperreNichtOeffenbar`` statt als ``PermissionError`` ankommt."""

from __future__ import annotations

import errno
import functools
import os
import sys
import types
from pathlib import Path

import pytest

from to_spawn import leitstand, sperre


def _windows_mit_eacces_beim_halten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows-Zweig über die Test-Naht ``msvcrt``; das O_CREAT-Öffnen in halte() scheitert."""
    attrappe = types.SimpleNamespace(LK_NBLCK=2, locking=lambda fd, modus, n: None)
    monkeypatch.setattr(leitstand._sperre, "halte", functools.partial(sperre.halte, msvcrt=attrappe))
    echtes_open = os.open

    def open_mit_eacces(pfad, flags, *args):
        if flags & os.O_CREAT:
            raise PermissionError(errno.EACCES, "Zugriff verweigert", str(pfad))
        return echtes_open(pfad, flags, *args)

    monkeypatch.setattr(os, "open", open_mit_eacces)


def test_probe_liefert_none_bei_eacces_aus_halte_unter_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pfad = tmp_path / "sperre.lock"
    pfad.write_text("")
    _windows_mit_eacces_beim_halten(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    assert leitstand._probe_frei(pfad) is None


def test_probe_wirft_bei_eacces_aus_halte_unter_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pfad = tmp_path / "sperre.lock"
    pfad.write_text("")
    _windows_mit_eacces_beim_halten(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(sperre.SperreNichtOeffenbar):
        leitstand._probe_frei(pfad)
