"""#783: Warte-Ende (fertig / fehlgeschlagen / blockiert) weckt Bau-Session aktiv.

Linux/tmux: ``to_spawn/briefkasten_wecker.py`` beobachtet ``.to-spawn/warten/<N>[-<name>].json``
(im Ticket-Worktree und im Hauptbaum) und weckt ein stilles ``bau <N>``-Fenster einmal je
Zustandsübergang (``laeuft`` → ``fertig`` / ``fehlgeschlagen`` / ``blockiert``).
Kern/allgemein: ``repo-scripts/_default.json`` gibt für ``to_spawn.py warten <N> --pruefen``
ein kurzes ``ScheduleWakeup``-Intervall (180–300 s) vor.

Gestellt ist nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``); Warte-Läufer,
Zustandsdateien, Wecker und Vorlage sind echt.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import (
    ARBEITET,
    PANE,
    RUECKFRAGE,
    SPEC,
    STILL,
    TICKET,
    _bild,
    _getippt,
    _pane,
)

from to_spawn import briefkasten_wecker, starter, warten

SKILL = Path(__file__).resolve().parents[1]
welt = basis.welt
BAU = f"=spec-{SPEC}:@2"
ZWEI = TICKET + 1
BAU_ZWEI = f"=spec-{SPEC}:@5"


def _schreibe_stand(
    repo: Path,
    ticket: int,
    status: str,
    *,
    name: str = "",
    ergebnis_exit: int | None = None,
    ergebnis_text: str = "",
    gestartet_am: str = "2026-11-25T20:00:00+00:00",
) -> Path:
    pfad = warten.zustand_pfad(repo, ticket, name)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    daten = {
        "ticket": str(ticket),
        "name": name,
        "befehl": ["python", "ticket_status.py"],
        "arbeitsordner": str(repo),
        "log_repo": str(repo),
        "status": status,
        "modus": "hintergrund",
        "versuch": 1,
        "max_versuche": 3,
        "gestartet_am": gestartet_am,
        "aktualisiert_am": "2026-11-25T20:01:00+00:00",
        "ergebnis_exit": ergebnis_exit,
        "ergebnis_text": ergebnis_text,
    }
    pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    return pfad


def _wecker(welt: dict[str, Any], *, gh_repo: str = "") -> briefkasten_wecker.Wecker:
    return briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE, gh_repo=gh_repo)


@pytest.mark.parametrize(
    ("ziel_status", "exit_code", "ausgabe"),
    [
        ("fehlgeschlagen", 1, "ticket-rot: Testlauf fehlgeschlagen"),
        ("fertig", 0, "ticket-gruen: auf main"),
        ("blockiert", -9, "Warte-Befehl nach 3 Abbrüchen blockiert"),
    ],
)
def test_uebergang_laeuft_zu_endzustand_weckt_bau_einmal(
    welt: dict[str, Any],
    ziel_status: str,
    exit_code: int,
    ausgabe: str,
) -> None:
    """``laeuft`` → ``fertig`` / ``fehlgeschlagen`` / ``blockiert`` weckt ``bau <N>`` genau einmal."""
    _schreibe_stand(welt["haupt"], TICKET, "laeuft")
    w = _wecker(welt)
    w.einmal()
    assert _getippt(welt) == [], "solange status == laeuft darf kein Weckruf erfolgen"

    _schreibe_stand(
        welt["haupt"],
        TICKET,
        ziel_status,
        ergebnis_exit=exit_code,
        ergebnis_text=ausgabe,
    )
    w.einmal()
    w.einmal()  # zweiter Durchgang darf denselben Übergang nicht nochmal wecken

    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    text = getippt[0]["text"]
    assert f"#{TICKET}" in text
    assert ziel_status in text
    assert f"warten {TICKET} --pruefen" in text
    assert "\n" not in text


def test_zweiter_durchlauf_nach_neustart_weckt_erneut(welt: dict[str, Any]) -> None:
    """Nach ``laeuft`` → ``fehlgeschlagen`` → erneut ``laeuft`` → ``fertig`` weckt der neue Übergang wieder."""
    _schreibe_stand(welt["haupt"], TICKET, "laeuft", gestartet_am="2026-11-25T20:00:00+00:00")
    w = _wecker(welt)
    w.einmal()
    _schreibe_stand(
        welt["haupt"],
        TICKET,
        "fehlgeschlagen",
        ergebnis_exit=1,
        ergebnis_text="ticket-rot",
        gestartet_am="2026-11-25T20:00:00+00:00",
    )
    w.einmal()
    assert len(_getippt(welt)) == 1

    # Bau-Session behebt Fehler und startet neuen Warte-Schritt
    _schreibe_stand(welt["haupt"], TICKET, "laeuft", gestartet_am="2026-11-25T20:10:00+00:00")
    w.einmal()
    assert len(_getippt(welt)) == 1

    _schreibe_stand(
        welt["haupt"],
        TICKET,
        "fertig",
        ergebnis_exit=0,
        ergebnis_text="ticket-gruen",
        gestartet_am="2026-11-25T20:10:00+00:00",
    )
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU, BAU], getippt
    assert "fehlgeschlagen" in getippt[0]["text"]
    assert "fertig" in getippt[1]["text"]


def test_bestand_beim_start_weckt_nicht(welt: dict[str, Any]) -> None:
    """Eine beim Start des Weckers bereits fertige/fehlgeschlagene Warte-Datei weckt nicht."""
    _schreibe_stand(welt["haupt"], TICKET, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="altes ticket-rot")
    w = _wecker(welt)
    w.einmal()
    w.einmal()
    assert _getippt(welt) == []


def test_echter_hintergrund_laeufer_ticket_rot_weckt_bau_session(welt: dict[str, Any]) -> None:
    """End-to-End mit echtem ``warten.starte_hintergrund``: ``ticket-rot`` weckt das stille ``bau <N>``-Fenster."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / "ticket_rot_783.py"
    skript.write_text("print('ticket-rot: Prüflauf rot')\nraise SystemExit(1)\n", encoding="utf-8")

    w = _wecker(welt)
    w.einmal()
    assert _getippt(welt) == []

    erg = warten.starte_hintergrund(
        welt["haupt"],
        TICKET,
        [sys.executable, str(skript)],
        pause_s=0.0,
    )
    assert erg.exit == warten.EXIT_LAEUFT

    pfad = warten.zustand_pfad(welt["haupt"], TICKET)
    ende = time.monotonic() + 15.0
    while time.monotonic() < ende:
        if pfad.is_file():
            stand = json.loads(pfad.read_text(encoding="utf-8"))
            if stand.get("status") == "fehlgeschlagen":
                break
        time.sleep(0.05)
    assert json.loads(pfad.read_text(encoding="utf-8"))["status"] == "fehlgeschlagen"

    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert "fehlgeschlagen" in getippt[0]["text"]
    assert "ticket-rot" in getippt[0]["text"]
    assert f"warten {TICKET} --pruefen" in getippt[0]["text"]


def test_warte_datei_im_ticket_worktree_weckt_bau_session(
    welt: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bau-Sessions laufen im Worktree (``BAU_WT_DIR/wt-<N>``); der Wecker im Hauptbaum sieht auch dort."""
    wt_basis = tmp_path / "wt-basis"
    wt = wt_basis / f"wt-{TICKET}"
    wt.mkdir(parents=True)
    monkeypatch.setenv("BAU_WT_DIR", str(wt_basis))

    _schreibe_stand(wt, TICKET, "laeuft")
    w = _wecker(welt)
    w.einmal()
    assert _getippt(welt) == []

    _schreibe_stand(wt, TICKET, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="ticket-rot im worktree")
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert "ticket-rot im worktree" in getippt[0]["text"]


def test_benannter_warte_schritt_und_keine_ticket_verwechslung(welt: dict[str, Any]) -> None:
    """``--name deploy`` weckt mit ``--name deploy --pruefen``; ``<N>0.json`` weckt ``bau <N>`` nie."""
    _schreibe_stand(welt["haupt"], TICKET, "laeuft", name="deploy")
    _schreibe_stand(welt["haupt"], TICKET * 10, "laeuft")
    w = _wecker(welt)
    w.einmal()

    _schreibe_stand(welt["haupt"], TICKET * 10, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="fremd")
    w.einmal()
    assert _getippt(welt) == [], "fremdes Ticket <N>0 darf bau <N> nicht wecken"

    _schreibe_stand(welt["haupt"], TICKET, "fertig", name="deploy", ergebnis_exit=0, ergebnis_text="deploy ok")
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [BAU], getippt
    assert f"warten {TICKET} --name deploy --pruefen" in getippt[0]["text"]


def test_unruhiges_fenster_wartet_und_weckt_dann_einmal(welt: dict[str, Any]) -> None:
    """Arbeitendes, rückfragendes oder frisch aktives Fenster wird nicht unterbrochen; still → ein Weckruf."""
    _schreibe_stand(welt["haupt"], TICKET, "laeuft")
    w = _wecker(welt)
    w.einmal()

    _schreibe_stand(welt["haupt"], TICKET, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="ticket-rot")
    for bild in (ARBEITET, RUECKFRAGE):
        _bild(welt, bild)
        w.einmal()
    _bild(welt, STILL)
    _pane(welt, aktiv=time.time())
    w.einmal()
    assert _getippt(welt) == []

    _pane(welt, aktiv=time.time() - 2 * briefkasten_wecker.RUHE_S)
    w.einmal()
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [BAU]


def test_fehlendes_bau_fenster_tippt_nicht_und_haengt_nicht(welt: dict[str, Any]) -> None:
    """Ohne Fenster ``bau <N>`` wird nicht getippt und das Ereignis nicht endlos wiederholt."""
    (welt["tmux"] / "fenster.txt").write_text(f"@1\twache {SPEC}\t0\n", encoding="utf-8")
    _schreibe_stand(welt["haupt"], TICKET, "laeuft")
    w = _wecker(welt)
    w.einmal()

    _schreibe_stand(welt["haupt"], TICKET, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="ticket-rot")
    w.einmal()
    assert _getippt(welt) == []
    # Taucht das Fenster später wieder auf, gilt der alte Übergang als erledigt.
    (welt["tmux"] / "fenster.txt").write_text(f"@1\twache {SPEC}\t0\n@2\tbau {TICKET}\t0\n", encoding="utf-8")
    w.einmal()
    assert _getippt(welt) == []


def test_kaputte_warte_datei_haelt_andere_tickets_nicht_auf(welt: dict[str, Any]) -> None:
    """Unlesbare Warte-Datei eines Tickets bremst das andere Ticket nicht aus."""
    manifest_datei = welt["haupt"] / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    manifest_datei.write_text(
        json.dumps({"spec": SPEC, "tickets": {str(TICKET): {}, str(ZWEI): {}}}),
        encoding="utf-8",
    )
    (welt["tmux"] / "fenster.txt").write_text(
        f"@1\twache {SPEC}\t0\n@2\tbau {TICKET}\t0\n@5\tbau {ZWEI}\t0\n",
        encoding="utf-8",
    )
    kaputt = warten.zustand_pfad(welt["haupt"], TICKET)
    kaputt.parent.mkdir(parents=True, exist_ok=True)
    kaputt.write_text("{kaputt", encoding="utf-8")
    _schreibe_stand(welt["haupt"], ZWEI, "laeuft")

    w = _wecker(welt)
    w.einmal()

    _schreibe_stand(welt["haupt"], ZWEI, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="ticket-rot")
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [BAU_ZWEI]


def test_langsames_github_haelt_warte_weckruf_nicht_auf(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Lokaler Warte-Weckruf läuft vor den GitHub-Blicken — hängendes ``gh`` verzögert ihn nicht."""
    freigeben = threading.Event()
    gh_gerufen = threading.Event()

    def langsames_gh(*_a: Any, **_k: Any) -> tuple[int, str]:
        gh_gerufen.set()
        freigeben.wait(timeout=5.0)
        return 1, ""

    monkeypatch.setattr(briefkasten_wecker.gh, "lauf", langsames_gh)
    _schreibe_stand(welt["haupt"], TICKET, "laeuft")
    w = _wecker(welt, gh_repo="o/r")
    _schreibe_stand(welt["haupt"], TICKET, "fehlgeschlagen", ergebnis_exit=1, ergebnis_text="ticket-rot")

    faden = threading.Thread(target=w.einmal, daemon=True)
    faden.start()
    try:
        assert gh_gerufen.wait(timeout=5.0), "gh-Blick lief nicht an"
        getippt = _getippt(welt)
        assert [g["ziel"] for g in getippt] == [BAU], getippt
        assert "fehlgeschlagen" in getippt[0]["text"]
    finally:
        freigeben.set()
        faden.join(timeout=5.0)


def test_start_vorlage_gibt_kurzes_schedule_wakeup_fuer_warten_pruefen_vor() -> None:
    """Start-Text (``repo-scripts/_default.json``) gibt für ``warten {N} --pruefen`` 180–300 s vor."""
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    prompt: str = vorlage["prompt_template"]
    treffer = re.search(
        r"warten \{N\} --hintergrund[^;]{0,200}?ScheduleWakeup\s+(\d+)\s*s[^;]{0,200}?warten \{N\} --pruefen",
        prompt,
    )
    assert treffer is not None, "Kurzes ScheduleWakeup-Intervall im Warte-Absatz von _default.json fehlt"
    sekunden = int(treffer.group(1))
    assert 180 <= sekunden <= 300, f"ScheduleWakeup-Intervall für warten --pruefen außerhalb 180–300 s: {sekunden}"
