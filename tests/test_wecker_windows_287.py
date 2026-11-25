"""#287: Externer Wecker unter Windows — geplante Aufgabe (schtasks) und Session-Anstoß.

Deckt ab:
1. Windows-Werkzeugkasten ``WindowsWecker`` hinter derselben Schnittstelle ``SystemWecker`` (L6):
   - Geplante Aufgabe über ``schtasks /Create /F /SC MINUTE /MO <M>`` mit ``pythonw.exe``
     (fensterlos, idempotent, ``PT1H`` bei 60 min).
   - Echte ``schtasks``-Einrichtung auf dem Windows-PC (mit Wegwerf-Aufgabenname + Aufräumen).
2. Nachgestellter Wochen-Limit-Fall über den echten Windows-Startweg (``starter.starte`` +
   Attrappe in ``TO_SPAWN_TEST_BIN`` + echtes Transkript ``.jsonl``):
   - Weckruf vor Reset scheitert am Limit → im Log ``wecker.jsonl`` mit Grund.
   - Vor dem Reset kein Spam (``wartet_auf_reset``).
   - Spätestens M min nach dem Reset weckt der Wecker die Session ohne Eingriff weiter,
     obwohl die alte Limit-Meldung noch im Transkript steht.
3. Schutz gegen Doppelstart bei laufender (``busy``/``shell``) oder noch arbeitender
   Weck-Session sowie Erkennung von Limit-Fehlern auf stdout/stderr ohne Transkript-Zeile.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent


def _schtasks_xml(befehl: str, argumente: str, intervall: str) -> str:
    """So sieht ``schtasks /Query /TN <name> /XML`` für eine MINUTE-Aufgabe unter Windows aus."""
    argumente_esc = argumente.replace("&", "&amp;").replace('"', "&quot;")
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        "  <RegistrationInfo>\r\n    <URI>\\to-spawn Wecker</URI>\r\n  </RegistrationInfo>\r\n"
        "  <Triggers>\r\n    <TimeTrigger>\r\n"
        "      <StartBoundary>2026-11-25T19:00:00</StartBoundary>\r\n"
        "      <Repetition>\r\n"
        f"        <Interval>{intervall}</Interval>\r\n"
        "      </Repetition>\r\n"
        "    </TimeTrigger>\r\n  </Triggers>\r\n"
        '  <Actions Context="Author">\r\n    <Exec>\r\n'
        f'      <Command>"{befehl}"</Command>\r\n'
        f"      <Arguments>{argumente_esc}</Arguments>\r\n"
        "    </Exec>\r\n  </Actions>\r\n</Task>\r\n"
    )


class _SchtasksNaht:
    """Naht für ``subprocess.run`` bei ``schtasks``-Aufrufen."""

    def __init__(self) -> None:
        self.xml: str | None = None
        self.aufrufe: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append((list(args), dict(kw)))
        if "/Query" in args:
            if self.xml is None:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    "",
                    "FEHLER: Das System kann die angegebene Datei nicht finden.\r\n",
                )
            return subprocess.CompletedProcess(args, 0, self.xml, "")
        if "/Create" in args:
            tr = args[args.index("/TR") + 1]
            befehl, _, rest = tr[1:].partition('" ')
            mo = int(args[args.index("/MO") + 1])
            intervall = "PT1H" if mo == 60 else f"PT{mo}M"
            self.xml = _schtasks_xml(befehl, rest, intervall)
            return subprocess.CompletedProcess(
                args,
                0,
                'ERFOLGREICH: Die geplante Aufgabe "to-spawn Wecker" wurde erstellt.\r\n',
                "",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    def creates(self) -> list[list[str]]:
        return [a for a, _ in self.aufrufe if "/Create" in a]


def _fake_claude_einrichten(
    *,
    transkript: Path,
    limit_flag: Path,
    spur: Path,
    env_beleg: Path | None = None,
    nur_stdout: bool = False,
    schlafen_s: float = 0.0,
) -> Path:
    """Schreibt eine Attrappe ``claude`` in ``TO_SPAWN_TEST_BIN`` für echte Unterprozess-Läufe."""
    from to_spawn import starter

    attrappen = Path(os.environ[starter.TEST_BIN_ENV])
    skript = attrappen / "claude"
    skript.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, os, sys, time",
                "from pathlib import Path",
                f"TRANSKRIPT = Path({str(transkript)!r})",
                f"FLAG = Path({str(limit_flag)!r})",
                f"SPUR = Path({str(spur)!r})",
                f"ENV_BELEG = {str(env_beleg)!r}",
                f"NUR_STDOUT = {nur_stdout!r}",
                f"SCHLAFEN_S = {schlafen_s!r}",
                "args = sys.argv[1:]",
                "if ENV_BELEG:",
                "    Path(ENV_BELEG).write_text(",
                "        json.dumps({'claudecode': os.environ.get('CLAUDECODE'), 'args': args}),",
                "        encoding='utf-8',",
                "    )",
                "prompt = args[-1] if args else ''",
                "if SCHLAFEN_S > 0:",
                "    with SPUR.open('a', encoding='utf-8') as f:",
                "        f.write(f'START:{prompt}\\n')",
                "    time.sleep(SCHLAFEN_S)",
                "    sys.exit(0)",
                "if FLAG.exists():",
                "    meldung = FLAG.read_text(encoding='utf-8').strip()",
                "    with SPUR.open('a', encoding='utf-8') as f:",
                "        f.write(f'LIMIT:{prompt}\\n')",
                "    if NUR_STDOUT:",
                "        print(meldung, flush=True)",
                "        sys.exit(1)",
                "    TRANSKRIPT.parent.mkdir(parents=True, exist_ok=True)",
                "    with TRANSKRIPT.open('a', encoding='utf-8') as f:",
                "        f.write(json.dumps({'type': 'user', 'message': {'content': prompt}}) + '\\n')",
                "        f.write(json.dumps({",
                "            'type': 'assistant',",
                "            'isApiErrorMessage': True,",
                "            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': meldung}]},",
                "        }) + '\\n')",
                "    sys.exit(1)",
                "with SPUR.open('a', encoding='utf-8') as f:",
                "    f.write(f'WEITER:{prompt}\\n')",
                "TRANSKRIPT.parent.mkdir(parents=True, exist_ok=True)",
                "with TRANSKRIPT.open('a', encoding='utf-8') as f:",
                "    f.write(json.dumps({'type': 'user', 'message': {'content': prompt}}) + '\\n')",
                "    f.write(json.dumps({",
                "        'type': 'assistant',",
                "        'message': {'content': [{'type': 'text', 'text': f'Weiterarbeit aufgenommen: {prompt}'}]},",
                "    }) + '\\n')",
                "print(f'Weiterarbeit aufgenommen: {prompt}', flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    skript.chmod(0o755)
    return skript


def test_windows_aufgabe_intervall_und_gleichheit(tmp_path: Path) -> None:
    """Intervall-Formatierung (inkl. PT1H für 60 min) und XML-Abgleich für Windows-Aufgaben."""
    from to_spawn import wecker

    assert wecker.windows_aufgabe_intervall(1) == "PT1M"
    assert wecker.windows_aufgabe_intervall(5) == "PT5M"
    assert wecker.windows_aufgabe_intervall(60) == "PT1H"
    for ungueltig in (0, -1, 61):
        with pytest.raises(ValueError):
            wecker.windows_aufgabe_intervall(ungueltig)

    zustand = tmp_path / "wecker-zustand"
    pythonw = str(tmp_path / "Python" / "pythonw.exe")
    argumente = wecker.windows_aufgabe_argumente(zustand)
    assert "wecker lauf" in argumente
    assert str(zustand) in argumente

    xml5 = _schtasks_xml(pythonw, argumente, "PT5M")
    assert wecker.windows_aufgabe_gleich(xml5, pythonw, argumente, 5)
    assert not wecker.windows_aufgabe_gleich(xml5, pythonw, argumente, 10)
    assert not wecker.windows_aufgabe_gleich(xml5, str(tmp_path / "anderes" / "pythonw.exe"), argumente, 5)
    assert not wecker.windows_aufgabe_gleich(None, pythonw, argumente, 5)

    xml60 = _schtasks_xml(pythonw, argumente, "PT1H")
    assert wecker.windows_aufgabe_gleich(xml60, pythonw, argumente, 60)


def test_windows_cron_einrichten_mit_naht_idempotent_und_pythonw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WindowsWecker.cron_einrichten`` legt die Aufgabe mit ``pythonw.exe`` idempotent an."""
    from to_spawn import wecker

    py = tmp_path / "Python" / "python.exe"
    py.parent.mkdir()
    py.write_text("", encoding="utf-8")
    pythonw = py.parent / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    zustand = tmp_path / "wecker-zustand"

    naht = _SchtasksNaht()
    monkeypatch.setattr(wecker.subprocess, "run", naht)

    kasten = wecker.WindowsWecker(zustand=zustand)
    trocken = kasten.cron_einrichten(zustand, takt_min=5, trocken=True, python=str(py))
    assert "[trocken]" in trocken and "alle 5 min" in trocken
    assert naht.creates() == []

    erster = kasten.cron_einrichten(zustand, takt_min=5, trocken=False, python=str(py))
    assert "eingerichtet" in erster
    (create,) = naht.creates()
    assert create[:2] == ["schtasks", "/Create"] and "/F" in create
    assert create[create.index("/SC") + 1] == "MINUTE"
    assert create[create.index("/MO") + 1] == "5"
    assert create[create.index("/TN") + 1] == wecker.WINDOWS_AUFGABE_NAME
    tr = create[create.index("/TR") + 1]
    assert tr == f'"{pythonw}" "{SKILL / "to_spawn.py"}" wecker lauf --zustand "{zustand}"'
    assert "python.exe" not in tr
    for _, kw in naht.aufrufe:
        assert kw.get("timeout")
        if sys.platform == "win32":
            flags = int(kw["creationflags"])
            assert flags == (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            assert not (flags & 0x00000008)  # kein DETACHED_PROCESS

    zweiter = kasten.cron_einrichten(zustand, takt_min=5, trocken=False, python=str(py))
    assert "unverändert" in zweiter
    assert len(naht.creates()) == 1

    dritter = kasten.cron_einrichten(zustand, takt_min=10, trocken=False, python=str(py))
    assert "eingerichtet" in dritter and "alle 10 min" in dritter
    assert len(naht.creates()) == 2

    # Ohne pythonw.exe klarer Fehler.
    nackt = tmp_path / "nackt" / "python.exe"
    nackt.parent.mkdir()
    nackt.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="pythonw.exe"):
        kasten.cron_einrichten(zustand, takt_min=5, trocken=False, python=str(nackt))

    # Fehlendes schtasks liefert klaren Fehler.
    def _weg(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(wecker.subprocess, "run", _weg)
    with pytest.raises(RuntimeError, match="schtasks"):
        kasten.cron_einrichten(zustand, takt_min=5, trocken=False, python=str(py))


@pytest.mark.skipif(sys.platform != "win32", reason="schtasks gibt es nur unter Windows")
def test_echt_windows_schtasks_einrichten_idempotent(tmp_path: Path) -> None:
    """Echte geplante Aufgabe am Windows-PC anlegen, prüfen (5 min + 60 min/PT1H), aufräumen."""
    from to_spawn import wecker

    name = f"to-spawn Wecker Test {uuid.uuid4().hex[:8]}"
    zustand = tmp_path / "wecker-echt"
    kasten = wecker.WindowsWecker(zustand=zustand, aufgabe_name=name)
    try:
        trocken = kasten.cron_einrichten(zustand, takt_min=5, trocken=True)
        assert "[trocken]" in trocken and "alle 5 min" in trocken

        eins = kasten.cron_einrichten(zustand, takt_min=5, trocken=False)
        assert "eingerichtet" in eins
        zwei = kasten.cron_einrichten(zustand, takt_min=5, trocken=False)
        assert "unverändert" in zwei

        abfrage = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/XML"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert abfrage.returncode == 0, abfrage.stderr
        assert "PT5M" in abfrage.stdout
        assert "pythonw.exe" in abfrage.stdout.lower()

        stunde = kasten.cron_einrichten(zustand, takt_min=60, trocken=False)
        assert "eingerichtet" in stunde
        stunde2 = kasten.cron_einrichten(zustand, takt_min=60, trocken=False)
        assert "unverändert" in stunde2
    finally:
        subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, check=False)


def test_windows_ziel_aufloesung_und_busy_schutz(tmp_path: Path) -> None:
    """Auflösung über Sessions-Datei, PID, Name und UUID sowie Schutz bei ``busy``/``shell``."""
    from to_spawn import waechter_lauf, wecker

    heim = tmp_path / "heim"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    transkript = waechter_lauf.transkript_ordner(repo, heim=heim) / f"{sid}.jsonl"
    transkript.parent.mkdir(parents=True, exist_ok=True)
    transkript.write_text(
        json.dumps({"type": "assistant", "cwd": str(repo), "message": {"content": "Leit-Session bereit"}}) + "\n",
        encoding="utf-8",
    )

    sessions_json = repo / ".to-spawn" / "sessions" / "wache-287.json"
    sessions_json.parent.mkdir(parents=True, exist_ok=True)
    sessions_json.write_text(json.dumps({"session_id": sid, "cwd": str(repo)}), encoding="utf-8")

    # Live-Session-JSON mit eigener lebender PID unter <heim>/.claude/sessions/<pid>.json.
    pid = os.getpid()
    live_json = heim / ".claude" / "sessions" / f"{pid}.json"
    live_json.parent.mkdir(parents=True, exist_ok=True)
    live_json.write_text(
        json.dumps({"pid": pid, "sessionId": sid, "cwd": str(repo), "name": "Ober-Aufsicht", "status": "idle"}),
        encoding="utf-8",
    )

    kasten = wecker.WindowsWecker(heim=heim, zustand=tmp_path / "zustand")
    for ziel in (str(sessions_json), f"pid:{pid}", "Ober-Aufsicht", f"session:{sid}", sid):
        stand = kasten.pane_lesen(ziel)
        assert stand.existiert, ziel
        assert stand.session_laeuft, ziel
        assert stand.status == "idle", ziel
        assert "Leit-Session bereit" in stand.text, ziel

    # Unbekanntes Ziel → existiert=False.
    fehlt = kasten.pane_lesen("nicht-vorhanden-287")
    assert not fehlt.existiert and "nicht gefunden" in fehlt.fehler

    # Busy und shell blockieren den Weckruf (uebersprungen).
    for aktiver_status in ("busy", "shell"):
        live_json.write_text(
            json.dumps(
                {"pid": pid, "sessionId": sid, "cwd": str(repo), "name": "Ober-Aufsicht", "status": aktiver_status}
            ),
            encoding="utf-8",
        )
        zustand = tmp_path / f"zustand-{aktiver_status}"
        wecker.auftrag_setzen(zustand, name="leit-pc", ziel="Ober-Aufsicht", text="Weckruf", takt_min=5)
        erg = wecker.durchgang(
            zustand,
            werkzeug=wecker.WindowsWecker(heim=heim, zustand=zustand),
            jetzt=1000.0,
            nachpruef_s=0.0,
        )
        assert len(erg) == 1
        assert erg[0].ereignis == "uebersprungen", erg[0]


def test_echter_windows_weg_limit_scheitert_und_nach_reset_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme 1–3 (#287) über den echten Windows-Startweg (``WindowsWecker`` + ``starter.starte``):
    1. Weckruf vor Reset scheitert am simulierten Wochen-Limit → im Log mit Grund.
    2. Vor dem Reset wartet der Wecker ab (kein Spam).
    3. Spätestens M min nach dem Reset weckt der Wecker die Session ohne Eingriff weiter,
       obwohl die alte Limit-Meldung noch im Transkript steht.
    """
    from to_spawn import waechter_lauf, wecker

    heim = tmp_path / "heim"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    zustand = tmp_path / "wecker-zustand"
    sid = f"win287-{uuid.uuid4().hex[:8]}"
    transkript = waechter_lauf.transkript_ordner(repo, heim=heim) / f"{sid}.jsonl"
    transkript.parent.mkdir(parents=True, exist_ok=True)
    transkript.write_text(
        json.dumps({"type": "assistant", "cwd": str(repo), "message": {"content": "Leit-Session gestartet"}}) + "\n",
        encoding="utf-8",
    )

    sessions_json = repo / ".to-spawn" / "sessions" / "leit-pc.json"
    sessions_json.parent.mkdir(parents=True, exist_ok=True)
    sessions_json.write_text(json.dumps({"session_id": sid, "cwd": str(repo)}), encoding="utf-8")

    limit_flag = tmp_path / "limit.flag"
    spur = tmp_path / "spur.txt"
    env_beleg = tmp_path / "env.json"
    limit_flag.write_text("95% of your weekly limit · resets 11pm\n", encoding="utf-8")
    _fake_claude_einrichten(transkript=transkript, limit_flag=limit_flag, spur=spur, env_beleg=env_beleg)

    # Geerbte Sitzungs-Variable darf nicht ans Kind durchgereicht werden.
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.setenv("HOME", str(heim))

    wecker.auftrag_setzen(
        zustand,
        name="leit-pc",
        ziel=str(sessions_json),
        text="Weckruf: Stand pruefen und weiterbauen",
        takt_min=5,
    )
    kasten = wecker.WindowsWecker(heim=heim, zustand=zustand)
    ortszone = datetime(2026, 11, 22, 22, 7).astimezone().tzinfo

    # 1) 22:07 — Weckruf trifft auf das Wochen-Limit.
    t_vor_reset = datetime(2026, 11, 22, 22, 7, tzinfo=ortszone).timestamp()
    erg1 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=t_vor_reset, nachpruef_s=0.6, puffer_s=120.0)
    assert len(erg1) == 1
    assert erg1[0].ereignis == "gescheitert", erg1[0]
    assert "weekly limit" in erg1[0].grund.lower(), erg1[0].grund

    beleg = json.loads(env_beleg.read_text(encoding="utf-8"))
    assert beleg["claudecode"] is None
    assert "--resume" in beleg["args"] and sid in beleg["args"]

    log_zeilen = [
        json.loads(z) for z in (zustand / "wecker.jsonl").read_text(encoding="utf-8").splitlines() if z.strip()
    ]
    assert any(
        z["name"] == "leit-pc" and z["ereignis"] == "gescheitert" and "weekly limit" in z["grund"].lower()
        for z in log_zeilen
    ), log_zeilen

    # 2) 22:25 — Limit-Reset (23:00 + 2 min Puffer) noch nicht vorbei → kein Spam.
    t_waehrend = datetime(2026, 11, 22, 22, 25, tzinfo=ortszone).timestamp()
    spur_vorher = spur.read_text(encoding="utf-8")
    erg2 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=t_waehrend, nachpruef_s=0.4, puffer_s=120.0)
    assert erg2[0].ereignis == "wartet_auf_reset", erg2[0]
    assert spur.read_text(encoding="utf-8") == spur_vorher

    # 3) 23:05 (≤ 5 min nach 23:00-Reset) — Limit weg → Session läuft ohne Eingriff weiter.
    limit_flag.unlink()
    t_nach_reset = datetime(2026, 11, 22, 23, 5, tzinfo=ortszone).timestamp()
    erg3 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=t_nach_reset, nachpruef_s=0.6, puffer_s=120.0)
    assert erg3[0].ereignis == "geweckt", erg3[0]
    assert "WEITER:Weckruf: Stand pruefen und weiterbauen" in spur.read_text(encoding="utf-8")


def test_windows_limit_nur_auf_stdout_wird_erkannt(tmp_path: Path) -> None:
    """Meldet ``claude -p`` das Limit nur auf stdout/stderr (ohne Transkript-Eintrag),
    erkennt ``WindowsWecker`` den Fehlschlag trotzdem und wartet auf den Reset."""
    from to_spawn import waechter_lauf, wecker

    heim = tmp_path / "heim"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    zustand = tmp_path / "wecker-stdout"
    sid = f"stdout-{uuid.uuid4().hex[:8]}"
    transkript = waechter_lauf.transkript_ordner(repo, heim=heim) / f"{sid}.jsonl"
    transkript.parent.mkdir(parents=True, exist_ok=True)
    transkript.write_text(
        json.dumps({"type": "assistant", "cwd": str(repo), "message": {"content": "Bereit"}}) + "\n",
        encoding="utf-8",
    )
    sessions_json = repo / ".to-spawn" / "sessions" / "leit.json"
    sessions_json.parent.mkdir(parents=True, exist_ok=True)
    sessions_json.write_text(json.dumps({"session_id": sid, "cwd": str(repo)}), encoding="utf-8")

    limit_flag = tmp_path / "limit.flag"
    spur = tmp_path / "spur.txt"
    limit_flag.write_text("You've hit your weekly limit · resets 11pm\n", encoding="utf-8")
    _fake_claude_einrichten(transkript=transkript, limit_flag=limit_flag, spur=spur, nur_stdout=True)

    wecker.auftrag_setzen(zustand, name="leit", ziel=str(sessions_json), text="Weckruf", takt_min=5)
    kasten = wecker.WindowsWecker(heim=heim, zustand=zustand)
    ortszone = datetime(2026, 11, 22, 22, 7).astimezone().tzinfo
    t_vor = datetime(2026, 11, 22, 22, 7, tzinfo=ortszone).timestamp()
    erg = wecker.durchgang(zustand, werkzeug=kasten, jetzt=t_vor, nachpruef_s=0.6, puffer_s=120.0)
    assert erg[0].ereignis == "gescheitert", erg[0]
    assert "weekly limit" in erg[0].grund.lower()
    assert erg[0].warten_bis > t_vor


def test_windows_laufender_weckprozess_sperrt_doppelstart(tmp_path: Path) -> None:
    """Solange ein angestoßener Windows-Weckprozess noch arbeitet, startet der nächste Takt
    keinen zweiten parallel (``uebersprungen``)."""
    from to_spawn import prozessbaum, waechter_lauf, wecker

    heim = tmp_path / "heim"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    zustand = tmp_path / "wecker-busy"
    sid = f"busy-{uuid.uuid4().hex[:8]}"
    transkript = waechter_lauf.transkript_ordner(repo, heim=heim) / f"{sid}.jsonl"
    transkript.parent.mkdir(parents=True, exist_ok=True)
    transkript.write_text(
        json.dumps({"type": "assistant", "cwd": str(repo), "message": {"content": "Bereit"}}) + "\n",
        encoding="utf-8",
    )
    sessions_json = repo / ".to-spawn" / "sessions" / "leit.json"
    sessions_json.parent.mkdir(parents=True, exist_ok=True)
    sessions_json.write_text(json.dumps({"session_id": sid, "cwd": str(repo)}), encoding="utf-8")

    limit_flag = tmp_path / "limit.flag"
    spur = tmp_path / "spur.txt"
    _fake_claude_einrichten(transkript=transkript, limit_flag=limit_flag, spur=spur, schlafen_s=10.0)

    wecker.auftrag_setzen(zustand, name="leit", ziel=str(sessions_json), text="Weckruf", takt_min=1)
    kasten = wecker.WindowsWecker(heim=heim, zustand=zustand)
    try:
        erg1 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=1000.0, nachpruef_s=0.3)
        assert erg1[0].ereignis == "geweckt", erg1[0]

        # Neuer Kasten (wie im nächsten geplanten Aufgaben-Lauf): erkennt lebenden Läufer aus Datei.
        kasten2 = wecker.WindowsWecker(heim=heim, zustand=zustand)
        erg2 = wecker.durchgang(zustand, werkzeug=kasten2, jetzt=1200.0, nachpruef_s=0.0)
        assert erg2[0].ereignis == "uebersprungen", erg2[0]
        assert len(spur.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        for proc in kasten._prozesse.values():
            if proc.poll() is None:
                prozessbaum.gruppe_beenden(proc)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-CLI-Pfad für geplante Aufgabe")
def test_cli_wecker_einrichten_trocken_unter_windows(tmp_path: Path) -> None:
    """``to_spawn.py wecker einrichten --trocken`` nutzt unter Windows ``WindowsWecker`` (Exit 0)."""
    zustand = tmp_path / "wecker-cli"
    env = {**os.environ, "TO_SPAWN_WECKER_ORDNER": str(zustand), "PYTHONIOENCODING": "utf-8"}
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "wecker", "einrichten", "--trocken", "--takt-min", "5"],
        cwd=str(SKILL),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "[trocken]" in lauf.stdout or "unverändert" in lauf.stdout
    assert "Wecker-Aufgabe" in lauf.stdout
    assert "alle 5 min" in lauf.stdout
