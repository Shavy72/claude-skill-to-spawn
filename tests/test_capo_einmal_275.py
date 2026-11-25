"""#275 Teil B: Wiederöffnen je Ticket und Regel nur einmal; ``staging:fertig`` startet keinen Bau."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from test_capo_folgerunde_284 import _folgerunden, _starts, _wieder_oeffnen_lassen, tmux  # noqa: F401
from test_waechter_213 import (  # noqa: F401
    _capo,
    _capo_zustand,
    _commit,
    _gh_setzen,
    _gh_zustand,
    _git,
    _iso,
    _kommentare,
    _ssh_fake,
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


def test_neuer_verstoss_derselben_regel_oeffnet_wieder_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Gleiche Regel, neuer Inhalt (weiterer Commit fehlt live): wieder öffnen, einmal."""
    _vps_staging(welt, monkeypatch, staging_sha=None, zettel=True)
    konfig = welt["repo"] / ".to-spawn" / "config.json"
    daten = json.loads(konfig.read_text(encoding="utf-8"))
    konfig.write_text(json.dumps({**daten, "waechter": {"karenz_minuten": 0}}), encoding="utf-8")
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0  # Ausgangsstand: #901 offen
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    erste = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open", _text(erste)
    # Neue Arbeit: weiterer Commit, der ebenfalls nicht live ist — neuer Verstoß derselben Regel.
    _commit(welt["repo"], "fix: Seite nachgebessert (#901)", {"web/seite.py": "x = 2\n"})
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-5)))
    zweite = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open", _text(zweite)
    assert len(_kommentare(welt, "901")) == 2, _text(zweite)
    # Derselbe neue Verstoß noch einmal: nur einmal melden, nicht öffnen.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-10)))
    dritte = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(dritte)
    assert "nicht wieder geöffnet" in _kommentare(welt, "901")[-1], _text(dritte)


def _einmal_geoeffnet(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """#901 einmal wegen ``vps_ungleich_origin`` wieder geöffnet, dann erneut geschlossen."""
    _vps_staging(welt, monkeypatch, staging_sha=None, zettel=True)
    konfig = welt["repo"] / ".to-spawn" / "config.json"
    daten = json.loads(konfig.read_text(encoding="utf-8"))
    konfig.write_text(json.dumps({**daten, "waechter": {"karenz_minuten": 0}}), encoding="utf-8")
    _gh_setzen(welt, "901", state="open", closed_at=None)
    assert _capo(welt).returncode == 0  # Ausgangsstand: #901 und #902 offen
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    erste = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open", _text(erste)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-5)))


def _erneut_merker(welt: dict[str, Path]) -> list[str]:
    return [s for s in _capo_zustand(welt).get("erledigt", []) if s.startswith("erneut|")]


def test_gemischt_erneut_und_neu_im_selben_takt_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Ein Takt: #901 nur erneut melden, #902 (neuer Verstoß) wieder öffnen."""
    _einmal_geoeffnet(welt, monkeypatch)
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    ergebnis = _capo(welt)
    issues = _gh_zustand(welt)["issues"]
    assert issues["901"]["state"] == "closed", _text(ergebnis)
    assert "nicht wieder geöffnet" in _kommentare(welt, "901")[-1], _text(ergebnis)
    assert issues["902"]["state"] == "open", _text(ergebnis)
    assert len(_kommentare(welt, "902")) == 1, _text(ergebnis)
    assert "#901 erneut gemeldet" in ergebnis.stdout, _text(ergebnis)
    assert "#902 wieder geöffnet" in ergebnis.stdout, _text(ergebnis)


def test_erneut_melden_gescheitert_versucht_naechsten_takt_wieder_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """``kommentiere`` scheitert: kein Erledigt-Merker, der nächste Takt meldet nach."""
    _einmal_geoeffnet(welt, monkeypatch)
    monkeypatch.setenv("GH_STUB_FEHLER", "comment")
    gescheitert = _capo(welt)
    assert "#901 FEHLER: erneut melden gescheitert" in gescheitert.stdout, _text(gescheitert)
    assert len(_kommentare(welt, "901")) == 1, _text(gescheitert)
    assert _erneut_merker(welt) == [], _text(gescheitert)
    monkeypatch.delenv("GH_STUB_FEHLER")
    danach = _capo(welt)
    assert "#901 erneut gemeldet" in danach.stdout, _text(danach)
    assert len(_kommentare(welt, "901")) == 2, _text(danach)
    assert len(_erneut_merker(welt)) == 1, _text(danach)


def test_erneut_melden_probe_schreibt_nichts_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """``--dry-run``: nur Probe-Zeile, kein Kommentar, kein Merker — echter Takt meldet danach."""
    _einmal_geoeffnet(welt, monkeypatch)
    probe = _capo(welt, "--dry-run")
    assert "#901 [Probe] würde erneut melden" in probe.stdout, _text(probe)
    assert len(_kommentare(welt, "901")) == 1, _text(probe)
    assert _erneut_merker(welt) == [], _text(probe)
    echt = _capo(welt)
    assert "#901 erneut gemeldet" in echt.stdout, _text(echt)
    assert len(_kommentare(welt, "901")) == 2, _text(echt)


def _auf_altstand(welt: dict[str, Path], erneut_alt: bool = False) -> None:
    """Zustand wie vor der Kennung: nur ``N|Regel|closed_at`` (+ optional ``erneut|N|Regel``)."""
    datei = next(welt["zustand"].glob("*_900.json"))
    zustand = json.loads(datei.read_text(encoding="utf-8"))
    erledigt = [s for s in zustand["erledigt"] if not s.startswith(("geoeffnet|", "erneut|"))]
    if erneut_alt:
        erledigt.append("erneut|901|vps_ungleich_origin")
    zustand["erledigt"] = erledigt
    datei.write_text(json.dumps(zustand), encoding="utf-8")


def test_alter_merker_ohne_kennung_gilt_als_schon_geoeffnet_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Zustand von vor der Kennung (nur ``N|Regel|closed_at``): echter Takt öffnet nicht zweitens."""
    _einmal_geoeffnet(welt, monkeypatch)
    _auf_altstand(welt)
    ergebnis = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(ergebnis)
    assert "#901 erneut gemeldet" in ergebnis.stdout, _text(ergebnis)
    assert len(_kommentare(welt, "901")) == 2, _text(ergebnis)


def test_altmerker_dann_neuer_text_oeffnet_einmal_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Alter Merker gilt nur für den Verstoß von damals: späterer neuer Verstoß öffnet einmal."""
    _einmal_geoeffnet(welt, monkeypatch)
    _auf_altstand(welt)
    erster = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(erster)
    _commit(welt["repo"], "fix: Seite nachgebessert (#901)", {"web/seite.py": "x = 2\n"})
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-10)))
    zweiter = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open", _text(zweiter)
    assert len(_kommentare(welt, "901")) == 3, _text(zweiter)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-15)))
    dritter = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(dritter)


def test_alter_erneut_merker_meldet_nicht_noch_einmal_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Altformat ``erneut|N|Regel`` zählt als „schon gemeldet“: kein Zusatz-Kommentar."""
    _einmal_geoeffnet(welt, monkeypatch)
    gemeldet = _capo(welt)
    assert len(_kommentare(welt, "901")) == 2, _text(gemeldet)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-10)))
    _auf_altstand(welt, erneut_alt=True)
    danach = _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(danach)
    assert len(_kommentare(welt, "901")) == 2, _text(danach)


def test_neuer_vps_kopf_ohne_commit_oeffnet_nicht_neu_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmux: dict[str, Path]
) -> None:
    """Neuer Deploy ohne den fehlenden Commit: derselbe Verstoß, nur einmal melden."""
    _einmal_geoeffnet(welt, monkeypatch)
    vorher = _git(welt["repo"], "rev-parse", "HEAD~1")
    deploy = _git(welt["repo"], "commit-tree", f"{vorher}^{{tree}}", "-p", vorher, "-m", "deploy")
    for name, wert in _ssh_fake(welt, deploy).items():
        monkeypatch.setenv(name, wert)
    ergebnis = _capo(welt)
    assert f"HEAD {deploy[:7]}" in ergebnis.stdout, _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed", _text(ergebnis)
    assert "nicht wieder geöffnet" in _kommentare(welt, "901")[-1], _text(ergebnis)
