"""Ort-Wahl für große Testläufe (#760, Teil 1/2, Beschluss E33).

Deckt alle Akzeptanzpunkte ab:
- Aufgezeichnete ruhige Messreihe (30 min, optional +30 min Verlängerung) ergibt ``server``.
- Ein einziger knapper Messpunkt (Speicher, Last, Bau-Sessions oder unlesbar) ergibt sofort ``github``.
- Kippt die Lage während eines Laufs auf dem Server, wird der Server-Lauf abgebrochen und an GitHub übergeben.
- Grenzwerte leben an genau einer Stelle (``config.DEFAULTS["ort_wahl"]``); keine Mail, nur Log.
- Läuft nur auf Linux; unter Windows meldet die Ort-Wahl sofort ``github`` mit Grund.
- Linux-Messung nutzt ``to_spawn.speicher`` (``frei_mib``, ``last_1m``, ``laufende_sessions``).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, ort_wahl, prozessbaum, speicher, starter


def _test_skript(name: str, inhalt: str) -> Path:
    """Legt ein Test-Skript im Attrappen-Ordner ``TO_SPAWN_TEST_BIN`` ab (#1406)."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / name
    skript.write_text(inhalt, encoding="utf-8")
    return skript


def _ruhig(zeit_s: float | None = None) -> ort_wahl.Messpunkt:
    return ort_wahl.Messpunkt(frei_mib=8192, last_1m=0.35, bau_sessions=0, zeit_s=zeit_s)


def _ruhige_reihe(minuten: int = 30, takt_s: int = 60) -> list[ort_wahl.Messpunkt]:
    anzahl = (minuten * 60) // takt_s + 1
    return [_ruhig(zeit_s=float(i * takt_s)) for i in range(anzahl)]


# --- 1. Aufgezeichnete Messreihen vor dem Start ---------------------------------


def test_ruhige_messreihe_30_min_ergibt_server(caplog: pytest.LogCaptureFixture) -> None:
    """Eine durchgehend ruhige 30-Minuten-Messreihe ergibt ``server`` und schreibt Log."""
    reihe = _ruhige_reihe(30, 60)
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        ergebnis = ort_wahl.waehlen(config.DEFAULTS, messreihe=reihe, plattform="linux")
    assert ergebnis.ort == ort_wahl.ORT_SERVER
    assert ergebnis.verlaengert is False
    assert len(ergebnis.messungen) == len(reihe)
    assert "ruhig" in ergebnis.grund.lower()
    assert any("server" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.parametrize(
    ("knapp", "erwartetes_wort"),
    [
        (ort_wahl.Messpunkt(frei_mib=2048, last_1m=0.3, bau_sessions=0), "speicher"),
        (ort_wahl.Messpunkt(frei_mib=8192, last_1m=3.5, bau_sessions=0), "last"),
        (ort_wahl.Messpunkt(frei_mib=8192, last_1m=0.3, bau_sessions=1), "bau-session"),
        (ort_wahl.Messpunkt(frei_mib=None, last_1m=0.3, bau_sessions=0), "nicht lesbar"),
        (ort_wahl.Messpunkt(frei_mib=8192, last_1m=None, bau_sessions=0), "nicht lesbar"),
        (ort_wahl.Messpunkt(frei_mib=8192, last_1m=0.3, bau_sessions=None), "nicht lesbar"),
    ],
)
def test_einzelner_knapper_messpunkt_ergibt_sofort_github(
    knapp: ort_wahl.Messpunkt,
    erwartetes_wort: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ein einziger knapper Messpunkt mitten in einer ruhigen Reihe ergibt sofort ``github``."""
    reihe = _ruhige_reihe(30, 60)
    reihe[10] = ort_wahl.Messpunkt(
        frei_mib=knapp.frei_mib,
        last_1m=knapp.last_1m,
        bau_sessions=knapp.bau_sessions,
        zeit_s=reihe[10].zeit_s,
    )
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        ergebnis = ort_wahl.waehlen(config.DEFAULTS, messreihe=reihe, plattform="linux")
    assert ergebnis.ort == ort_wahl.ORT_GITHUB
    # Sofortiger Abbruch am 11. Messpunkt (Index 10), nicht erst am Ende der 30 Minuten.
    assert len(ergebnis.messungen) == 11
    assert erwartetes_wort in ergebnis.grund.lower()
    assert any("github" in r.getMessage().lower() for r in caplog.records)


def test_verlaengerung_um_weitere_30_minuten_bei_bedarf() -> None:
    """Bei Bedarf wird über weitere 30 Minuten gemessen (gesamt 60 min)."""
    reihe_60 = _ruhige_reihe(60, 60)
    ergebnis_ruhig = ort_wahl.waehlen(
        config.DEFAULTS,
        messreihe=reihe_60,
        verlaengern=True,
        plattform="linux",
    )
    assert ergebnis_ruhig.ort == ort_wahl.ORT_SERVER
    assert ergebnis_ruhig.verlaengert is True
    assert len(ergebnis_ruhig.messungen) == len(reihe_60)

    # Kippt es erst in der Verlängerung (Minute 45), ergibt das sofort github.
    reihe_kippt = list(reihe_60)
    reihe_kippt[45] = ort_wahl.Messpunkt(frei_mib=8192, last_1m=0.4, bau_sessions=1, zeit_s=45 * 60.0)
    ergebnis_kippt = ort_wahl.waehlen(
        config.DEFAULTS,
        messreihe=reihe_kippt,
        verlaengern=True,
        plattform="linux",
    )
    assert ergebnis_kippt.ort == ort_wahl.ORT_GITHUB
    assert ergebnis_kippt.verlaengert is True
    assert len(ergebnis_kippt.messungen) == 46


def test_unvollstaendige_oder_leere_messreihe_fail_closed_github() -> None:
    """Zu kurze oder leere Messreihe darf nie still ``server`` ergeben."""
    leer = ort_wahl.waehlen(config.DEFAULTS, messreihe=[], plattform="linux")
    assert leer.ort == ort_wahl.ORT_GITHUB
    assert "leer" in leer.grund.lower()

    kurz = ort_wahl.waehlen(config.DEFAULTS, messreihe=_ruhige_reihe(10, 60), plattform="linux")
    assert kurz.ort == ort_wahl.ORT_GITHUB
    assert "unvollständig" in kurz.grund.lower()


def test_live_schleife_nutzt_uhr_und_bricht_bei_knappem_punkt_sofort_ab() -> None:
    """Live-Messung schläft nur so lange wie nötig und bricht beim ersten knappen Punkt ab."""
    zeit = [0.0]
    pausen: list[float] = []

    def uhr() -> float:
        return zeit[0]

    def schlafen(s: float) -> None:
        pausen.append(s)
        zeit[0] += s

    punkte = iter([_ruhig(), _ruhig(), ort_wahl.Messpunkt(frei_mib=1024, last_1m=0.2, bau_sessions=0)])
    ergebnis = ort_wahl.waehlen(
        config.DEFAULTS,
        messen=lambda: next(punkte),
        schlafen=schlafen,
        uhr=uhr,
        plattform="linux",
    )
    assert ergebnis.ort == ort_wahl.ORT_GITHUB
    assert len(ergebnis.messungen) == 3
    assert pausen == [60.0, 60.0]


# --- 2. Kippen während des Laufs: Abbruch + Übergabe an GitHub ------------------


def test_kippt_waehrend_server_lauf_bricht_ab_und_wechselt_zu_github(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kippt die Lage während des Server-Laufs, bricht der Lauf ab und GitHub übernimmt."""
    server_marker = tmp_path / "server_fertig.txt"
    github_marker = tmp_path / "github_lief.txt"

    # Server-Befehl würde lange schlafen und erst am Ende eine Datei schreiben.
    server_skript = _test_skript(
        "server_langsam.py",
        f"import pathlib, time\ntime.sleep(60)\npathlib.Path({str(server_marker)!r}).write_text('fertig')\n",
    )
    github_skript = _test_skript(
        "github_uebernahme.py",
        f"import pathlib\npathlib.Path({str(github_marker)!r}).write_text('uebernommen')\n",
    )
    server_befehl = [sys.executable, str(server_skript)]
    github_befehl = [sys.executable, str(github_skript)]

    vorlauf = _ruhige_reihe(30, 60)
    lauf_reihe = [
        _ruhig(0.0),
        _ruhig(30.0),
        ort_wahl.Messpunkt(frei_mib=8192, last_1m=4.8, bau_sessions=0, zeit_s=60.0),
    ]

    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        ergebnis = ort_wahl.ausfuehren(
            server_befehl,
            github_befehl,
            config.DEFAULTS,
            messreihe=vorlauf,
            lauf_messreihe=lauf_reihe,
            schlafen=lambda _: None,
            plattform="linux",
            abbruch_frist_s=1.0,
        )

    assert ergebnis.wahl.ort == ort_wahl.ORT_SERVER
    assert ergebnis.ort == ort_wahl.ORT_GITHUB
    assert ergebnis.abgebrochen is True
    assert ergebnis.uebergeben is True
    assert ergebnis.exit_code == 0
    assert not server_marker.exists(), "Abgebrochener Server-Lauf darf nicht zu Ende gelaufen sein"
    assert github_marker.read_text(encoding="utf-8") == "uebernommen"
    assert len(ergebnis.lauf_messungen) == 3
    assert any("gekippt" in r.getMessage().lower() and "github" in r.getMessage().lower() for r in caplog.records)


def test_ruhiger_server_lauf_laeuft_ohne_github_durch(tmp_path: Path) -> None:
    """Bleibt die Lage während des Server-Laufs ruhig, läuft der Server-Lauf fertig, GitHub nicht."""
    server_marker = tmp_path / "server_ok.txt"
    github_marker = tmp_path / "github_darf_nicht.txt"

    server_skript = _test_skript(
        "server_ruhig.py",
        f"import pathlib\npathlib.Path({str(server_marker)!r}).write_text('server-ok')\n",
    )
    github_skript = _test_skript(
        "github_nicht.py",
        f"import pathlib\npathlib.Path({str(github_marker)!r}).write_text('falsch')\n",
    )
    server_befehl = [sys.executable, str(server_skript)]
    github_befehl = [sys.executable, str(github_skript)]

    ergebnis = ort_wahl.ausfuehren(
        server_befehl,
        github_befehl,
        config.DEFAULTS,
        messreihe=_ruhige_reihe(30, 60),
        lauf_messreihe=[_ruhig(0.0), _ruhig(30.0)],
        schlafen=lambda _: None,
        plattform="linux",
    )

    assert ergebnis.ort == ort_wahl.ORT_SERVER
    assert ergebnis.abgebrochen is False
    assert ergebnis.uebergeben is False
    assert ergebnis.exit_code == 0
    assert server_marker.read_text(encoding="utf-8") == "server-ok"
    assert not github_marker.exists()


# --- 3. Plattform-Regel: Windows meldet sofort github mit Grund -----------------


def test_windows_meldet_sofort_github_mit_grund(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unter Windows meldet die Ort-Wahl sofort ``github`` mit Grund — ohne Messung oder Schlaf."""
    monkeypatch.setattr(ort_wahl.sys, "platform", "win32")
    gemessen = False
    geschlafen = False

    def darf_nicht_messen() -> ort_wahl.Messpunkt:
        nonlocal gemessen
        gemessen = True
        return _ruhig()

    def darf_nicht_schlafen(_: float) -> None:
        nonlocal geschlafen
        geschlafen = True

    ergebnis = ort_wahl.waehlen(
        config.DEFAULTS,
        messen=darf_nicht_messen,
        schlafen=darf_nicht_schlafen,
    )
    assert ergebnis.ort == ort_wahl.ORT_GITHUB
    assert "linux" in ergebnis.grund.lower() or "win32" in ergebnis.grund.lower()
    assert ergebnis.grund.strip() != ""
    assert gemessen is False
    assert geschlafen is False


# --- 4. Grenzwerte nur an einer Stelle, keine Mail, nur Log ---------------------


def test_grenzwerte_nur_in_config_defaults_und_ueberschreibbar(tmp_path: Path) -> None:
    """Alle Grenzwerte stammen aus ``config.DEFAULTS['ort_wahl']`` und lassen sich je Repo überschreiben."""
    assert "ort_wahl" in config.DEFAULTS
    vorgabe = config.DEFAULTS["ort_wahl"]
    assert ort_wahl.VORGABE == vorgabe
    assert vorgabe["fenster_min"] == 30
    assert vorgabe["verlaengerung_min"] == 30

    g = ort_wahl.grenzen(None)
    assert g.fenster_min == vorgabe["fenster_min"]
    assert g.verlaengerung_min == vorgabe["verlaengerung_min"]
    assert g.min_frei_mib == vorgabe["min_frei_mib"]
    assert g.max_last == vorgabe["max_last"]
    assert g.max_bau_sessions == vorgabe["max_bau_sessions"]

    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"ort_wahl": {"min_frei_mib": 6144, "max_last": 1.25, "max_bau_sessions": 1}}),
        encoding="utf-8",
    )
    g_repo = ort_wahl.grenzen(config.lade(repo))
    assert g_repo.min_frei_mib == 6144
    assert g_repo.max_last == 1.25
    assert g_repo.max_bau_sessions == 1
    assert g_repo.fenster_min == vorgabe["fenster_min"]


def test_keine_mail_nur_log() -> None:
    """``to_spawn/ort_wahl.py`` importiert kein Mail-/Melder-Modul und ruft keinen Mailversand auf."""
    quelle = (SKILL / "to_spawn" / "ort_wahl.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    importierte: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            importierte.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.module:
                importierte.add(knoten.module)
            importierte.update(a.name for a in knoten.names)
    verboten = {"smtplib", "melder", "email"}
    assert not (importierte & verboten), f"Verbotene Mail-Importe in ort_wahl.py: {importierte & verboten}"
    assert "logging" in importierte


# --- 5. Linux-Messung über to_spawn.speicher ------------------------------------


def test_speicher_last_1m_und_ort_wahl_messen_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``speicher.last_1m`` liest ``/proc/loadavg`` und ``ort_wahl.messen_linux`` bündelt alle drei Werte."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemAvailable:    6291456 kB\n", encoding="utf-8")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.42 0.38 0.31 1/312 12345\n", encoding="utf-8")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "501").mkdir()
    (proc / "501" / "cmdline").write_bytes(b"/usr/local/bin/claude\0--session-id\0abc\0")

    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_LOADAVG", str(loadavg))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(tmp_path / "sessions"))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))

    assert speicher.last_1m() == pytest.approx(0.42)
    assert speicher.last_1m(tmp_path / "fehlt") is None
    punkt = ort_wahl.messen_linux()
    assert punkt.frei_mib == 6144
    assert punkt.last_1m == pytest.approx(0.42)
    assert punkt.bau_sessions == 1


# --- 6. Schmale CLI-Schnittstelle: ein Befehl, ein Ergebnis ---------------------


def test_cli_ort_wahl_mit_aufgezeichneter_messreihe(tmp_path: Path) -> None:
    """``to_spawn.py ort-wahl --messreihe …`` liefert ein klares Ergebnis (``server`` / ``github``)."""
    ruhig_datei = tmp_path / "ruhig.json"
    ruhig_datei.write_text(
        json.dumps([p.als_dict() for p in _ruhige_reihe(30, 60)]),
        encoding="utf-8",
    )
    knapp_reihe = _ruhige_reihe(30, 60)
    knapp_reihe[3] = ort_wahl.Messpunkt(frei_mib=1024, last_1m=0.2, bau_sessions=0, zeit_s=180.0)
    knapp_datei = tmp_path / "knapp.jsonl"
    knapp_datei.write_text(
        "\n".join(json.dumps(p.als_dict()) for p in knapp_reihe) + "\n",
        encoding="utf-8",
    )

    def aufruf(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), "ort-wahl", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )

    r_ruhig = aufruf("--messreihe", str(ruhig_datei))
    assert r_ruhig.returncode == 0, r_ruhig.stdout + r_ruhig.stderr
    erwartet_ruhig = "server" if sys.platform == "linux" else "github"
    assert r_ruhig.stdout.strip() == erwartet_ruhig

    r_knapp = aufruf("--messreihe", str(knapp_datei), "--json")
    assert r_knapp.returncode == 0, r_knapp.stdout + r_knapp.stderr
    daten: dict[str, Any] = json.loads(r_knapp.stdout)
    assert daten["ort"] == "github"
    assert daten["grund"]

    # Nur ein Befehl ohne Gegenstück → Aufruffehler (Exit 2).
    r_halb = aufruf("--server-befehl", "echo ok")
    assert r_halb.returncode == ort_wahl.EXIT_AUFRUF


def test_ausfuehren_direkt_github_bei_knappem_vorlauf_oder_windows(tmp_path: Path) -> None:
    """Ergibt schon die Vorab-Wahl ``github`` (knapp oder Windows), läuft der Server-Befehl nie."""
    server_marker = tmp_path / "server_darf_nicht.txt"
    github_marker = tmp_path / "github_direkt.txt"
    server_skript = _test_skript(
        "server_darf_nicht.py",
        f"import pathlib\npathlib.Path({str(server_marker)!r}).write_text('falsch')\n",
    )
    github_skript = _test_skript(
        "github_direkt.py",
        f"import pathlib\npathlib.Path({str(github_marker)!r}).write_text('direkt')\n",
    )
    server_cmd = [sys.executable, str(server_skript)]
    github_cmd = [sys.executable, str(github_skript)]

    erg = ort_wahl.ausfuehren(
        server_cmd,
        github_cmd,
        config.DEFAULTS,
        plattform="win32",
    )
    assert erg.ort == ort_wahl.ORT_GITHUB
    assert erg.abgebrochen is False
    assert erg.uebergeben is False
    assert erg.exit_code == 0
    assert not server_marker.exists()
    assert github_marker.read_text(encoding="utf-8") == "direkt"


def test_verlaengerung_ueber_bedarf_rueckruf_und_reihe_ohne_zeitstempel() -> None:
    """``braucht_verlaengerung`` löst die +30-min-Verlängerung aus; Reihen ohne ``zeit_s`` nutzen ``takt_s``."""
    aufrufe = 0

    def bedarf(punkte: tuple[ort_wahl.Messpunkt, ...], schwellen: ort_wahl.Grenzen) -> bool:
        nonlocal aufrufe
        aufrufe += 1
        return len(punkte) > 0 and schwellen.verlaengerung_min > 0

    reihe = [_ruhig(zeit_s=None) for _ in range(61)]
    erg = ort_wahl.waehlen(
        config.DEFAULTS,
        messreihe=reihe,
        braucht_verlaengerung=bedarf,
        plattform="linux",
    )
    assert aufrufe == 1
    assert erg.ort == ort_wahl.ORT_SERVER
    assert erg.verlaengert is True
    assert len(erg.messungen) == 61


def test_cli_ort_wahl_ueberwachter_lauf_kippt_zu_github(tmp_path: Path) -> None:
    """CLI-End-to-End: ``ort-wahl`` mit ``--server-befehl``/``--github-befehl`` und kippender Lauf-Messreihe."""
    if sys.platform != "linux":
        pytest.skip("CLI-End-to-End des Server-Laufs gilt nur unter Linux")
    vorlauf_datei = tmp_path / "vorlauf.json"
    vorlauf_datei.write_text(
        json.dumps({"messreihe": [p.als_dict() for p in _ruhige_reihe(30, 60)]}),
        encoding="utf-8",
    )
    lauf_datei = tmp_path / "lauf.jsonl"
    lauf_datei.write_text(
        json.dumps(ort_wahl.Messpunkt(frei_mib=512, last_1m=0.5, bau_sessions=0, zeit_s=0.0).als_dict()) + "\n",
        encoding="utf-8",
    )
    github_marker = tmp_path / "gh_cli.txt"
    server_skript = _test_skript("server_cli_langsam.py", "import time\ntime.sleep(30)\n")
    gh_skript = _test_skript(
        "gh_cli_lauf.py",
        f"import pathlib\npathlib.Path({str(github_marker)!r}).write_text('ok')\n",
    )
    server_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(server_skript))}"
    github_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(gh_skript))}"

    r = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "ort-wahl",
            "--messreihe",
            str(vorlauf_datei),
            "--lauf-messreihe",
            str(lauf_datei),
            "--server-befehl",
            server_cmd,
            "--github-befehl",
            github_cmd,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.fensterlos(),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    daten: dict[str, Any] = json.loads(r.stdout)
    assert daten["ort"] == "github"
    assert daten["abgebrochen"] is True
    assert daten["uebergeben"] is True
    assert github_marker.read_text(encoding="utf-8") == "ok"


def test_ausfuehren_nutzt_start_tuer_und_sperrt_fremden_befehl() -> None:
    """Server- und GitHub-Starts laufen durch ``to_spawn.starter.starte`` und sperren Fremdprogramme im Test."""
    with pytest.raises(starter.StartGesperrt):
        ort_wahl.ausfuehren(
            [sys.executable, "-c", "pass"],
            [sys.executable, "-c", "pass"],
            config.DEFAULTS,
            plattform="win32",
        )
    with pytest.raises(starter.StartGesperrt):
        ort_wahl.ausfuehren(
            [sys.executable, "-c", "pass"],
            [sys.executable, "-c", "pass"],
            config.DEFAULTS,
            messreihe=_ruhige_reihe(30, 60),
            plattform="linux",
        )
