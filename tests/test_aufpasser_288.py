"""Tests für #288: Aufpasser-Startfehler lesbar im Log, kein Zweitstart bei fremdem Rechner,
Bau-Session mit „nichts zu tun“ beendet sich samt Fenster ohne Neustart und ohne Fehlalarm.

Abdeckung der Abnahme-Punkte von #288:
1. Ursache von „Fenster gleich wieder weg“ steht lesbar im Aufpasser-Log (und Spec-Kommentar),
   aus ``wache_log`` bzw. ``capo.fenster_log`` (nur neue Zeilen ab Start) oder als tmux-Befund.
2. Läuft der Aufseher einer Spec laut Auswahl auf einem anderen Rechner (fremder Rechner oder
   ``--ohne-wache``), versucht der Aufpasser keinen zweiten Start und meldet das in einer Zeile.
3. Bau-Session mit „nichts zu tun“: ``resume_prompt`` weist nicht mehr zu ``ScheduleWakeup`` an,
   sondern zum Beenden von Loop und Session; ``bau.py`` beendet sein tmux-Fenster bei fertigem
   oder geschlossenem Ticket sofort ohne Claude-Start; zwei Aufpasser-Takte hintereinander
   erzeugen 0 neue Fenster und hinterlassen keine Fenster/Prozesse ohne Arbeit.
4. Direkter Aufpasser-Test: kein Fehlalarm „startet nicht (Fenster verschwindet wieder)“ für
   fertig gebaute oder geschlossene Tickets, auch bei gefülltem Start-Gedächtnis.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SITZUNG,
    SPEC,
    Welt,
    fenster_namen,
    tmux,
    welt,
)

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import (  # noqa: E402
    aufpasser,
    auswahl,
    briefkasten,
    capo,
    hooks,
    prozessbaum,
    startstelle,
    ticket_zustand,
    umzug,
    warten,
)


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path, name: str) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(name, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


def _gh_labels(w: Welt, monkeypatch: pytest.MonkeyPatch, daten: dict[str, list[str]]) -> None:
    datei = w.tmp / "gh_daten_288.json"
    datei.write_text(
        json.dumps({n: {"labels": namen} for n, namen in daten.items()}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_STUB_DATEN", str(datei))


# --- 1. Ursache von „Fenster gleich wieder weg“ lesbar im Log -----------------


def test_288_aufseher_start_fehlgeschlagen_grund_lesbar_im_log(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme 1 (Aufseher-Fenster): scheitert der Start, stehen Fehlertext, Exit-Code
    und Log-Pfad aus ``wache_log`` lesbar im Aufpasser-Log und im Spec-Kommentar."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    (welt.repo / ".to-spawn" / "config.json").write_text('{"speicher": {"staffel_s": 0}}', encoding="utf-8")
    # Platzhalter hält die tmux-Sitzung, während das Aufseher-Fenster fehlt.
    tmux("new-window", "-d", "-t", f"={SITZUNG}", "-n", "platzhalter", "-c", str(welt.repo), "sleep 3600")
    tmux("kill-window", "-t", f"={SITZUNG}:wache {SPEC}")

    vorlage = "sh -c 'echo Startklar: Manifest spec-9001.json fehlt >&2; exit 2'"
    assert welt.lauf("--wache-vorlage", vorlage) == 0

    log_datei = aufpasser.wache_log(welt.repo, SPEC)
    assert log_datei.is_file()
    fehler_zeilen = [
        z for z in welt.log().splitlines() if "Aufseher-Fenster: Start fehlgeschlagen (Fenster gleich wieder weg)" in z
    ]
    assert len(fehler_zeilen) == 1, welt.log()
    assert "Manifest spec-9001.json fehlt" in fehler_zeilen[0]
    assert "Exit 2" in fehler_zeilen[0]
    assert log_datei.name in fehler_zeilen[0]

    kommentare = welt.kommentare()
    treffer = [k for k in kommentare if "Aufseher-Fenster: Start fehlgeschlagen" in k]
    assert len(treffer) == 1, kommentare
    assert "Manifest spec-9001.json fehlt" in treffer[0]
    assert "Exit 2" in treffer[0]


def test_288_bau_start_fehlgeschlagen_liest_nur_neue_log_zeilen(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme 1 (Bau-Fenster): alte Zeilen im Fenster-Log werden übersprungen; nur
    der Grund des aktuellen Fehlschlags landet im Aufpasser-Log und Kommentar."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    log_datei = capo.fenster_log(welt.repo, 9002)
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    log_datei.write_text("ALTER_FEHLER_VON_GESTERN\n", encoding="utf-8")

    vorlage = (
        f"sh -c 'echo NEUER_FEHLER_9002 >> {log_datei}; "
        f'echo "2026-11-25T12:00:00 bau.py #9002 Exit 2" >> {log_datei}; exit 2\''
    )
    assert welt.lauf("--bau-vorlage", vorlage) == 0

    fehler_zeilen = [
        z for z in welt.log().splitlines() if "#9002: Start fehlgeschlagen (Fenster gleich wieder weg)" in z
    ]
    assert len(fehler_zeilen) == 1, welt.log()
    assert "NEUER_FEHLER_9002" in fehler_zeilen[0]
    assert "Exit 2" in fehler_zeilen[0]
    assert "ALTER_FEHLER_VON_GESTERN" not in fehler_zeilen[0]

    kommentare = welt.kommentare()
    treffer = [k for k in kommentare if "#9002: Start fehlgeschlagen" in k]
    assert len(treffer) == 1, kommentare
    assert "NEUER_FEHLER_9002" in treffer[0]
    assert "ALTER_FEHLER_VON_GESTERN" not in treffer[0]


def test_288_start_log_auszug_offset_kuerzung_und_fehlend(tmp_path: Path) -> None:
    """Einheiten-Test (Linux + Windows): ``_start_log_auszug`` liest ab Offset, fällt bei
    gekürzter Datei auf 0 zurück, begrenzt Zeilen/Länge und liefert leer bei fehlender Datei."""
    fehlt = tmp_path / "fehlt.log"
    assert aufpasser._start_log_auszug(fehlt) == ""

    datei = tmp_path / "fenster.log"
    datei.write_text("alt1\nalt2\n", encoding="utf-8")
    offset = datei.stat().st_size
    assert aufpasser._start_log_auszug(datei, offset) == ""

    with datei.open("a", encoding="utf-8") as f:
        f.write("z1\nz2\nz3\nz4\n")
    assert aufpasser._start_log_auszug(datei, offset) == "z2 | z3 | z4"

    # Datei gekürzt (kleiner als Offset) → liest von vorn.
    datei.write_text("frisch\n", encoding="utf-8")
    assert aufpasser._start_log_auszug(datei, offset) == "frisch"

    # Kürzung langer Zeilen mit Auslassungspunkten.
    datei.write_text("x" * 300 + "\n", encoding="utf-8")
    kurz = aufpasser._start_log_auszug(datei, 0, max_zeichen=40)
    assert len(kurz) == 40 and kurz.endswith("…")


def test_288_start_fehlgeschlagen_ohne_fenster_log_nennt_tmux_befund(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Fenster-Log (eigene Vorlage) nennt das Log trotzdem den konkreten tmux-Befund."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    assert welt.lauf("--bau-vorlage", "sh -c 'exit 1'") == 0

    log_text = welt.log()
    assert "#9002: Start fehlgeschlagen (Fenster gleich wieder weg) — Grund:" in log_text


# --- 2. Aufseher auf anderem Rechner: kein Zweitstart, Meldung in einer Zeile ---


@pytest.mark.parametrize("modus", ["fremder_rechner", "ohne_wache"])
def test_288_aufseher_anderer_rechner_kein_zweitstart_und_eine_zeile(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, modus: str
) -> None:
    """Abnahme 2: gehört die Spec einem anderen Rechner (oder gilt ``--ohne-wache``),
    versucht der Aufpasser keinen zweiten Aufseher-Start und meldet das in einer Zeile."""
    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-server-test")
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    tmux("new-window", "-d", "-t", f"={SITZUNG}", "-n", "platzhalter", "-c", str(welt.repo), "sleep 3600")
    tmux("kill-window", "-t", f"={SITZUNG}:wache {SPEC}")

    if modus == "fremder_rechner":
        auswahl.speichern(welt.repo, SPEC, [9002], ohne_wache=False, rechner="pc-anderer-rechner")
        erwartet_teil = "pc-anderer-rechner"
    else:
        auswahl.speichern(welt.repo, SPEC, [9002], ohne_wache=True, rechner="bau-server-test")
        erwartet_teil = "ohne Aufseher"

    wache_protokoll = welt.tmp / f"wache_start_{modus}.txt"
    vorlage = f"sh -c 'echo gestartet >> {wache_protokoll}; exec sleep 3600'"
    assert welt.lauf("--wache-vorlage", vorlage) == 0

    assert f"wache {SPEC}" not in fenster_namen()
    assert not wache_protokoll.exists(), "Aufseher darf nicht gestartet worden sein"
    assert f"wache-{SPEC}" not in (welt.stand().get("starts") or {})

    zeilen = [
        z for z in welt.log().splitlines() if f"spec {SPEC}:" in z and "Aufseher" in z and "kein zweiter Start" in z
    ]
    assert len(zeilen) == 1, welt.log()
    assert erwartet_teil in zeilen[0], zeilen[0]


# --- 3. Bau-Session mit „nichts zu tun“ beendet sich samt Fenster, 0 Neustarts ---


def test_288_resume_prompt_kein_schedule_wakeup_bei_nichts_zu_tun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offen (c): ``bau.py::resume_prompt`` weist bei „nichts zu tun“ (fertig gebaut /
    geschlossen) nicht mehr zu ``ScheduleWakeup`` an, sondern zum Beenden von Loop und Session."""
    bau = _lade_bau(monkeypatch, tmp_path, "bau_probe_288")
    text = bau.resume_prompt("9002")
    assert "nichts zu tun? ScheduleWakeup" not in text
    assert "stop: true" in text
    assert "/exit" in text


def test_288_fertige_bau_session_beendet_fenster_zwei_takte_null_neue_fenster(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme 3 & 4:
    - Eine Bau-Session (``skripte/bau.py``) im tmux-Fenster für ein geschlossenes bzw. fertig
      gebautes Ticket stellt „nichts zu tun“ fest und beendet sich samt Fenster ohne Claude-Start.
    - Ein noch stehendes stilles Fenster eines fertig gebauten Tickets wird geschlossen.
    - Zwei Aufpasser-Takte hintereinander erzeugen 0 neue Fenster und hinterlassen weder
      Bau-Fenster noch laufende Bau-Prozesse."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    _gh_labels(welt, monkeypatch, {"9002": [ticket_zustand.STAGING_FERTIG_LABEL]})
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    sperren = welt.tmp / "sperren_288"

    # (a) Echtes bau.py im tmux-Fenster für geschlossenes Ticket #9003:
    #     muss sofort „nichts zu tun“ melden, Exit 0 liefern und das tmux-Fenster schließen.
    manifest_dir = welt.repo / "docs" / "agents" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {
                "spec": int(SPEC),
                "feature": "probe-288",
                "tickets": {
                    "9002": {"title": "Ticket 9002"},
                    "9003": {"title": "Ticket 9003"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx_plugin = welt.heim / ".claude" / "plugins" / "cache" / "context-mode" / "context-mode" / "1.0.0"
    ctx_plugin.mkdir(parents=True, exist_ok=True)
    (ctx_plugin / "start.mjs").write_text("// stub\n", encoding="utf-8")

    bau_ausgabe = welt.tmp / "bau_9003.out"
    gh_stub = SKILL / "tests" / "hilfen" / "gh_stub.py"
    bau_cmd = (
        f"unset {startstelle.TEST_OFFEN_ENV}; "
        f"export HOME={welt.heim} "
        f"PATH={welt.bin}:$PATH "
        f"TO_SPAWN_TEST_BIN={welt.bin} "
        f"TO_SPAWN_GH_STUB={gh_stub} "
        f"GH_STUB_ZU=9003 "
        f"TO_SPAWN_REPO={welt.repo} "
        f"TO_SPAWN_GH_REPO=probe/repo "
        f"TO_SPAWN_START_SPERREN={sperren} "
        f"TMPDIR={welt.tmp}; "
        f"{sys.executable} {SKRIPTE / 'bau.py'} 9003 --sofort > {bau_ausgabe} 2>&1"
    )
    tmux("new-window", "-d", "-t", f"={SITZUNG}", "-n", "bau 9003", "-c", str(welt.repo), f"bash -c {bau_cmd!r}")
    frist = time.monotonic() + 15.0
    while "bau 9003" in fenster_namen() and time.monotonic() < frist:
        time.sleep(0.2)
    assert "bau 9003" not in fenster_namen(), (
        f"Fenster bau 9003 blieb stehen: "
        f"{bau_ausgabe.read_text(encoding='utf-8', errors='replace') if bau_ausgabe.is_file() else ''}"
    )
    assert "nichts zu tun" in bau_ausgabe.read_text(encoding="utf-8", errors="replace")

    # (b) Bestehendes stilles Fenster für fertig gebautes Ticket #9002 (staging:fertig):
    #     Takt 1 schließt es sauber.
    welt.fenster_still(9002, "sleep 3600")
    welt.stand_setzen("bau 9002", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9002"))
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert fenster_namen() == [f"wache {SPEC}"]

    # (c) Zwei Aufpasser-Takte hintereinander: 0 neue Fenster, keine Bau-Prozesse.
    fenster_vorher = list(fenster_namen())
    assert welt.lauf(hang_min=90) == 0
    assert fenster_namen() == fenster_vorher
    assert welt.lauf(hang_min=90) == 0
    assert fenster_namen() == fenster_vorher
    assert len(fenster_namen()) == 1
    assert not welt.protokoll_bau.exists() or welt.protokoll_bau.read_text(encoding="utf-8").strip() == ""
    kommentare = welt.kommentare()
    assert not any("startet nicht" in k for k in kommentare), kommentare
    assert not any("braucht den Menschen" in k for k in kommentare), kommentare


# --- 4. Direkter Aufpasser-Test: kein Fehlalarm bei fertigem Ticket -----------


@pytest.mark.parametrize(
    "ticket_daten",
    [
        {"state": "OPEN", "labels": {ticket_zustand.STAGING_FERTIG_LABEL}},
        {"state": "CLOSED", "labels": set()},
    ],
    ids=["staging_fertig", "geschlossen"],
)
def test_288_start_erlaubt_kein_fehlalarm_bei_fertigem_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ticket_daten: dict
) -> None:
    """Offen (d): ``start_erlaubt`` prüft ``fertig_gebaut`` vor der ``START_MAX``-Sperre —
    ein fertiges Ticket löst nie den Fehlalarm „startet nicht (Fenster verschwindet wieder)“ aus."""
    repo = tmp_path / "repo"
    repo.mkdir()
    e = aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo)
    a = aufpasser.Aufpasser(e)
    # Start-Gedächtnis ist bereits voll (START_MAX frühere Starts im 6-h-Fenster).
    a.stand.starts["1473"] = [a.jetzt - 120.0, a.jetzt - 60.0]

    meldungen: list[tuple[str, str]] = []
    monkeypatch.setattr(a, "ticket_lesen", lambda _n, _r: ticket_daten)
    monkeypatch.setattr(
        a,
        "melden",
        lambda _s, _r, _f, ereignis, text: meldungen.append((ereignis, text)),
    )

    assert a.start_erlaubt("1330", repo, 1473, sitzung="spec-1330") is False
    assert meldungen == [], f"Fehlalarm für fertiges Ticket: {meldungen}"


def test_288_aufpasser_zwei_takte_mit_vollem_start_gedaechtnis_kein_alarm(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test in echtem tmux: #9002 hat ``staging:fertig`` und 2 alte Einträge in
    ``stand.starts`` — zwei Takte hintereinander starten 0 Fenster und melden 0 Alarme."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    _gh_labels(welt, monkeypatch, {"9002": [ticket_zustand.STAGING_FERTIG_LABEL]})
    welt.zustand.mkdir(parents=True, exist_ok=True)
    jetzt = time.time()
    (welt.zustand / "stand.json").write_text(
        json.dumps({"fenster": {}, "meldungen": {}, "starts": {"9002": [jetzt - 120, jetzt - 60]}}),
        encoding="utf-8",
    )

    assert welt.lauf(hang_min=90) == 0
    assert welt.lauf(hang_min=90) == 0

    assert fenster_namen() == [f"wache {SPEC}"]
    kommentare = welt.kommentare()
    assert not any("startet nicht" in k for k in kommentare), kommentare
    assert not any("braucht den Menschen" in k for k in kommentare), kommentare


# --- 5. Probe 5 (#288): laufende Bau-Session schließt sich nach Ticket-Schluss ---


@pytest.mark.skipif(sys.platform == "win32", reason="Nur Linux-Teil (Bau-Server, #288)")
def test_288_hook_stop_meldet_fertig_und_respektiert_schutzregeln(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop-Hook meldet fertige Tickets über ``BAU_FERTIG_DATEI`` — nur wenn wirklich
    nichts mehr zu tun ist (fail-closed bei offenem Ticket, Briefkasten, Checkpoint,
    offenem Abschluss, laufendem ``warten``-Job oder frischem Handoff)."""
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    wt = tmp_path / "wt-9004"
    wt.mkdir()
    fertig = tmp_path / "fertig.json"
    transkript = tmp_path / "session.jsonl"
    transkript.write_text('{"type": "assistant", "message": {"content": []}}\n', encoding="utf-8")

    monkeypatch.setenv("BAU_TICKET", "9004")
    monkeypatch.setenv("BAU_FERTIG_DATEI", str(fertig))
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(wt))
    monkeypatch.setenv("TO_SPAWN_LOG_RUECKFALL", str(repo))
    monkeypatch.setenv("TO_SPAWN_START", str(time.time() - 10))
    monkeypatch.delenv("BAU_STAFFEL_DATEI", raising=False)

    eingabe = json.dumps({"session_id": "sid-288", "transcript_path": str(transkript), "stop_hook_active": False})

    # (a) Offenes Ticket → keine Fertig-Datei.
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: {"state": "OPEN", "labels": []})
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()

    # (b) Stand unbekannt (gh-Fehler) → fail-closed, keine Fertig-Datei.
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: None)
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()

    # (c) Geschlossenes Ticket → schreibt Fertig-Datei (auch nach review-hook mit stop_hook_active=True).
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: {"state": "CLOSED", "labels": []})
    eingabe_aktiv = json.dumps({"session_id": "sid-288", "transcript_path": str(transkript), "stop_hook_active": True})
    assert hooks.fertig_pruefen_und_melden(eingabe_aktiv) is True
    daten = json.loads(fertig.read_text(encoding="utf-8"))
    assert daten["ticket"] == "9004"
    assert daten["grund"] == "Ticket zu"
    assert daten["transcript_path"] == str(transkript)
    assert daten["transcript_size"] == transkript.stat().st_size
    fertig.unlink()

    # (d) Geschlossenes Ticket, aber laufender warten-Job → bleibt noch offen.
    monkeypatch.setattr(
        warten,
        "zustaende_fuer_ticket",
        lambda _r, _t: [
            (repo / ".to-spawn" / "warten" / "9004.json", {"status": "laeuft", "laeufer_pid": os.getpid()})
        ],
    )
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()
    monkeypatch.setattr(warten, "zustaende_fuer_ticket", lambda _r, _t: [])

    # (e) staging:fertig mit offener Briefkasten-Frage (#416) → bleibt offen.
    staging = {"state": "OPEN", "labels": [ticket_zustand.STAGING_FERTIG_LABEL]}
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: staging)
    monkeypatch.setattr(briefkasten, "offene", lambda _ts, hauptbaum=None: [{"id": "q1", "frage": "Live?"}])
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()
    monkeypatch.setattr(briefkasten, "offene", lambda _ts, hauptbaum=None: [])

    # (f) staging:fertig mit checkpoint:human-Label → bleibt offen.
    staging_cp = {"state": "OPEN", "labels": [ticket_zustand.STAGING_FERTIG_LABEL, "checkpoint:human"]}
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: staging_cp)
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()

    # (g) staging:fertig mit offenem Abschluss (wiederanlauf = abschluss) → bleibt offen.
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: staging)
    from to_spawn import wiederanlauf

    monkeypatch.setattr(
        wiederanlauf,
        "feststellen",
        lambda _t, _r, daten=None: wiederanlauf.Wiederanlauf("abschluss", ("Klickweg-Beleg",), "fehlt"),
    )
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()

    # (h) staging:fertig ohne offene Fragen/Checkpoint/warten und wiederanlauf = nichts → schreibt Fertig-Datei.
    monkeypatch.setattr(
        wiederanlauf,
        "feststellen",
        lambda _t, _r, daten=None: wiederanlauf.Wiederanlauf("nichts", (), "fertig"),
    )
    assert hooks.fertig_pruefen_und_melden(eingabe) is True
    assert json.loads(fertig.read_text(encoding="utf-8"))["grund"] == "Ticket fertig gebaut, wartet auf Live"
    fertig.unlink()

    # (i) Frischer Handoff im Worktree → Staffel hat Vorrang, keine Fertig-Datei.
    monkeypatch.setattr(ticket_zustand, "lesen", lambda _t, _r=None: {"state": "CLOSED", "labels": []})
    handoff_dir = wt / "docs" / "handoffs"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "HANDOFF_2026-11-26_9004.md").write_text("Staffel: weiter\n", encoding="utf-8")
    assert hooks.fertig_pruefen_und_melden(eingabe) is False
    assert not fertig.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Nur Linux-Teil (Bau-Server, #288)")
def test_288_fertig_wache_respektiert_review_block_und_beendet_nach_stille(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wache in ``starte_mit_umzug_wache``: blockiert ein paralleler Stop-Hook
    (z. B. ``review/stop.py`` wie in Probe 5 ``bau 62`` L388–L471), läuft das Transkript
    weiter — die Wache verwirft die erste Fertig-Datei statt vorzeitig zu beenden.
    Erst nach echter Stille am Ende der zweiten Runde beendet sie den Prozessbaum."""
    monkeypatch.setenv("TO_SPAWN_FERTIG_RUHE_S", "0.4")
    umzug_datei = tmp_path / "umzug.json"
    fertig_datei = tmp_path / "fertig.json"
    transkript = tmp_path / "session.jsonl"
    phase_log = tmp_path / "phasen.txt"
    kind_pid_datei = tmp_path / "kind.pid"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(bin_dir))
    skript = bin_dir / "fake_claude_288.py"
    skript.write_text(
        f"""#!{sys.executable}
import json, subprocess, sys, time
from pathlib import Path

transkript = Path({str(transkript)!r})
fertig = Path({str(fertig_datei)!r})
phasen = Path({str(phase_log)!r})
kind_pfad = Path({str(kind_pid_datei)!r})

# Kindprozess wie MCP-Server (ignoriert SIGHUP), muss mit beendet werden.
kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
kind_pfad.write_text(str(kind.pid), encoding="utf-8")

# Runde 1: erster Stopp-Hook meldet fertig, aber review/stop.py blockiert → neuer Zug im Transkript!
transkript.write_text(json.dumps({{"type": "assistant", "message": {{"content": []}}}}) + "\\n", encoding="utf-8")
size1 = transkript.stat().st_size
fertig.write_text(json.dumps({{"ticket": "9004", "grund": "Ticket zu", "zeit": time.time(), "transcript_path": str(transkript), "transcript_size": size1}}), encoding="utf-8")
time.sleep(0.15)
with transkript.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"type": "user", "message": {{"role": "user", "content": "Stop hook feedback: Review fällig"}}}}) + "\\n")
phasen.write_text("runde1_ueberlebt\\n", encoding="utf-8")
time.sleep(0.55)
with phasen.open("a", encoding="utf-8") as fh:
    fh.write("nach_runde1_noch_aktiv\\n")

# Runde 2: finaler Stopp (nur system/cost-state danach) → Session bleibt still und muss beendet werden.
size2 = transkript.stat().st_size
fertig.write_text(json.dumps({{"ticket": "9004", "grund": "Ticket zu", "zeit": time.time(), "transcript_path": str(transkript), "transcript_size": size2}}), encoding="utf-8")
with transkript.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"type": "system", "subtype": "stop_hook_summary"}}) + "\\n")
    fh.write(json.dumps({{"type": "cost-state"}}) + "\\n")
time.sleep(30)
""",
        encoding="utf-8",
    )

    skript.chmod(0o755)
    start_zeit = time.monotonic()
    _code, umzug_daten = umzug.starte_mit_umzug_wache(
        [str(skript)],
        umzug_datei,
        takt=0.05,
        frist=3.0,
        fertig_datei=fertig_datei,
    )
    dauer = time.monotonic() - start_zeit
    assert umzug_daten is None
    assert dauer < 8.0, f"Wache hat stille fertige Session nicht beendet ({dauer:.1f}s)"
    assert phase_log.read_text(encoding="utf-8").splitlines() == [
        "runde1_ueberlebt",
        "nach_runde1_noch_aktiv",
    ]
    fertig_ergebnis = umzug.lies_fertig(fertig_datei)
    assert fertig_ergebnis is not None and fertig_ergebnis.get("beendet") is True
    kind_pid = int(kind_pid_datei.read_text(encoding="utf-8").strip())
    assert not prozessbaum.lebt(kind_pid), f"Kindprozess {kind_pid} überlebte das Session-Ende"


def test_288_laufende_bau_session_schliesst_tmux_fenster_nach_ticket_schluss(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe-5-Beleg (Abnahme 3 & 4, Repo-Präfix-Sitzung ``spec-probe-288-9001``):
    - ``bau.py 9004 --sofort`` startet in einem tmux-Fenster bei OFFENEM Ticket #9004.
    - Die interaktive Claude-Attrappe schließt #9004 während des Laufs, ruft den echten
      Stop-Hook ``to_spawn.py hook-stop`` auf und bleibt am Prompt sitzen (wie in Probe 5).
    - ``bau.py`` erkennt den fertigen Stand nach Ruhefrist, beendet Claude + Kindprozess
      sauber und endet mit Exit 0 → das tmux-Fenster schließt sich selbst ohne ``/exit``
      von außen und ohne Fehlerzeile im Fenster-Log.
    - Zwei Aufpasser-Takte hintereinander auf der Repo-Präfix-Sitzung (ohne ``maschine.json``)
      starten 0 neue Fenster und hinterlassen weder Bau-Fenster noch Bau-Prozesse."""
    sitzung_neu = f"spec-probe5-{SPEC}"
    tmux("rename-session", "-t", f"={SITZUNG}", sitzung_neu)

    zu_flag = welt.tmp / "ticket_9004_zu.flag"
    kind_pid_datei = welt.tmp / "bau_9004_kind.pid"
    transkript = welt.tmp / "bau_9004_transcript.jsonl"
    bin_9004 = welt.tmp / "bin_9004"
    bin_9004.mkdir()

    # Eigene gh-Attrappe: liest zu_flag dynamisch (erst OPEN, nach Abschluss CLOSED).
    gh_stub = SKILL / "tests" / "hilfen" / "gh_stub.py"
    gh_wrap = bin_9004 / "gh"
    gh_wrap.write_text(
        f"""#!{sys.executable}
import os, sys
from pathlib import Path
if Path({str(zu_flag)!r}).is_file():
    alt = os.environ.get("GH_STUB_ZU", "")
    os.environ["GH_STUB_ZU"] = (alt + ",9004").lstrip(",")
os.execv({sys.executable!r}, [{sys.executable!r}, {str(gh_stub)!r}, *sys.argv[1:]])
""",
        encoding="utf-8",
    )
    gh_wrap.chmod(0o755)

    # Interaktive Claude-Attrappe: legt Kindprozess an, schließt #9004, feuert echten
    # Stop-Hook (``to_spawn.py hook-stop``) und bleibt dann wie echtes Claude am Prompt stehen.
    claude_fake = bin_9004 / "claude"
    claude_fake.write_text(
        f"""#!{sys.executable}
import json, os, subprocess, sys, time
from pathlib import Path

kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path({str(kind_pid_datei)!r}).write_text(str(kind.pid), encoding="utf-8")

# Ticket wird während des Laufs geschlossen.
Path({str(zu_flag)!r}).write_text("closed\\n", encoding="utf-8")
transkript = Path({str(transkript)!r})
transkript.write_text(json.dumps({{"type": "assistant", "message": {{"content": []}}}}) + "\\n", encoding="utf-8")

# Echten Stop-Hook aufrufen wie Claude Code am Ende des Zuges.
eingabe = json.dumps({{
    "session_id": os.environ.get("BAU_SESSION_ID", "sid-9004"),
    "transcript_path": str(transkript),
    "stop_hook_active": False,
    "last_assistant_message": "Nichts zu tun, Ticket #9004 ist geschlossen.",
}})
subprocess.run(
    [sys.executable, {str(SKILL / "to_spawn.py")!r}, "hook-stop"],
    input=eingabe,
    text=True,
    check=False,
)
# Bleibt interaktiv am Prompt stehen (ohne Fix bliebe das Fenster für immer offen).
time.sleep(60)
""",
        encoding="utf-8",
    )
    claude_fake.chmod(0o755)

    manifest_dir = welt.repo / "docs" / "agents" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {"spec": int(SPEC), "feature": "probe-288", "tickets": {"9004": {"title": "Ticket 9004"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx_plugin = welt.heim / ".claude" / "plugins" / "cache" / "context-mode" / "context-mode" / "1.0.0"
    ctx_plugin.mkdir(parents=True, exist_ok=True)
    (ctx_plugin / "start.mjs").write_text("// stub\n", encoding="utf-8")

    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9004")
    monkeypatch.setenv("GH_STUB_ZU", "")
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    sperren = welt.tmp / "sperren_9004"
    log_datei = capo.fenster_log(welt.repo, 9004)
    bau_zeile = capo.bau_startzeile(welt.repo, 9004)
    start_cmd = (
        f"unset {startstelle.TEST_OFFEN_ENV}; "
        f"export HOME={welt.heim} "
        f"PATH={bin_9004}:{welt.bin}:$PATH "
        f"TO_SPAWN_TEST_BIN={bin_9004} "
        f"TO_SPAWN_GH_STUB={gh_wrap} "
        f"TO_SPAWN_GH_REPO=probe/repo "
        f"TO_SPAWN_START_SPERREN={sperren} "
        f"TO_SPAWN_TEST_FERN_SPERRE_AUS=1 "
        f"TO_SPAWN_FERTIG_RUHE_S=0.3 "
        f"TMPDIR={welt.tmp}; "
        f"{bau_zeile}"
    )
    tmux("new-window", "-d", "-t", f"={sitzung_neu}", "-n", "bau 9004", "-c", str(welt.repo), f"bash -lc {start_cmd!r}")

    def _fenster() -> list[str]:
        return tmux("list-windows", "-t", f"={sitzung_neu}", "-F", "#{window_name}").splitlines()

    frist = time.monotonic() + 20.0
    while "bau 9004" in _fenster() and time.monotonic() < frist:
        time.sleep(0.2)
    assert "bau 9004" not in _fenster(), (
        f"Fenster bau 9004 schloss sich nach Ticket-Schluss nicht selbst: "
        f"{log_datei.read_text(encoding='utf-8', errors='replace') if log_datei.is_file() else ''}"
    )
    assert zu_flag.is_file(), "Fake-Claude lief gar nicht an"
    assert kind_pid_datei.is_file()
    kind_pid = int(kind_pid_datei.read_text(encoding="utf-8").strip())
    assert not prozessbaum.lebt(kind_pid), f"Kindprozess {kind_pid} lief nach Fensterschluss weiter"
    if log_datei.is_file():
        assert "bau.py #9004 Exit " not in log_datei.read_text(encoding="utf-8", errors="replace")

    # Zwei reguläre Aufpasser-Takte auf der Repo-Präfix-Sitzung (ohne maschine.json):
    # erkennt spec-probe5-9001, startet 0 neue Fenster, keine Fehlalarme.
    monkeypatch.setenv("GH_STUB_ZU", "9004")
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(welt.tmp / "maschine_leer.json"))
    assert welt.lauf(hang_min=90) == 0
    assert _fenster() == [f"wache {SPEC}"]
    assert welt.lauf(hang_min=90) == 0
    assert _fenster() == [f"wache {SPEC}"]
    log_text = welt.log()
    assert log_text.count(f"{sitzung_neu}: 1 Fenster (wache {SPEC})") == 2, log_text
    kommentare = welt.kommentare()
    assert not any("startet nicht" in k for k in kommentare), kommentare
    assert not any("braucht den Menschen" in k for k in kommentare), kommentare
