"""Ticket #289: Aufseher meldet „arbeitet“ ohne Blick ins Fenster (kein Claim ohne Beweis).

Deckt alle vier Abnahme-Punkte maschinell ab:
1. Nach jedem Anstoß Wirkung am Fenster bzw. Stand prüfen (ein Befehl
   ``to_spawn.py aufseher-stand <S> --ticket <N>`` sowie Eingriffs-Leiter) und Beleg mit
   Zeit, letzter Zeile und Zustand nennen.
2. Stand-Abfrage unterscheidet ``arbeitet``, ``wartet``, ``blockiert``, ``fertig`` —
   ``blockiert`` wird an der Meldung der Session erkannt, nicht am bloßen Vorhandensein
   des Fensters; ``staging:fertig`` gilt als ``wartet``, nicht ``fertig``.
3. Nachstellung des echten Falls: Session steht nach Anstoß weiter auf „blockiert“ mit
   ``/implement``-Wortlaut → Beleg und Meldung lauten ``blockiert`` mit Wortlaut, nicht
   ``arbeitet``; erst nach echter Arbeitsaufnahme lautet der Beleg ``arbeitet``.
4. Aufseher-Meldung ohne gültige Beleg-Zeile (oder im Widerspruch zum Beleg) gilt im
   Aufseher-Takt als Fehler (Exit 1, Merker bleibt unverändert).
"""

# ruff: noqa: F811 — Fixtures ``repo``, ``ordner``, ``welt`` kommen aus den Nachbar-Tests.

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_aufseher_stand_430 import (  # noqa: F401
    JETZT,
    SPEC,
    FakeWelt,
    bau_log,
    bildschirm,
    ordner,
    repo,
    zeile_fuer,
)
from test_waechter_takt_316 import CLAUDE_AUFZ, PY, _alt_merker, _merker, _takt, welt  # noqa: F401

from to_spawn import aufseher_stand, leiter, ticket_zustand, ticketstand, waechter_takt

BLOCKIERT_WORTLAUT = (
    "Den Skill implement darf ich nicht selbst starten, er braucht die Nachricht von außen. "
    "Bitte schick hier diese Nachricht: /implement Ticket #1479."
)


# --- Abnahme 2: Stand-Abfrage unterscheidet arbeitet / wartet / blockiert / fertig ---


def test_stand_unterscheidet_alle_vier_zustaende(repo: Path, ordner: Path) -> None:
    """arbeitet, wartet, blockiert und fertig werden sauber getrennt — Fenster allein ≠ arbeitet."""
    w = FakeWelt()
    w.ticket(1479)
    w.ticket(1480)
    w.ticket(1481)
    w.ticket(1482, zu=True)
    w.ticket(1483)
    w.tickets[1483]["labels"] = [{"name": ticket_zustand.STAGING_FERTIG_LABEL}]

    w.fenster[1479] = (JETZT - 420, bildschirm(f"● {BLOCKIERT_WORTLAUT}"))
    w.fenster[1480] = (JETZT - 10, bildschirm("● Baue Änderung\n✻ Baking… (esc to interrupt)"))
    w.fenster[1481] = (JETZT - 300, bildschirm("✻ Baked for 22s"))
    w.fenster[1483] = (JETZT - 600, bildschirm("Auf Staging grün, warte auf Live."))

    q = w.quellen(repo)
    assert aufseher_stand.beleg_fuer(SPEC, 1479, q, ordner=ordner).zustand == "blockiert"
    assert aufseher_stand.beleg_fuer(SPEC, 1480, q, ordner=ordner).zustand == "arbeitet"
    assert aufseher_stand.beleg_fuer(SPEC, 1481, q, ordner=ordner).zustand == "wartet"
    assert aufseher_stand.beleg_fuer(SPEC, 1482, q, ordner=ordner).zustand == "fertig"
    # staging:fertig ≠ abgeschlossen: stehendes Fenster zählt als wartet, nicht fertig.
    assert aufseher_stand.beleg_fuer(SPEC, 1483, q, ordner=ordner).zustand == "wartet"


def test_blockiert_im_bau_log_auch_ohne_tmux_erkannt(repo: Path, ordner: Path) -> None:
    """PC-Weg (ohne tmux): Bau-Log mit jüngster Phase ``blockiert`` wird als ``blockiert`` erkannt."""
    w = FakeWelt()
    w.ticket(1484)
    bau_log(
        repo,
        1484,
        {
            "ts": "2026-11-23T03:01:00+01:00",
            "typ": "blockiert",
            "text": BLOCKIERT_WORTLAUT,
        },
    )
    q = aufseher_stand.Quellen(
        repo=repo,
        kinder=w.kinder,
        tmux=lambda _args: None,
        jetzt=lambda: JETZT,
        pc_still=lambda _n: 300.0,
    )
    beleg = aufseher_stand.beleg_fuer(SPEC, 1484, q, ordner=ordner)
    assert beleg.zustand == "blockiert"
    assert "/implement Ticket #1479" in beleg.letzte_zeile


def test_verneinung_nicht_blockiert_ist_kein_blockiert(repo: Path, ordner: Path) -> None:
    """„nicht mehr blockiert“ darf nicht fälschlich als ``blockiert`` gewertet werden."""
    w = FakeWelt()
    w.ticket(1485)
    w.fenster[1485] = (JETZT - 120, bildschirm("Blocker erledigt, nicht mehr blockiert."))
    beleg = aufseher_stand.beleg_fuer(SPEC, 1485, w.quellen(repo), ordner=ordner)
    assert beleg.zustand == "wartet"


def test_blocker_wartezeile_ist_wartet_nicht_arbeitet(repo: Path, ordner: Path) -> None:
    """``bau.py``-Wartezeile im Fenster zählt als ``wartet``, nicht als ``arbeitet``."""
    w = FakeWelt()
    w.ticket(1486)
    w.fenster[1486] = (
        JETZT - 60,
        "Ticket #1486 wartet (#1485 offen) — nächste Prüfung in 5 min [03:05]",
    )
    beleg = aufseher_stand.beleg_fuer(SPEC, 1486, w.quellen(repo), ordner=ordner)
    assert beleg.zustand == "wartet"


def test_stand_kurzzeile_und_ticketstand_zeigen_blockiert(repo: Path, ordner: Path) -> None:
    """Auch ``aufseher-stand`` und ``ticketstand`` weisen ``blockiert`` samt Wortlaut aus."""
    w = FakeWelt()
    w.ticket(1479)
    w.fenster[1479] = (JETZT - 420, bildschirm(f"● {BLOCKIERT_WORTLAUT}"))
    ausgabe = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    z = zeile_fuer(ausgabe, 1479)
    assert " · blockiert 7 min · " in z
    assert "Den Skill implement darf ich nicht selbst starten" in z
    assert "1 blockiert" in ausgabe.splitlines()[0]

    erg = aufseher_stand.sammeln(SPEC, w.quellen(repo), aufseher_stand._vorher(SPEC, ordner))
    tz, hinweis = ticketstand.zustand(w.tickets[1479], erg.lagen[0])
    assert tz == ticketstand.BLOCKIERT
    assert "Den Skill implement" in hinweis


# --- Abnahme 1 + 3: Nachstellung nach Anstoß → Beleg mit Zeit, letzter Zeile, Zustand ---


def test_nachstellung_nach_anstoss_blockiert_statt_arbeitet(repo: Path, ordner: Path) -> None:
    """Abnahme 3: Anstoß „Blocker erledigt“ bei blockierter Session → Beleg ``blockiert`` mit Wortlaut."""
    w = FakeWelt()
    w.ticket(1479)
    w.fenster[1479] = (
        JETZT - 420,
        bildschirm(f"❯ Blocker erledigt, jetzt bauen\n● {BLOCKIERT_WORTLAUT}"),
    )
    beleg = aufseher_stand.nach_anstoss_beleg(SPEC, 1479, w.quellen(repo), ordner=ordner)
    zeile = aufseher_stand.beleg_zeile(beleg)
    assert beleg.zustand == "blockiert"
    assert BLOCKIERT_WORTLAUT in beleg.letzte_zeile
    assert "blockiert" in zeile
    assert "arbeitet" not in zeile
    assert BLOCKIERT_WORTLAUT in zeile
    assert aufseher_stand.pruefe_meldung(f"#1479 steht nach Anstoß weiter auf blockiert.\n{zeile}") is None

    # Erst nach echtem /implement-Anstoß arbeitet die Session wirklich.
    w.uhr = JETZT + 360
    w.fenster[1479] = (
        w.uhr,
        bildschirm("❯ /implement Ticket #1479\n● Starte Implementierung\n✻ Baking… (esc to interrupt)"),
    )
    danach = aufseher_stand.nach_anstoss_beleg(SPEC, 1479, w.quellen(repo), ordner=ordner)
    assert danach.zustand == "arbeitet"
    assert "arbeitet" in aufseher_stand.beleg_zeile(danach)


def test_cli_aufseher_stand_ticket_liefert_beleg_zeile(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Abnahme 1: Ein Befehl ``to_spawn.py aufseher-stand <S> --ticket <N>`` liefert die Beleg-Zeile."""
    from test_aufseher_stand_430 import _cli_welt

    stand_ordner = _cli_welt(tmp_path, monkeypatch)
    welt_datei = Path(os.environ["FAKE_WELT"])
    daten = json.loads(welt_datei.read_text(encoding="utf-8"))
    daten["panes"][f"=spec-{SPEC}:1"] = bildschirm(f"● {BLOCKIERT_WORTLAUT}")
    welt_datei.write_text(json.dumps(daten), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("to_spawn_cli_289", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    code = cli.main(
        [
            "aufseher-stand",
            str(SPEC),
            "--ticket",
            "450",
            "--gh-repo",
            "o/r",
            "--stand-ordner",
            str(stand_ordner),
        ]
    )
    raus = capsys.readouterr().out.strip()
    assert code == 0, raus
    assert raus.startswith("Beleg #450 · ")
    assert " · blockiert · " in raus
    assert BLOCKIERT_WORTLAUT in raus
    assert aufseher_stand.pruefe_meldung(raus) is None


def test_leiter_prueft_wirkung_nach_anstoss_und_nennt_beleg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme 1 + 3: Nach dem Anstupsen holt die Leiter den Beleg und meldet ``blockiert`` mit Wortlaut."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)

    class FakeWerkzeug:
        def __init__(self) -> None:
            self.getippt: list[tuple[str, str]] = []

        def tippen(self, ziel: str, text: str) -> None:
            self.getippt.append((ziel, text))

    class FakeUmweltMitBeleg:
        def __init__(self) -> None:
            self.wz = FakeWerkzeug()
            self.beleg_gefragt = 0

        def jetzt(self) -> float:
            return JETZT

        def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
            return leiter.Lage(
                stufe="offen",
                fenster=aufseher_stand.STILL,
                still_min=25,
                kontext_k=None,
                prompt_datei=False,
                ziel=f"=spec-{spec}:1",
            )

        def handoff_k(self) -> float:
            return 250.0

        def worktree(self, ticket: int) -> Path:
            return wt

        def werkzeug(self) -> Any:
            return self.wz

        def beleg(self, spec: int, ticket: int) -> aufseher_stand.StandBeleg:
            self.beleg_gefragt += 1
            return aufseher_stand.StandBeleg(
                ticket=ticket,
                zeit="03:08",
                zustand=aufseher_stand.BLOCKIERT,
                letzte_zeile=BLOCKIERT_WORTLAUT,
            )

    u = FakeUmweltMitBeleg()
    erg = leiter.eingreifen(SKILL, SPEC, 1479, umwelt=u)
    assert erg.exit == 0
    assert len(u.wz.getippt) == 1
    assert u.beleg_gefragt == 1
    assert "Beleg #1479 · 03:08 · blockiert · " in erg.zeile
    assert BLOCKIERT_WORTLAUT in erg.zeile


# --- Abnahme 4: Meldung ohne Beleg-Zeile gilt im Aufseher-Takt als Fehler ---


@pytest.mark.parametrize(
    "meldung",
    [
        "",
        "beide arbeiten",
        "#1479 und #1484 arbeiten nach dem Hinweis beide weiter.",
        "Beleg #1479 · blockiert · „Zeit fehlt“",
        "Beleg #1479 · 03:08 · blockiert · „“",
        "Beleg #1479 · 03:08 · unbekannt · „Zeile da“",
        f"#1479 arbeitet.\nBeleg #1479 · 03:08 · blockiert · „{BLOCKIERT_WORTLAUT}“",
        f"Beleg #1479 · 03:08 · arbeitet · „{BLOCKIERT_WORTLAUT}“",
    ],
)
def test_pruefe_meldung_lehnt_fehlenden_oder_widerspruechlichen_beleg_ab(meldung: str) -> None:
    fehler = waechter_takt.pruefe_meldung(meldung)
    assert fehler is not None and fehler.strip()


def test_pruefe_meldung_akzeptiert_gueltigen_beleg() -> None:
    gueltig = f"#1479 steht auf blockiert (Wortlaut unten).\nBeleg #1479 · 03:08 · blockiert · „{BLOCKIERT_WORTLAUT}“"
    assert waechter_takt.pruefe_meldung(gueltig) is None


def test_takt_unterprozess_lehnt_meldung_ohne_beleg_ab_und_akzeptiert_mit_beleg(
    welt: dict[str, Any],
) -> None:
    """Abnahme 4 (Weg-Test): ``to_spawn.py takt`` bricht bei Meldung ohne Beleg mit Exit 1 ab."""
    _alt_merker(welt)

    # Ohne Beleg-Zeile → Exit 1, FEHLER, Merker bleibt unverändert.
    welt["env"]["CLAUDE_AUFZEICHNUNG_AUSGABE"] = "Beide Bau-Sessions (#297, #301) arbeiten."
    rot = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert "FEHLER" in rot.stdout and "Beleg" in rot.stdout
    assert _merker(welt) == {}

    # Mit gültiger Beleg-Zeile → Exit 0, Merker fortgeschrieben.
    welt["env"]["CLAUDE_AUFZEICHNUNG_AUSGABE"] = (
        f"#297 steht auf blockiert.\nBeleg #297 · 03:08 · blockiert · „{BLOCKIERT_WORTLAUT}“"
    )
    gruen = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert _merker(welt) and _merker(welt).get("zuletzt")


def test_cli_takt_pruefe_meldung_exit_codes(welt: dict[str, Any]) -> None:
    """Direkte Takt-Prüfung per CLI: ohne Beleg Exit 1, mit Beleg Exit 0."""
    ohne = subprocess.run(
        [
            PY,
            str(SKILL / "to_spawn.py"),
            "takt",
            "293",
            "--pruefe-meldung",
            "beide arbeiten",
        ],
        cwd=str(SKILL),
        env=welt["env"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ohne.returncode == 1
    assert "FEHLER" in (ohne.stdout + ohne.stderr)

    mit = subprocess.run(
        [
            PY,
            str(SKILL / "to_spawn.py"),
            "takt",
            "293",
            "--pruefe-meldung",
            f"Beleg #1479 · 03:08 · blockiert · „{BLOCKIERT_WORTLAUT}“",
        ],
        cwd=str(SKILL),
        env=welt["env"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert mit.returncode == 0, mit.stdout + mit.stderr


def test_prompts_nennen_beleg_pflicht_aus_einer_quelle() -> None:
    """Beide Aufseher-Prompts (Dauer-Aufseher und Takt) tragen die Beleg-Regel aus einer Quelle."""
    sys.path.insert(0, str(SKILL / "skripte"))
    import wache

    hinweis = aufseher_stand.beleg_hinweis("python {SKILL}/to_spawn.py")
    assert hinweis in wache.PROMPT
    assert hinweis in waechter_takt.PROMPT
    # Formatierung der Prompts bleibt gültig.
    wache.PROMPT.format(S=900, REPO="o/r", DATUM="2026-11-23", TAKT=1800, SKILL="/skill")
    waechter_takt.PROMPT.format(S=900, REPO="o/r", SKILL="/skill", LISTE="- x", NOTIZ="(leer)")
