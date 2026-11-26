"""#758 (Windows-Teil): Nachstellung und Einordnung der Probe-1330-Beobachtungen am PC.

Prüft alle vier offenen PC-Beobachtungen aus #758 (aufgeteilt aus #291), vollständig ohne
neue Terminal-Fenster, ohne ``wt``-Tabs und ohne ``spawn_local.ps1``-Start:

1. **6 Minuten Anlauf** (App-Ticket 1477, ``system:kern`` + Windows-Verstärker):
   ``bau.py`` ruft vor dem Claude-Start ``speicher.auf_platte_warten()`` →
   ``hausmeister.start_erlaubt(loeschen=True)`` synchron auf (umgeht ``_platte_je_start`` aus #617).
   Unter ``PLATZNOT_ANTEIL`` (15 %; am PC real 9,5 % frei) läuft ein voller Hausmeister-Rundgang
   über alle Ordner inkl. fremder Tickets (je Ordner 2× ``gh`` in ``_schliesszeit`` + ``_regeln``;
   unter Windows zudem je Ordner ein ungecachter ``powershell``-``Win32_Process``-Aufruf), während
   ``sessions_stand.py`` das Ticket als ``wartet`` meldet.
2. **Toter Tab** (``system:windows``):
   ``spawn_local.ps1`` und ``spawn.wt_tab_befehl`` starten Tabs mit ``pwsh -NoExit``.
   ``pc_fenster.lauf_aus`` sucht in der Eltern-Kommandozeile nach ``bau.py <N>``, verpasst damit
   den ``spawn_local.ps1``-Tab ``pwsh -NoExit -Command "bau <N>"`` (``tab_pid is None``) —
   ``pc_fenster.abloesen`` und ``spawn.neustart --beenden`` beenden nur ``bau.py``/``claude``,
   lassen den leeren ``pwsh -NoExit``-Tab ohne Kind-Prozess stehen und öffnen einen zweiten Tab.
3. **Session merkt „ticket-rot“ 29 min nicht** (``system:kern``):
   ``ticket-rot`` ist ein Urteil der App-Repo-Deploy-Schlange; ``warten.py`` schreibt das Ende
   (``status="fehlgeschlagen"``) nur in ``.to-spawn/warten/<N>.json`` ohne aktiven Weckruf.
   ``briefkasten_wecker`` beobachtet weder ``warten``-Dateien noch Schlangen-Urteile und läuft
   am PC (ohne tmux, ``kann_tippen == False``) gar nicht — die Session merkt es erst beim
   nächsten ``ScheduleWakeup``.
4. **Session außerhalb von ``bau.py`` fortgesetzt** (``system:kern``, Auslöser mit #278 entfallen):
   Ohne ``bau.py`` greift ``staffel_stop.py`` nicht, ``sessions_stand.py`` meldet rohes
   ``claude --resume`` ohne ``…-bau/<N>-…/settings.json`` als ``aus``, ``pc_fenster.lauf_aus``
   sieht ``None`` und ``startstelle`` hält keine Sperre. Der ursprüngliche Anlass (Freigabe per
   Neustart im Start-Auftrag) ist auf ``main`` durch #278 (``befugnis`` + Briefkasten) sowie
   ``bau.py --auftrag``/#285 und ``--resume``/#236 entfallen.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import (  # noqa: E402
    befugnis,
    briefkasten_wecker,
    hausmeister,
    hausmeister_windows,
    pc_fenster,
    prozessbaum,
    respawn,
    spawn,
    speicher,
    starter,
    warten,
)

_FT = 11644473600.0
T0 = 1_790_000_000.0


def _ft(unix: float) -> int:
    return int((unix + _FT) * 1e7)


def _lade_skript(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


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


# --- Punkt 1: 6 Minuten Anlauf (bau.py → auf_platte_warten → Hausmeister) ----


def test_punkt1_auf_platte_warten_fragt_fremde_tickets_doppelt_und_prozesse_je_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bei <15 % freier Platte startet ``auf_platte_warten`` den Hausmeister synchron für alle Ordner."""
    wurzeln = tmp_path / "dev"
    origin_url = "https://github.com/probe-org/app-repo.git"
    _mini_klon(wurzeln / "wt-1477", origin_url, "ticket/1477-probe")
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
    altbestand = tmp_path / "altbestand.json"
    altbestand.write_text(json.dumps({"ordner": []}), encoding="utf-8")

    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(wurzeln / "wt-*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.095")  # wie real am PC (< 15 % Platznot, >= 5 % Sperre)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "0")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ALTBESTAND", str(altbestand))
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "win32")

    gh_abfragen: list[tuple[int, str | None]] = []

    def _fake_gh_ticket(ticket: int, repo: str | None) -> dict[str, object]:
        gh_abfragen.append((ticket, repo))
        if ticket == 1477:
            return {"state": "OPEN", "closedAt": None}
        return {"state": "CLOSED", "closedAt": "2026-11-01T10:00:00Z"}

    prozess_aufrufe = 0

    def _fake_win_prozesse() -> list[tuple[int | None, str | None, str | None]]:
        nonlocal prozess_aufrufe
        prozess_aufrufe += 1
        # Beide fremden Ordner bleiben wegen laufendem Prozess stehen — nichts wird gelöscht.
        return [
            (99991, str(wurzeln / "wt-1498"), None),
            (99992, str(wurzeln / "wt-1499"), None),
        ]

    monkeypatch.setattr(hausmeister, "_gh_ticket", _fake_gh_ticket)
    monkeypatch.setattr(hausmeister, "_keine_spec_sitzung", lambda _ticket: None)
    monkeypatch.setattr(hausmeister_windows, "_prozesse", _fake_win_prozesse)
    monkeypatch.setattr(hausmeister_windows, "_kein_arbeitsordner_drin", lambda _ordner: None)
    monkeypatch.setattr(hausmeister.temp_ordner, "aufraeumen", lambda **_kw: [])
    monkeypatch.setattr(hausmeister.uebergabe_sichern, "sichern", lambda **_kw: None)

    zyklen = speicher.auf_platte_warten(wer="bau 1477")
    assert zyklen == 0, "9,5 % >= 5 % Startsperre: kein Wartezyklus"
    assert gh_abfragen == [], "Kern-Fix #781: auf_platte_warten startet bei 9,5 % ohne synchronen Fremd-Ticket-Scan"
    assert prozess_aufrufe == 0

    # Direkter Hausmeister-Rundgang bei Platznot: Ticket-Daten (#781 Kern) und
    # Windows-Prozessliste (#781 Windows) werden je Rundgang einmal gecacht.
    rund = hausmeister.rundgang(loeschen=True)
    assert rund.gelaufen is True
    gefragte_tickets = [nr for nr, _ in gh_abfragen]
    assert 1498 in gefragte_tickets and 1499 in gefragte_tickets
    assert gefragte_tickets.count(1498) == 1 and gefragte_tickets.count(1499) == 1, (
        "Kern-Fix #781: jeder Ordner wird je Rundgang höchstens einmal per gh abgefragt (_TICKET_DATEN_CACHE)"
    )
    assert prozess_aufrufe == 1, "Windows-Fix #781: hausmeister_windows._prozesse() wird je Rundgang einmal gecacht"


def test_punkt1_sessions_stand_meldet_wartet_solange_bau_py_vor_claude_steht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Während ``bau.py`` vor dem Claude-Start im Hausmeister/Blocker hängt, zeigt ``sessions`` „wartet“."""
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    ss = _lade_skript("sessions_stand_probe758_p1", SKILL / "skripte" / "sessions_stand.py")
    eintraege = {"1477": ss.Eintrag("1477", "ticket", "App-Ticket 1477")}
    alle = [
        ss.Prozess(
            pid=5100,
            ppid=5000,
            name="python.exe",
            cmd=r'"C:\Python313\python.exe" ./scripts/bau.py 1477',
            start=None,
            ordner=tmp_path,
        )
    ]
    ss.zuordnen(eintraege, alle)
    assert eintraege["1477"].zustand == "wartet"
    assert eintraege["1477"].pid == 5100
    assert eintraege["1477"].session_pid is None


# --- Punkt 2: Toter Tab (Windows, ohne wt-Start nachgestellt) ----------------


def test_punkt2_pc_fenster_lauf_aus_verpasst_spawn_local_tab() -> None:
    """``spawn_local.ps1`` öffnet ``pwsh -NoExit -Command "bau <N>"`` — ``pc_fenster.lauf_aus`` erkennt den Tab (#782)."""
    prozesse = {
        100: (4, r"C:\Program Files\WindowsApps\WindowsTerminal.exe", _ft(T0 - 30)),
        200: (100, r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477"', _ft(T0 - 20)),
        201: (200, r"C:\windows\system32\conhost.exe 0x4", _ft(T0 - 20)),
        300: (200, r'"C:\Python313\python.exe" ./scripts/bau.py 1477', _ft(T0 - 10)),
        400: (
            300,
            r'"C:\Users\x\.local\bin\claude.exe" --settings C:\tmp\x-bau\1477-20261122-204500\settings.json',
            _ft(T0),
        ),
    }
    lauf = pc_fenster.lauf_aus(prozesse, 1477)
    assert lauf.bau_pid == 300
    assert lauf.session_pid == 400
    assert lauf.tab_pid == 200, "Fix #782: spawn_local-Tab mit 'bau 1477' wird als alleiniger Tab-pwsh erkannt"


def test_punkt2_pc_fenster_abloesen_laesst_spawn_local_tab_stehen_und_oeffnet_zweiten(tmp_path: Path) -> None:
    """Ohne ``tab_pid`` beendet ``pc_fenster.abloesen`` nur ``bau.py`` und öffnet einen zweiten Tab."""
    beendet: list[int] = []
    gestartet: list[tuple[int, str]] = []

    class _FakePcWerkzeug(pc_fenster.PcWerkzeug):
        def __init__(self) -> None:
            self._abfragen = 0

        def alte_session(self, repo: Path, spec: int, ticket: int) -> pc_fenster.Alte:
            self._abfragen += 1
            if self._abfragen == 1:
                return pc_fenster.Alte(bau_pid=300, session_pid=400)
            return pc_fenster.Alte(bau_pid=500, session_pid=600)

        def lauf(self, ticket: int) -> pc_fenster.Lauf:
            return pc_fenster.Lauf(tab_pid=None, bau_pid=300, session_pid=400, start=T0, werkzeug_arbeitet=False)

        def baum_beenden(self, pid: int) -> bool:
            beendet.append(pid)
            return True

        def tab_starten(self, repo: Path, ticket: int, auftrag: str) -> None:
            gestartet.append((ticket, auftrag))

        def jetzt(self) -> float:
            return T0 + 600.0

        def schlafen(self, s: float) -> None:
            pass

    auftrag = respawn.Auftrag(spec=1330, ticket=1477, repo=tmp_path, warte_max=30.0)
    erg = pc_fenster.abloesen(auftrag, _FakePcWerkzeug())
    assert erg.exit == respawn.EXIT_OK
    assert beendet == [300], "Nur bau.py (300) beendet — der Eltern-pwsh-Tab (-NoExit) lebt ohne Kind-Prozess weiter"
    assert len(gestartet) == 1 and gestartet[0][0] == 1477, "Zweiter Tab für dasselbe Ticket wird geöffnet"


def test_punkt2_spawn_local_und_wt_tab_befehl_halten_tabs_mit_noexit_offen(tmp_path: Path) -> None:
    """``spawn_local.ps1`` und ``spawn.wt_tab_befehl`` behalten ``-NoExit`` für Fehler, schließen aber bei Exit 0 (#282/#782)."""
    ps1 = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert '"pwsh", "-NoExit", "-Command"' in ps1
    assert "`$LASTEXITCODE -eq 0" in ps1 and "exit 0" in ps1
    wt_cmd = spawn.wt_tab_befehl(tmp_path, 1477, "")
    assert "pwsh" in wt_cmd and "-NoExit" in wt_cmd
    innen = wt_cmd[wt_cmd.index("-Command") + 1]
    assert "$LASTEXITCODE -eq 0" in innen and "exit 0" in innen


# --- Punkt 3: Session merkt „ticket-rot“ 29 min nicht (Kern) -----------------


def test_punkt3_warten_persistiert_ticket_rot_aber_weckt_session_nicht(tmp_path: Path) -> None:
    """``warten.py`` schreibt ``fehlgeschlagen`` sofort in die Datei, weckt die Session aber nicht aktiv."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / "ticket_rot_758.py"
    skript.write_text("print('ticket-rot: Testlauf fehlgeschlagen')\nraise SystemExit(1)\n", encoding="utf-8")
    erg = warten.ausfuehren(
        tmp_path,
        1477,
        [sys.executable, str(skript)],
        pause_s=0.0,
    )
    assert erg.exit == warten.EXIT_FEHLER
    assert erg.status == "fehlgeschlagen"
    assert "ticket-rot" in erg.zeile

    zustand = json.loads(warten.zustand_pfad(tmp_path, 1477).read_text(encoding="utf-8"))
    assert zustand["status"] == "fehlgeschlagen"
    assert zustand["ergebnis_exit"] == 1

    # Erst der aktive Abruf (z. B. beim nächsten ScheduleWakeup) meldet das Ergebnis an die Session.
    abruf = warten.pruefen(tmp_path, 1477)
    assert abruf.exit == warten.EXIT_FEHLER
    assert abruf.status == "fehlgeschlagen"


def test_punkt3_pc_hat_keinen_pane_wecker_und_wecker_beobachtet_warten_nicht() -> None:
    """Am PC ist nichts tippbar (kein Briefkasten-Wecker); auch auf Linux kennt ``ereignisse`` kein ``ticket-rot``."""
    assert pc_fenster.kann_tippen(pc_fenster.PcWerkzeug()) is False
    # Offenes Ticket ohne Aufseher-Kommentar erzeugt kein Weck-Ereignis — auch wenn extern ticket-rot vorliegt.
    assert briefkasten_wecker.ereignisse({"1477": {"state": "OPEN", "comments": {"nodes": []}}}) == []


# --- Punkt 4: Session außerhalb von bau.py fortgesetzt (Kern, #278) ----------


def test_punkt4_session_ohne_bau_py_hat_keine_staffel_und_steht_auf_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direkt gestartetes ``claude --resume`` ohne ``bau.py``: keine Staffel, ``sessions`` = ``aus``, ``lauf_aus`` = leer."""
    for var in ("BAU_TICKET", "BAU_STAFFEL_DATEI", "BAU_SESSION_START", "BAU_HANDOFF_DIRS", "BAU_WORKTREE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    staffel = _lade_skript("staffel_stop_probe758_p4", SKILL / "skripte" / "hooks" / "staffel_stop.py")
    assert staffel.main() == 0

    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    ss = _lade_skript("sessions_stand_probe758_p4", SKILL / "skripte" / "sessions_stand.py")
    eintraege = {"1476": ss.Eintrag("1476", "ticket", "App-Ticket 1476")}
    wt = tmp_path / "wt-1476"
    wt.mkdir()
    alle = [
        ss.Prozess(
            pid=6200,
            ppid=1,
            name="claude.exe",
            cmd=r'"C:\Users\x\.local\bin\claude.exe" --resume sess-1476 "Freigabe des Nutzers"',
            start=None,
            ordner=wt,
        )
    ]
    ss.zuordnen(eintraege, alle)
    assert eintraege["1476"].zustand == "aus", "Ohne …-bau/<N>-…/settings.json erkennt VERWAIST die Session nicht"

    prozesse = {
        6200: (1, r'"C:\Users\x\.local\bin\claude.exe" --resume sess-1476', _ft(T0)),
    }
    lauf = pc_fenster.lauf_aus(prozesse, 1476)
    assert lauf.bau_pid is None and lauf.session_pid is None


def test_punkt4_ausloeser_durch_278_befugnis_und_bau_auftrag_entfallen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auf ``main`` liefert ``bau.py`` Befugnis-Klassen (#278) und ``--auftrag`` (#285) — kein Start ohne ``bau.py``."""
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    bau = _lade_skript("bau_probe758_p4", SKILL / "skripte" / "bau.py")
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    prompt = bau.build_prompt(
        vorlage["prompt_template"],
        "1476",
        "1330",
        "Probe-Ticket",
        "- modul.py",
        konfig={},
        repo=tmp_path,
        gh_repo="probe-org/app-repo",
    )
    assert befugnis.KOPF in prompt
    assert "to_spawn.py frage 1476" in prompt
    mit_auftrag = bau.auftrag_prompt(prompt, "Freigabe liegt vor")
    assert "## Auftrag dieser Runde" in mit_auftrag and "Freigabe liegt vor" in mit_auftrag


# --- Doku-Absicherung (Rot vor Ergänzung in SKILL.md) ------------------------


def test_skill_md_dokumentiert_probe_1330_pc_lehren() -> None:
    """``SKILL.md`` hält die PC-Lehren aus #758 fest (nie außerhalb ``bau.py``, ``-NoExit``-Tab-Falle)."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "#758" in text
    assert "bau.py" in text and "--resume" in text and "aus" in text
    assert "-NoExit" in text
