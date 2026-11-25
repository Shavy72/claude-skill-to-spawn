"""#425 Teil C2: Komplett-Prüfung im Takt (alle auf Staging) und als Tür vor Live.

Echt laufen: der capo-Takt (CLI ``skripte/capo.py``) im Wegwerf-Repo aus ``test_waechter_213``,
der Unterbefehl ``to_spawn.py komplett`` und ein echter kleiner Prüf-Befehl, der jede Ausführung
in eine Zähldatei schreibt. Gestellt ist nur ``gh`` (Ersatz aus #213).

Ausroll-Frage: ein Repo ohne Gruppe ``pruefung`` (laufende Spec auf dem Bau-Server) verhält sich
im Takt exakt wie vorher — kein Lauf, keine Komplett-Zeile, SPEC FERTIG wie bisher.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_komplett_je_spec_425 import _befehl, _laeufe, _rot
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _konfig,
    _text,
    welt,
)

from to_spawn import capo, config, komplett_lauf, prozessbaum
from to_spawn.pruefplan import Moment

SKILL = Path(__file__).resolve().parents[1]
STAGING = {"url": "http://127.0.0.1:8001/"}


def _eingerichtet(welt: dict[str, Path], zaehl: Path, *, staging: bool = True) -> None:
    _konfig(
        welt,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        staging=STAGING if staging else {"ohne": True},
    )


def _alle_zu(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def _zaehl(welt: dict[str, Path]) -> Path:
    return welt["tmp"] / "zaehl.txt"


# --- (a) Staging-Moment im Takt -----------------------------------------------------------------


def test_takt_alle_auf_staging_laeuft_einmal_gruen(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _alle_zu(welt)
    erster = _capo(welt)
    zweiter = _capo(welt)
    assert "Komplett-Prüfung grün" in erster.stdout, _text(erster)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert _laeufe(zaehl) == 1, _text(zweiter)
    assert "Komplett-Prüfung" not in zweiter.stdout, _text(zweiter)


def test_takt_rot_haelt_spec_fertig_zurueck(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _alle_zu(welt)
    _rot(zaehl)
    rot = _capo(welt)
    assert "Komplett-Prüfung rot" in rot.stdout, _text(rot)
    assert "Handlung" in rot.stdout, _text(rot)
    assert "SPEC FERTIG" not in rot.stdout, _text(rot)
    assert rot.returncode == 1, _text(rot)
    zaehl.with_name("exit.txt").unlink()
    # #455: rot je Kopf läuft im Takt nicht neu — nach dem Fix von Hand anstoßen.
    gemerkt = _capo(welt)
    assert "schon rot" in gemerkt.stdout and "SPEC FERTIG" not in gemerkt.stdout, _text(gemerkt)
    assert _laeufe(zaehl) == 1
    assert _tuer(welt["repo"], "--moment", "alle-auf-staging").returncode == 0
    gruen = _capo(welt)
    assert "SPEC FERTIG" in gruen.stdout, _text(gruen)
    assert _laeufe(zaehl) == 2


def test_takt_mit_offenem_ticket_kein_lauf(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout


def test_takt_ohne_staging_kein_lauf_spec_fertig(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl, staging=False)
    _alle_zu(welt)
    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout


def test_ausroll_repo_ohne_einrichtung_takt_wie_vorher(welt: dict[str, Path]) -> None:
    """Laufende Spec ohne ``pruefung``: kein Lauf, keine neue Zeile, kein Fehler."""
    _alle_zu(welt)
    ergebnis = _capo(welt)
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)
    assert "Komplett" not in _text(ergebnis), _text(ergebnis)
    assert "FEHLER" not in ergebnis.stdout, _text(ergebnis)
    assert not (welt["repo"] / ".to-spawn" / "pruef" / f"komplett-{SPEC}.json").exists()


def test_ausnahme_bricht_takt_nicht_ab(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _eingerichtet(welt, _zaehl(welt))
    _alle_zu(welt)

    def kaputt(*_a: object, **_k: object) -> komplett_lauf.Ergebnis:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(komplett_lauf, "komplett_einmal", kaputt)
    erg = capo.tick(
        welt["repo"],
        int(SPEC),
        "test/wegwerf",
        config.lade(welt["repo"]),
        wt_basis=str(welt["wt"]),
    )
    text = "\n".join(erg.zeilen)
    assert "FEHLER: Komplett-Prüfung" in text and "kaputt" in text, text
    assert not erg.fertig
    assert erg.exit_code == 1


def test_takt_laeuft_schon_meldet_und_haelt_fertig(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _eingerichtet(welt, _zaehl(welt))
    _alle_zu(welt)
    belegt = komplett_lauf.Ergebnis(False, True, "Komplett-Prüfung läuft schon", None, "", laeuft=True)
    monkeypatch.setattr(komplett_lauf, "komplett_einmal", lambda *_a, **_k: belegt)
    erg = capo.tick(
        welt["repo"],
        int(SPEC),
        "test/wegwerf",
        config.lade(welt["repo"]),
        wt_basis=str(welt["wt"]),
    )
    text = "\n".join(erg.zeilen)
    assert "läuft schon" in text and "FEHLER" not in text, text
    assert not erg.fertig


# --- (b) Tür vor Live ---------------------------------------------------------------------------


def _tuer(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "komplett", SPEC, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        timeout=60,
        check=False,
    )


def test_tuer_vor_live_gruen_rot_und_einmal(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl, staging=False)
    _rot(zaehl)
    rot = _tuer(welt["repo"], "--moment", "vor-live")
    assert rot.returncode == 1, _text(rot)
    assert "rot" in rot.stdout and len(rot.stdout.strip().splitlines()) == 1, _text(rot)
    zaehl.with_name("exit.txt").unlink()
    gruen = _tuer(welt["repo"], "--moment", "vor-live")
    nochmal = _tuer(welt["repo"], "--moment", "vor-live")
    assert gruen.returncode == 0 and "grün" in gruen.stdout, _text(gruen)
    assert nochmal.returncode == 0 and "schon grün" in nochmal.stdout, _text(nochmal)
    assert _laeufe(zaehl) == 2


def test_tuer_unbekannter_moment_exit_2(welt: dict[str, Path]) -> None:
    erg = _tuer(welt["repo"], "--moment", "irgendwann")
    assert erg.returncode == 2, _text(erg)


def test_tuer_alle_auf_staging_und_nicht_eingerichtet(welt: dict[str, Path]) -> None:
    ohne = _tuer(welt["repo"], "--moment", "vor-live")
    assert ohne.returncode == 0 and "nicht eingerichtet" in ohne.stdout, _text(ohne)
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    erg = _tuer(welt["repo"], "--moment", "alle-auf-staging")
    assert erg.returncode == 0 and "grün" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 1


# --- (d) Sperre gegen Doppellauf ----------------------------------------------------------------


def _langsamer_lauf(klon: Path, zaehl: Path) -> subprocess.Popen[str]:
    """Zweiter Prozess: Komplett-Prüfung, deren Befehl nach dem Zählen 5 s schläft."""
    code = (
        "import sys, pathlib, json\n"
        "from to_spawn import komplett_lauf, config\n"
        "from to_spawn.pruefplan import Moment\n"
        "klon = pathlib.Path(sys.argv[1])\n"
        "erg = komplett_lauf.komplett_einmal(klon, int(sys.argv[2]), Moment.VOR_LIVE, config.lade(klon))\n"
        "print(json.dumps([erg.gelaufen, erg.ok]))\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(klon), SPEC],
        cwd=str(SKILL),
        stdout=subprocess.PIPE,
        text=True,
        **prozessbaum.ohne_fenster(),
    )


def _warte_auf(zaehl: Path) -> None:
    ende = time.monotonic() + 20
    while _laeufe(zaehl) == 0:
        assert time.monotonic() < ende, "Prüf-Befehl startete nicht"
        time.sleep(0.05)


def _langsam_eingerichtet(welt: dict[str, Path], zaehl: Path) -> None:
    befehl = _befehl(zaehl) + " && sleep 5"
    _konfig(welt, pruefung={"schnell": "true", "komplett": befehl}, staging={"ohne": True})


def test_sperre_zweiter_aufruf_waehrend_lauf(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _langsam_eingerichtet(welt, zaehl)
    erster = _langsamer_lauf(welt["repo"], zaehl)
    try:
        _warte_auf(zaehl)
        zweit = komplett_lauf.komplett_einmal(welt["repo"], int(SPEC), Moment.VOR_LIVE, config.lade(welt["repo"]))
        assert (zweit.gelaufen, zweit.ok, zweit.laeuft) == (False, True, True)
        assert "läuft schon" in zweit.grund
    finally:
        aus, _ = erster.communicate(timeout=30)
    assert json.loads(aus) == [True, True]
    assert _laeufe(zaehl) == 1


def _hart_beenden(prozess: subprocess.Popen[str]) -> None:
    """Beendet ``prozess`` samt Nachfahren hart über ``prozessbaum`` (Linux + Windows, #647).

    Unter Windows fehlt ``signal.SIGKILL``; jedes Signal beendet dort per ``TerminateProcess``.
    ``prozessbaum.baum`` liefert die Wurzel zuerst — so stirbt der Halter der Sperre hart,
    bevor Kindprozesse (``bash``/``sleep``) enden, und auch ein Windows-venv-Kindprozess
    samt Nachfahren wird mit beendet.
    """
    hart = getattr(signal, "SIGKILL", signal.SIGTERM)
    pids = prozessbaum.baum(prozess.pid)
    prozessbaum.beenden(pids, ((hart, 5.0),), warten_auf=pids[1:])
    prozess.wait(timeout=10)
    if prozess.stdout is not None:
        prozess.stdout.close()


def test_sperre_abgestuerzter_lauf_sperrt_nicht(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _langsam_eingerichtet(welt, zaehl)
    erster = _langsamer_lauf(welt["repo"], zaehl)
    try:
        _warte_auf(zaehl)
    finally:
        _hart_beenden(erster)
    _konfig(
        welt,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        staging={"ohne": True},
    )
    erg = komplett_lauf.komplett_einmal(welt["repo"], int(SPEC), Moment.VOR_LIVE, config.lade(welt["repo"]))
    assert (erg.gelaufen, erg.ok) == (True, True), erg.grund
    assert _laeufe(zaehl) == 2


def test_sperre_abgestuerzter_lauf_ohne_sigkill(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """#647: Unter Windows fehlt ``signal.SIGKILL`` — Abbruch läuft trotzdem über ``prozessbaum``."""
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    test_sperre_abgestuerzter_lauf_sperrt_nicht(welt)
