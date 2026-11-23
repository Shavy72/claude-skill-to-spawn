"""#275 Teil B: Wiederöffnen je Ticket und Regel nur einmal; ``staging:fertig`` startet keinen Bau."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from test_capo_folgerunde_284 import _folgerunden, _starts, _wieder_oeffnen_lassen, tmux  # noqa: F401
from test_waechter_213 import (  # noqa: F401
    _capo,
    _gh_setzen,
    _gh_zustand,
    _iso,
    _kommentare,
    _text,
    _vps_staging,
    welt,
)


def test_echter_verstoss_oeffnet_je_regel_nur_einmal_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Live-Freigabe da, Commit fehlt live: einmal öffnen, beim nächsten Schließen nur einmal melden."""
    _vps_staging(welt, monkeypatch, staging_sha=None, zettel=True)
    konfig = welt["repo"] / ".to-spawn" / "config.json"
    daten = json.loads(konfig.read_text(encoding="utf-8"))
    konfig.write_text(json.dumps({**daten, "waechter": {"karenz_minuten": 0}}), encoding="utf-8")
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0  # Ausgangsstand: #901 offen
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    erste = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open", _text(erste)
    assert len(_kommentare(welt, "901")) == 1, _text(erste)
    # Jemand schließt wieder, ohne dass sich etwas ändert — Commit fehlt weiter live.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-5)))
    zweite = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(zweite)
    kommentare = _kommentare(welt, "901")
    assert len(kommentare) == 2, _text(zweite)
    assert "nicht wieder geöffnet" in kommentare[-1], kommentare
    # Noch ein Schließen aus demselben Grund: keine weitere Meldung.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-10)))
    dritte = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(dritte)
    assert len(_kommentare(welt, "901")) == 2, _text(dritte)


def test_wiedergeoeffnet_mit_staging_fertig_startet_keinen_bau_275(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    """Wiedergeöffnetes Ticket mit ``staging:fertig``: kein zweiter Bau-Start."""
    _wieder_oeffnen_lassen(welt)
    _gh_setzen(welt, "901", labels=["staging:fertig"])
    ergebnis = _capo(welt)
    assert "#901 wieder geöffnet" in ergebnis.stdout, _text(ergebnis)
    assert _starts(tmux) == [], _text(ergebnis)
    assert _folgerunden(welt) == {}
