"""#781 (Linux-/Kern-Teil): ``bau.py`` / ``speicher.auf_platte_warten`` blockiert bei Platznot
über der Startsperre nicht durch einen synchronen Hausmeister-Rundgang über fremde Tickets,
und ``hausmeister._alle_ordner()`` cacht Ticket-Daten zwischen ``_schliesszeit()`` und
``_ticket_lange_zu()``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import hausmeister, prozessbaum, speicher, starter


def _git(ordner: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ordner), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **prozessbaum.fensterlos(),
    )


def _mini_klon(ziel: Path, origin_url: str, zweig: str) -> None:
    ziel.mkdir(parents=True, exist_ok=True)
    assert _git(ziel, "init", "-q", "-b", zweig).returncode == 0
    assert _git(ziel, "config", "user.email", "test@example.invalid").returncode == 0
    assert _git(ziel, "config", "user.name", "Test").returncode == 0
    assert _git(ziel, "remote", "add", "origin", origin_url).returncode == 0
    (ziel / "README.md").write_text("probe\n", encoding="utf-8")
    assert _git(ziel, "add", "README.md").returncode == 0
    assert _git(ziel, "commit", "-q", "-m", "Basis").returncode == 0


def _probe_welt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platte: str
) -> tuple[Path, list[tuple[int, str | None]]]:
    """Richtet drei Ordner (1 offen, 2 fremd geschlossen) unter Linux ein."""
    wurzeln = tmp_path / "wt"
    origin_url = "https://github.com/probe-org/app-repo.git"
    _mini_klon(wurzeln / "wt-1477", origin_url, "ticket/1477-eigen")
    _mini_klon(wurzeln / "wt-1498", origin_url, "ticket/1498-fremd-a")
    _mini_klon(wurzeln / "wt-1499", origin_url, "ticket/1499-fremd-b")

    maschine = tmp_path / "maschine.json"
    maschine.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "pfad": str(wurzeln / "hauptrepo"),
                        "slug": "probe-org/app-repo",
                        "ticket_repo": "probe-org/app-repo",
                    }
                ],
                "hausmeister": {
                    "wurzeln": [str(wurzeln / "wt-*")],
                    "ordner_praefixe": ["wt-"],
                    "geschuetzte_ordner": ["hauptrepo"],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(wurzeln / "wt-*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", platte)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "0")
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "linux")

    gh_abfragen: list[tuple[int, str | None]] = []

    def _fake_gh_ticket(ticket: int, repo: str | None) -> dict[str, object]:
        gh_abfragen.append((ticket, repo))
        if ticket == 1477:
            return {"state": "OPEN", "closedAt": None}
        return {"state": "CLOSED", "closedAt": "2026-11-01T10:00:00Z"}

    monkeypatch.setattr(hausmeister, "_gh_ticket", _fake_gh_ticket)
    monkeypatch.setattr(hausmeister, "_keine_spec_sitzung", lambda _ticket: None)
    monkeypatch.setattr(
        hausmeister,
        "_kein_prozess",
        lambda _ordner, **_kw: (_ for _ in ()).throw(hausmeister._Bleibt("Prozess läuft")),
    )
    monkeypatch.setattr(hausmeister.temp_ordner, "aufraeumen", lambda **_kw: [])
    monkeypatch.setattr(hausmeister.uebergabe_sichern, "sichern", lambda **_kw: None)
    return wurzeln, gh_abfragen


def test_auf_platte_warten_bei_platznot_ueber_startsperre_ohne_synchronen_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bei 9,5 % freier Platte (>= STARTSPERRE_ANTEIL, < PLATZNOT_ANTEIL) startet
    ``auf_platte_warten`` sofort ohne synchronen Fremd-Ticket-Scan und ohne falsche Wartezeile."""
    _wurzeln, gh_abfragen = _probe_welt(tmp_path, monkeypatch, "0.095")
    gestartet: list[tuple[list[str], dict[str, Any]]] = []

    def _fake_starte(befehl: list[str], **kw: Any) -> object:
        gestartet.append((list(befehl), dict(kw)))
        return object()

    monkeypatch.setattr(starter, "starte", _fake_starte)

    zyklen = speicher.auf_platte_warten(wer="bau 1477")
    assert zyklen == 0
    assert gh_abfragen == [], f"Synchroner Fremd-Ticket-Scan trotz 9,5 % frei: {gh_abfragen}"
    assert speicher.WARTE_TEXT not in capsys.readouterr().err
    # Einmaliger Hintergrund-Anstoß auf Linux; zweiter Start innerhalb des Abstands drosselt.
    assert len(gestartet) == 1
    assert gestartet[0][0][-3:] == ["aufraeumen", "--alles", "--loeschen"]
    assert gestartet[0][1].get("start_new_session") is True

    zyklen2 = speicher.auf_platte_warten(wer="bau 1478")
    assert zyklen2 == 0
    assert gh_abfragen == []
    assert len(gestartet) == 1, "Stempel muss wiederholte Hintergrund-Anstöße drosseln"


def test_hintergrund_stempel_aus_zukunft_gilt_nicht_als_frisch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uhr-Sprung wie #617: ein Stempel in der Zukunft unterdrückt den Hintergrund-Anstoß nicht."""
    _probe_welt(tmp_path, monkeypatch, "0.095")
    gestartet: list[list[str]] = []
    monkeypatch.setattr(starter, "starte", lambda befehl, **_kw: gestartet.append(list(befehl)))

    assert speicher.auf_platte_warten(wer="bau 1477") == 0
    assert len(gestartet) == 1
    stempel = hausmeister._hintergrund_stempel()
    zukunft = time.time() + 3600
    os.utime(stempel, (zukunft, zukunft))

    assert speicher.auf_platte_warten(wer="bau 1478") == 0
    assert len(gestartet) == 2


def test_auf_platte_warten_unter_startsperre_raeumt_weiterhin_synchron(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unterhalb ``STARTSPERRE_ANTEIL`` räumt ``auf_platte_warten`` weiter synchron auf und meldet vorab."""
    platte_datei = tmp_path / "platte.txt"
    platte_datei.write_text("0.03\n", encoding="utf-8")
    _wurzeln, gh_abfragen = _probe_welt(tmp_path, monkeypatch, str(platte_datei))
    pausen: list[float] = []

    def _schlafen(s: float) -> None:
        pausen.append(s)
        platte_datei.write_text("0.50\n", encoding="utf-8")

    zyklen = speicher.auf_platte_warten(wer="bau 1477", schlafen=_schlafen)
    assert zyklen == 1
    assert pausen == [speicher.PLATTE_TAKT_S]
    assert speicher.WARTE_TEXT in capsys.readouterr().err
    # Synchroner Rundgang lief, aber jeder Ordner wurde dank Ticket-Cache höchstens einmal gefragt.
    gefragte = [nr for nr, _ in gh_abfragen]
    assert sorted(gefragte) == [1477, 1498, 1499]


def test_alle_ordner_cacht_ticket_daten_zwischen_schliesszeit_und_regeln(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Im Hausmeister-Rundgang bei Platznot wird jedes Ticket zwischen ``_schliesszeit`` und
    ``_ticket_lange_zu`` höchstens einmal per ``gh`` abgefragt — ein zweiter Rundgang fragt frisch."""
    _wurzeln, gh_abfragen = _probe_welt(tmp_path, monkeypatch, "0.095")

    rund1 = hausmeister.rundgang(loeschen=True)
    assert rund1.gelaufen is True
    gefragte1 = [nr for nr, _ in gh_abfragen]
    assert gefragte1.count(1477) == 1
    assert gefragte1.count(1498) == 1
    assert gefragte1.count(1499) == 1

    # Zweiter Rundgang startet mit frischem Cache — keine veralteten Daten über Rundgänge hinweg.
    gh_abfragen.clear()
    rund2 = hausmeister.rundgang(loeschen=True)
    assert rund2.gelaufen is True
    gefragte2 = [nr for nr, _ in gh_abfragen]
    assert sorted(gefragte2) == [1477, 1498, 1499]


def test_alle_ordner_cacht_auch_fehler_antwort_none_innerhalb_eines_rundgangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert ``_gh_ticket`` (``None``) schon in ``_schliesszeit``, fragt ``_ticket_lange_zu``
    im selben Rundgang nicht erneut ab — der Ordner bleibt mit Fehler."""
    _probe_welt(tmp_path, monkeypatch, "0.095")
    aufrufe: list[tuple[int, str | None]] = []

    def _gh_kaputt(ticket: int, repo: str | None) -> object:
        aufrufe.append((ticket, repo))
        return None

    monkeypatch.setattr(hausmeister, "_gh_ticket", _gh_kaputt)

    rund = hausmeister.rundgang(loeschen=True)
    assert rund.gelaufen is True
    assert rund.fehler == 3
    gefragte = [nr for nr, _ in aufrufe]
    assert sorted(gefragte) == [1477, 1498, 1499]
