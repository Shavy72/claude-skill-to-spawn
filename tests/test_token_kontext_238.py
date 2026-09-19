"""#238: Token-Anzeige zeigt den Spitzen-Kontext statt der Summe des Cache-Lesens.

Weg-Test mit dem echten Transkript-Format: ``hilfen/transkript_204/`` ist das
aufgezeichnete Transkript der #204-Bau-Session (Bau-Server, 18.09.2026) — nur die
Zahlenfelder (``message.usage``, Kennungen, Zeitstempel), Inhalte entfernt.
Hook-Befehle, Bau-Log, Umrechnung, ``sessions_stand`` und Lernstoff laufen echt.

Nachgerechnet im Original: 61 API-Aufrufe der Hauptsession, Spitzen-Kontext
180 199, Summe ``cache_read`` 7,96 Mio. (``sessions`` zeigte „6735,1k“). Größter
Subagent (``agent-a78c5760522d15cbe``): 50 Aufrufe, Spitzen-Kontext 169 188.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"
AUFZEICHNUNG = Path(__file__).resolve().parent / "hilfen" / "transkript_204"

sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKRIPTE))

from to_spawn import bau_log  # noqa: E402

TICKET = "204"
SESSION = "97a30ed9-6786-4987-b0ba-3197343cfec6"
SUBAGENT = "a78c5760522d15cbe"
HAUPT_TRANSKRIPT = AUFZEICHNUNG / f"{SESSION}.jsonl"
SUB_TRANSKRIPT = AUFZEICHNUNG / SESSION / "subagents" / f"agent-{SUBAGENT}.jsonl"

#: Alte Zeile aus ``wt-204/.to-spawn/bau_log/204.jsonl`` (vor #238, ohne ``kontext``).
ALTE_ZEILE = {
    "ticket": TICKET,
    "typ": "session_ende",
    "session_id": SESSION,
    "tokens": {
        "input": 106,
        "cache_read": 6556891,
        "cache_creation": 138256,
        "output": 39878,
        "gesamt": 6735131,
    },
    "dauer_s": 2887,
}


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    ordner = tmp_path / f"wt-{TICKET}"
    ordner.mkdir()
    return ordner


def _cli(args: list[str], worktree: Path, tmp_path: Path, eingabe: str = "") -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "TO_SPAWN_LOG_REPO": str(worktree),
        "TO_SPAWN_TICKET": TICKET,
    }
    env.pop("TO_SPAWN_START", None)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        input=eingabe,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(worktree),
        timeout=60,
        check=False,
    )


def _stop(worktree: Path, tmp_path: Path) -> None:
    eingabe = json.dumps(
        {
            "session_id": SESSION,
            "transcript_path": str(HAUPT_TRANSKRIPT),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
    )
    ergebnis = _cli(["hook-stop"], worktree, tmp_path, eingabe)
    assert ergebnis.returncode == 0, ergebnis.stderr


def _subagent_stop(worktree: Path, tmp_path: Path) -> None:
    eingabe = json.dumps(
        {
            "session_id": SESSION,
            "transcript_path": str(HAUPT_TRANSKRIPT),
            "agent_id": SUBAGENT,
            "agent_type": "general-purpose",
            "agent_transcript_path": str(SUB_TRANSKRIPT),
            "hook_event_name": "SubagentStop",
        }
    )
    ergebnis = _cli(["hook-subagent-stop"], worktree, tmp_path, eingabe)
    assert ergebnis.returncode == 0, ergebnis.stderr


def _alte_zeile(worktree: Path, **extra: object) -> None:
    ordner = worktree / bau_log.LAUF_ORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    with (ordner / f"{TICKET}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**ALTE_ZEILE, **extra}, ensure_ascii=False) + "\n")


def _token_text(worktree: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    import sessions_stand

    # Der echte Worktree-Pfad (/home/bau/wt/wt-204) darf nicht mitlesen.
    monkeypatch.setattr(sessions_stand.config, "worktree_pfad", lambda _t: str(worktree))
    return sessions_stand.token_text(TICKET, repo=worktree)


# --- Weg-Test: echter Stop-Hook über das aufgezeichnete Transkript -------------


def test_weg_stop_hook_ist_k_ist_spitzen_kontext(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stop(worktree, tmp_path)

    ende = [z for z in bau_log.lese(worktree, TICKET) if z["typ"] == "session_ende"][-1]
    assert ende["kontext"] == {"spitze": 180199, "aufrufe": 61}

    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] == 180.2, z
    assert z["aufrufe"] == 61
    # Die Summe bleibt sichtbar, aber ehrlich benannt und nie als Kontext.
    assert z["verbrauch_k"] > 6000
    assert z["output_k"] == round(ende["tokens"]["output"] / 1000, 1)

    assert _token_text(worktree, monkeypatch) == "180,2k"


def test_weg_subagent_eigene_spitze_nicht_in_ist_k(worktree: Path, tmp_path: Path) -> None:
    _stop(worktree, tmp_path)
    _subagent_stop(worktree, tmp_path)

    sub = [z for z in bau_log.lese(worktree, TICKET) if z["typ"] == "subagent_ende"][-1]
    assert sub["kontext"] == {"spitze": 169188, "aufrufe": 50}

    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] == 180.2  # Subagenten nie dazuaddiert
    assert z["sub_spitze_k"] == 169.2
    assert z["subagenten"] == 1


def test_stop_hook_mehrere_runden_spitze_bleibt(worktree: Path, tmp_path: Path) -> None:
    """Stop feuert je Runde — die jüngste Zeile je Session zählt, keine Summe."""
    _stop(worktree, tmp_path)
    _stop(worktree, tmp_path)
    assert bau_log.zusammenfassung(worktree, TICKET)["ist_k"] == 180.2


# --- Alte Bau-Logs: beim Lesen ehrlich, per Umrechnung korrekt -----------------


def test_alte_zeile_ohne_kontext_zeigt_keinen_falschen_wert(worktree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _alte_zeile(worktree)
    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] is None, "Summe des Cache-Lesens ist keine Kontextgröße"
    assert z["verbrauch_k"] == 6735.1
    assert _token_text(worktree, monkeypatch) == "—"
    zeile = bau_log.tabelle(worktree, [TICKET]).splitlines()[2].split()
    assert zeile[2] == "—", zeile  # Spalte „Ist k“
    assert zeile[-1] == "6735.1", zeile  # nur als „Verbrauch k (Cache inkl.)“


def test_weg_umrechnen_alter_log_aus_transkript(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _alte_zeile(worktree)
    _alte_zeile(
        worktree,
        typ="subagent_ende",
        session_id=SUBAGENT,
        eltern_session=SESSION,
        tokens={"gesamt": 11_400_000},
    )

    ergebnis = _cli(
        ["umrechnen", "--ticket", TICKET, "--repo", str(worktree), "--transkripte", str(AUFZEICHNUNG)],
        worktree,
        tmp_path,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "2 Zeile(n) umgerechnet" in ergebnis.stdout, ergebnis.stdout

    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] == 180.2
    assert z["sub_spitze_k"] == 169.2
    assert _token_text(worktree, monkeypatch) == "180,2k"

    # Zweiter Lauf ändert nichts mehr.
    nochmal = _cli(
        ["umrechnen", "--ticket", TICKET, "--repo", str(worktree), "--transkripte", str(AUFZEICHNUNG)],
        worktree,
        tmp_path,
    )
    assert "0 Zeile(n) umgerechnet" in nochmal.stdout, nochmal.stdout


def test_umrechnen_ohne_transkript_meldet_luecke(worktree: Path, tmp_path: Path) -> None:
    _alte_zeile(worktree, session_id="gibt-es-nicht")
    ergebnis = _cli(
        ["umrechnen", "--ticket", TICKET, "--repo", str(worktree), "--transkripte", str(AUFZEICHNUNG)],
        worktree,
        tmp_path,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "0 Zeile(n) umgerechnet" in ergebnis.stdout
    assert "1 ohne Transkript" in ergebnis.stdout
    assert bau_log.zusammenfassung(worktree, TICKET)["ist_k"] is None


# --- Lernschleife nutzt nur den Spitzen-Kontext --------------------------------


def test_lernstoff_und_tabelle_nutzen_spitzen_kontext(worktree: Path, tmp_path: Path) -> None:
    bau_log.schreibe(worktree, TICKET, "auftrag", schaetzung_k=60, umfang="Bau-Log Token-Anzeige")
    _stop(worktree, tmp_path)

    text = bau_log.lernstoff(worktree, grenze_k=200)
    assert "60k geschätzt → 180.2k ist (Faktor 3,0)" in text, text
    assert "6735" not in text and "7961" not in text

    tabelle = bau_log.tabelle(worktree, [TICKET])
    assert "180.2" in tabelle, tabelle


# --- Fixrunde Prüfpanel #238 --------------------------------------------------


def test_umrechnen_haengt_nur_an_alte_zeilen_bleiben(worktree: Path, tmp_path: Path) -> None:
    """Kein Umschreiben: ein gleichzeitig anhängender Hook verliert keine Zeile."""
    _alte_zeile(worktree)
    datei = worktree / bau_log.LAUF_ORDNER / f"{TICKET}.jsonl"
    vorher = datei.read_text(encoding="utf-8")
    ergebnis = _cli(
        ["umrechnen", "--ticket", TICKET, "--repo", str(worktree), "--transkripte", str(AUFZEICHNUNG)],
        worktree,
        tmp_path,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    nachher = datei.read_text(encoding="utf-8")
    assert nachher.startswith(vorher), "alte Zeilen müssen unverändert bleiben"
    neu = [json.loads(z) for z in nachher[len(vorher) :].splitlines() if z.strip()]
    assert len(neu) == 1 and neu[0]["kontext"] == {"spitze": 180199, "aufrufe": 61}
    assert neu[0]["tokens"] == ALTE_ZEILE["tokens"] and neu[0]["dauer_s"] == 2887
    assert not list(datei.parent.glob("*.tmp"))


def test_gemischtes_log_ohne_teilwert(worktree: Path, tmp_path: Path) -> None:
    """Session 1 alt ohne Transkript, Session 2 neu → kein zu kleines ist_k."""
    _alte_zeile(worktree, session_id="alte-session-ohne-transkript")
    _stop(worktree, tmp_path)
    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] is None
    assert z["spitze_k"] is None


def test_sessions_spalte_zeigt_einzelspitze_bei_staffel(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for kennung in ("staffel-1", "staffel-2"):
        bau_log.schreibe(
            worktree,
            TICKET,
            "session_ende",
            session_id=kennung,
            tokens={"gesamt": 9_000_000},
            kontext={"spitze": 150_000, "aufrufe": 40},
        )
    z = bau_log.zusammenfassung(worktree, TICKET)
    assert z["ist_k"] == 300.0  # Bedarf gegen die Schätzung (Lernschleife)
    assert z["spitze_k"] == 150.0  # Smart-Zone-Zahl
    assert _token_text(worktree, monkeypatch) == "150,0k"


def test_stop_hook_leeres_transkript_schreibt_keine_null(worktree: Path, tmp_path: Path) -> None:
    leer = tmp_path / "leer.jsonl"
    leer.write_text("", encoding="utf-8")
    eingabe = json.dumps({"session_id": "leer", "transcript_path": str(leer), "hook_event_name": "Stop"})
    assert _cli(["hook-stop"], worktree, tmp_path, eingabe).returncode == 0
    ende = [z for z in bau_log.lese(worktree, TICKET) if z["typ"] == "session_ende"][-1]
    assert "kontext" not in ende
    assert bau_log.zusammenfassung(worktree, TICKET)["ist_k"] is None


def test_umrechnen_subagent_kennung_aus_dateiname(worktree: Path, tmp_path: Path) -> None:
    _alte_zeile(worktree, typ="subagent_ende", session_id=f"agent-{SUBAGENT}", eltern_session=SESSION)
    ergebnis = _cli(
        ["umrechnen", "--ticket", TICKET, "--repo", str(worktree), "--transkripte", str(AUFZEICHNUNG)],
        worktree,
        tmp_path,
    )
    assert "1 Zeile(n) umgerechnet" in ergebnis.stdout, ergebnis.stdout
    assert bau_log.zusammenfassung(worktree, TICKET)["sub_spitze_k"] == 169.2


def test_tabelle_weist_aufrufe_verbrauch_und_subagent_getrennt_aus(
    worktree: Path, tmp_path: Path
) -> None:
    _stop(worktree, tmp_path)
    _subagent_stop(worktree, tmp_path)
    kopf, _strich, zeile = bau_log.tabelle(worktree, [TICKET]).splitlines()[:3]
    assert "Verbrauch k (Cache inkl.)" in kopf and "Aufrufe" in kopf
    werte = zeile.split()
    assert werte[2] == "180.2"  # Ist k = Spitzen-Kontext
    assert "169.2" in werte and "61" in werte
