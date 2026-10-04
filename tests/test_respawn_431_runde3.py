"""#431 Fixrunde 3: Befund 6 (Bereit-Frist vs. Speicher-Sperre) und Befund 4
(Sessions-Datei bei Abbruch zurücksetzen), Befunde 2, 3, 8."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_respawn_431 import (
    ALT_ZIEL,
    NEU_ZIEL,
    SPEC,
    TICKET,
    FakeWerkzeug,
    _TmuxFehler,
    _weiter_getippt,
    umgebung,  # noqa: F401  (Fixture)
)

from to_spawn import respawn, sessions_datei

SPEICHER_SCHIRM = (
    f"bau {TICKET} wartet auf Speicher (Zyklus 1, Takt 60 s): "
    "13 Claude-Sessions (Obergrenze 12)"
)


def _lauf(repo: Path, fake: FakeWerkzeug, warte_max: float) -> respawn.Ergebnis:
    return respawn.abloesen(repo, SPEC, TICKET, werkzeug=fake, warte_max=warte_max)


def _speicher_sperre(fake: FakeWerkzeug, dauer_s: float) -> None:
    """Neuer Bildschirm zeigt ``dauer_s`` lang die Speicher-Wartezeile von bau.py."""
    original_starten = fake.fenster_starten
    original_schirm = fake.bildschirm
    start: list[float] = []

    def starten(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        start.append(fake.zeit)
        return original_starten(sitzung, name, cwd, befehl)

    def bildschirm(ziel: str) -> str:
        if ziel == NEU_ZIEL and start and fake.zeit - start[0] < dauer_s:
            fake.aufrufe.append(("bildschirm", ziel))
            return SPEICHER_SCHIRM
        return original_schirm(ziel)

    fake.fenster_starten = starten  # type: ignore[method-assign]
    fake.bildschirm = bildschirm  # type: ignore[method-assign]


# --- Befund 6: Speicher-Sperre ist Warten, kein Fehler --------------------------------


def test_r3_b6_speicher_warten_laenger_als_bereit_frist_ist_kein_fehler(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    _speicher_sperre(fake, respawn.BEREIT_MAX_S + 180)
    erg = _lauf(repo, fake, warte_max=600)
    assert erg.exit == 0, erg.zeile
    assert "Speicher" in erg.zeile
    assert "300 s" in erg.zeile, erg.zeile


def test_r3_b6_speicher_warten_ueber_warte_max_bricht_mit_wartezeit_ab(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    _speicher_sperre(fake, 10_000)
    erg = _lauf(repo, fake, warte_max=400)
    assert erg.exit == 1, erg.zeile
    assert "Speicher" in erg.zeile
    assert "400 s" in erg.zeile, erg.zeile
    assert _weiter_getippt(fake)


def test_r3_b6_ohne_speicher_bleibt_bereit_frist(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    """Hängt die neue Session ohne Speicher-Zeile, gilt weiter BEREIT_MAX_S."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.bildschirm

    def bildschirm(ziel: str) -> str:
        return "lädt …" if ziel == NEU_ZIEL else original(ziel)

    fake.bildschirm = bildschirm  # type: ignore[method-assign]
    start = fake.zeit
    erg = _lauf(repo, fake, warte_max=1800)
    assert erg.exit == 1, erg.zeile
    assert "nicht bereit" in erg.zeile
    assert fake.zeit - start < respawn.BEREIT_MAX_S + 10


def test_r3_b6_wartet_auf_speicher_nur_letzte_speicher_zeile() -> None:
    assert respawn._wartet_auf_speicher(SPEICHER_SCHIRM)
    frei = SPEICHER_SCHIRM + f"\nbau {TICKET}: Speicher wieder frei nach 3 Wartezyklen"
    assert not respawn._wartet_auf_speicher(frei)
    assert not respawn._wartet_auf_speicher("Claude Code\n❯ ")


# --- Befund 4: Sessions-Datei bei Abbruch zurücksetzen --------------------------------


def _bau_schreibt_sessions(fake: FakeWerkzeug, repo: Path) -> None:
    """Simuliert bau.py: schreibt beim Fensterstart die neue Gesprächs-ID."""
    original = fake.fenster_starten

    def starten(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        sessions_datei.schreiben(repo, str(TICKET), "neu-verworfen", repo, 1)
        return original(sitzung, name, cwd, befehl)

    fake.fenster_starten = starten  # type: ignore[method-assign]


def test_r3_b4_abbruch_schreibt_alte_sessions_datei_zurueck(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    datei = sessions_datei.pfad(repo, str(TICKET))
    sessions_datei.schreiben(repo, str(TICKET), "alt-1234", repo, 3)
    vorher = datei.read_bytes()
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    _bau_schreibt_sessions(fake, repo)
    erg = _lauf(repo, fake, warte_max=30)
    assert erg.exit == 2, erg.zeile
    assert datei.read_bytes() == vorher
    assert json.loads(datei.read_text(encoding="utf-8"))["session_id"] == "alt-1234"


def test_r3_b4_abbruch_ohne_vorherige_datei_loescht_sie(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    datei = sessions_datei.pfad(repo, str(TICKET))
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    _bau_schreibt_sessions(fake, repo)
    erg = _lauf(repo, fake, warte_max=30)
    assert erg.exit == 2, erg.zeile
    assert not datei.exists()


def test_r3_b4_erfolg_behaelt_neue_sessions_datei(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    sessions_datei.schreiben(repo, str(TICKET), "alt-1234", repo, 3)
    fake = FakeWerkzeug(wt)
    _bau_schreibt_sessions(fake, repo)
    erg = _lauf(repo, fake, warte_max=600)
    assert erg.exit == 0, erg.zeile
    assert sessions_datei.lesen(repo, str(TICKET))["session_id"] == "neu-verworfen"  # type: ignore[index]


def test_r3_b6_spaeterer_abbruch_nennt_speicher_wartezeit(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    _speicher_sperre(fake, 200)
    erg = _lauf(repo, fake, warte_max=300)
    assert erg.exit == 2, erg.zeile
    assert "200 s auf Speicher" in erg.zeile, erg.zeile


# --- Befund 2: Signal während des Aufräumens nach normalem Abbruch -------------------


def test_r3_b2_signal_waehrend_aufraeumen_verlaesst_abloesen_nicht(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    """Exit-2-Abbruch räumt auf; ein SIGTERM mitten darin bricht das Aufräumen nicht ab."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    original = fake.fenster_schliessen
    geschickt: list[bool] = []

    def schliessen(ziel: str) -> None:
        if not geschickt:
            geschickt.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
        original(ziel)

    fake.fenster_schliessen = schliessen  # type: ignore[method-assign]
    erg = _lauf(repo, fake, warte_max=30)
    assert geschickt
    assert erg.exit == 2, erg.zeile
    assert _weiter_getippt(fake), "Aufräumen wurde unterbrochen"
    assert "SIGTERM" in erg.zeile, erg.zeile


# --- Befund 3: unlesbarer Bildschirm der alten Session ist nicht „ruhig“ --------------


def test_r3_b3_tmux_bildschirm_fehler_liefert_none() -> None:
    assert _TmuxFehler("server exited unexpectedly").bildschirm("=spec:@1") is None


def test_r3_b3_unlesbarer_alter_bildschirm_wartet_und_meldet(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.bildschirm

    def bildschirm(ziel: str) -> str | None:
        if ziel == ALT_ZIEL:
            fake.aufrufe.append(("bildschirm", ziel))
            return None
        return original(ziel)

    fake.bildschirm = bildschirm  # type: ignore[method-assign,assignment]
    erg = _lauf(repo, fake, warte_max=600)
    assert erg.exit == 0, erg.zeile
    assert "nicht lesbar" in erg.zeile, erg.zeile
    assert fake.zeit >= respawn.ALT_RUHE_MAX_S


# --- Befund 8: Sessions-Datei nicht zurücksetzbar → gemeldet, nicht verschluckt -------


def test_r3_b8_schreibfehler_beim_zuruecksetzen_wird_gemeldet(
    umgebung: tuple[Path, Path],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo, wt = umgebung
    datei = sessions_datei.pfad(repo, str(TICKET))
    sessions_datei.schreiben(repo, str(TICKET), "alt-1234", repo, 3)
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    original = fake.fenster_starten

    def starten(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        datei.unlink()
        datei.mkdir()  # Zurückschreiben scheitert jetzt mit OSError
        return original(sitzung, name, cwd, befehl)

    fake.fenster_starten = starten  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger=respawn.log.name):
        erg = _lauf(repo, fake, warte_max=30)
    assert erg.exit == 2, erg.zeile
    assert f"Sessions-Datei {datei.name} nicht zurückgesetzt" in erg.zeile, erg.zeile
    assert _weiter_getippt(fake)
    assert "neues Fenster zu" in erg.zeile
    assert any("nicht zurückgesetzt" in r.getMessage() for r in caplog.records)


def test_r3_b8_unlesbare_sessions_datei_wird_bei_abbruch_gemeldet(
    umgebung: tuple[Path, Path],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo, wt = umgebung
    datei = sessions_datei.pfad(repo, str(TICKET))
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.mkdir()  # Lesen vor Schritt b scheitert mit OSError
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    with caplog.at_level(logging.ERROR, logger=respawn.log.name):
        erg = _lauf(repo, fake, warte_max=30)
    assert erg.exit == 2, erg.zeile
    assert f"Sessions-Datei {datei.name} nicht zurückgesetzt" in erg.zeile, erg.zeile
    assert _weiter_getippt(fake)
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
