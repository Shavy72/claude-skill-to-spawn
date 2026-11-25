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

from to_spawn import aufpasser, auswahl, capo, startstelle, ticket_zustand  # noqa: E402


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
