"""#603: Stehende SSH-Beobachtungen verbinden nach Aussetzer automatisch neu (mit Log-Zeile).

Belegt den Code-Teil von Issue #603 mit simulierten Verbindungsabbrüchen (Exit 255,
Namensauflösung weg, Broken pipe, Zeitüberschreitung) — ohne das echte Netz anzufassen:

1. ``to_spawn.ssh_beobachten``: stehende Verbindung (Warten auf Urteil) und Takt-Schleife
   (Status lesen) verbinden bei SSH-Abbruch automatisch neu statt abzubrechen, je mit
   Log-Zeile; reguläre Fern-Exits (z. B. Exit 1 = Urteil rot) werden nie blind wiederholt.
2. ``to_spawn.warten``: erkennt SSH-Verbindungsabbruch (Exit 255) bei ``ssh``-Befehlen,
   protokolliert das Neuverbinden und startet nach Pause neu statt ``fehlgeschlagen``.
3. ``to_spawn.umzug``: ``_warte_auf_server`` / ``server_laeuft`` übersteht einen
   SSH-Aussetzer mit Warn-Log-Zeile und verbindet im nächsten Takt neu.
4. ``skripte/spec_stand.py``: ``server_head`` verbindet bei SSH-Aussetzer einmal neu
   (mit Log-Zeile) statt sofort leer zurückzugeben.
5. CLI ``to_spawn.py ssh-beobachten`` und Start-Tür (#1406) samt Windows-Attrappe.
"""

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
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import cli_parser, starter, umzug, warten  # noqa: E402
from windows_attrappen import windows_starter  # noqa: E402


def _ssh_attrappe(inhalt: str) -> Path:
    """Legt eine ``ssh``-Attrappe in ``TO_SPAWN_TEST_BIN`` an (unter Windows inkl. ``.exe``)."""
    ordner = Path(os.environ[starter.TEST_BIN_ENV])
    pfad = ordner / "ssh"
    pfad.write_text(f"#!{sys.executable}\n{inhalt}", encoding="utf-8")
    pfad.chmod(0o755)
    windows_starter(pfad)
    return pfad


# --- 1. to_spawn.ssh_beobachten: stehende Verbindung + Takt-Schleife ---------------------


def test_ssh_befehl_setzt_keepalive_und_batchmode_ohne_doppelte() -> None:
    """SSH-Beobachtung setzt Keepalive- und BatchMode-Optionen und doppelt vorhandene nicht."""
    from to_spawn import ssh_beobachten

    cmd = ssh_beobachten.ssh_befehl("bau-server", "sessions 42")
    assert cmd[0] == "ssh"
    assert "BatchMode=yes" in cmd
    assert any(teil.startswith("ConnectTimeout=") for teil in cmd)
    assert any(teil.startswith("ServerAliveInterval=") for teil in cmd)
    assert any(teil.startswith("ServerAliveCountMax=") for teil in cmd)
    assert cmd[-2:] == ["bau-server", "sessions 42"]

    eigen = ssh_beobachten.ssh_befehl(
        "bau-server",
        "sessions 42",
        zusatz_optionen=["-o", "ConnectTimeout=5", "-p", "2222"],
    )
    assert sum(1 for teil in eigen if teil.startswith("ConnectTimeout=")) == 1
    assert "ConnectTimeout=5" in eigen
    assert "-p" in eigen and "2222" in eigen


def test_stehende_beobachtung_verbindet_nach_exit_255_automatisch_neu(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Simulierter Netz-Abbruch (Exit 255: Broken pipe + DNS weg) → Log-Zeile, Pause, Neuverbindung, Exit 0."""
    from to_spawn import ssh_beobachten

    zaehler = tmp_path / "versuch.txt"
    argv_log = tmp_path / "argv.jsonl"
    _ssh_attrappe(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        f"a = Path({str(argv_log)!r})\n"
        "with a.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.write('client_loop: send disconnect: Broken pipe\\n')\n"
        "    sys.exit(255)\n"
        "if n == 2:\n"
        "    sys.stderr.write('ssh: Could not resolve hostname bau-server\\n')\n"
        "    sys.exit(255)\n"
        "print('URTEIL: gruen')\n"
        "sys.exit(0)\n"
    )

    pausen: list[float] = []
    with caplog.at_level("INFO", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "warte_auf_urteil 603",
            pause_s=15.0,
            max_neuverbindungen=5,
            schlafen=pausen.append,
        )

    assert erg.exit_code == 0, erg
    assert "URTEIL: gruen" in erg.stdout
    assert erg.neuverbindungen == 2
    assert len(erg.abbruch_gruende) == 2
    assert "Broken pipe" in erg.abbruch_gruende[0]
    assert "Could not resolve hostname" in erg.abbruch_gruende[1]
    assert pausen == [15.0, 15.0]
    assert "verbinde" in caplog.text.lower()
    assert "wiederhergestellt" in caplog.text.lower()
    aufrufe = [json.loads(z) for z in argv_log.read_text(encoding="utf-8").splitlines()]
    assert len(aufrufe) == 3
    assert all(any(t.startswith("ServerAliveInterval=") for t in a) for a in aufrufe)


def test_stehende_beobachtung_verbindet_nach_timeout_neu(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hängende SSH-Verbindung (TimeoutExpired) → Log-Zeile, Pause und Neuverbindung."""
    from to_spawn import ssh_beobachten

    versuche = 0

    def fake_starte(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        nonlocal versuche
        versuche += 1
        if versuche == 1:
            raise subprocess.TimeoutExpired(cmd, 30)
        return subprocess.CompletedProcess(cmd, 0, stdout="URTEIL: gruen\n", stderr="")

    monkeypatch.setattr(starter, "starte", fake_starte)
    pausen: list[float] = []
    with caplog.at_level("INFO", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "warte_auf_urteil",
            timeout_s=30.0,
            pause_s=10.0,
            max_neuverbindungen=3,
            schlafen=pausen.append,
        )

    assert erg.exit_code == 0
    assert erg.neuverbindungen == 1
    assert pausen == [10.0]
    assert "Zeitüberschreitung" in caplog.text or "keine Antwort" in caplog.text


def test_regulaerer_fern_exit_wird_nicht_wiederholt(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exit 1 (z. B. Urteil rot) ist kein SSH-Aussetzer → sofort zurückgeben, 0 Neuverbindungen."""
    from to_spawn import ssh_beobachten

    zaehler = tmp_path / "versuch.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "print('URTEIL: rot')\n"
        "sys.exit(1)\n"
    )

    pausen: list[float] = []
    with caplog.at_level("WARNING", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "pruefe_urteil",
            pause_s=10.0,
            max_neuverbindungen=3,
            schlafen=pausen.append,
        )

    assert erg.exit_code == 1
    assert "URTEIL: rot" in erg.stdout
    assert erg.neuverbindungen == 0
    assert pausen == []
    assert int(zaehler.read_text(encoding="utf-8")) == 1


def test_max_neuverbindungen_begrenzt_ausfall(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bleibt SSH dauerhaft tot, endet ``beobachten`` nach ``max_neuverbindungen`` mit Exit 255 + Fehler-Log."""
    from to_spawn import ssh_beobachten

    _ssh_attrappe("import sys\nsys.stderr.write('Connection reset by peer\\n')\nsys.exit(255)\n")
    pausen: list[float] = []
    with caplog.at_level("WARNING", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "sessions 603",
            pause_s=5.0,
            max_neuverbindungen=2,
            schlafen=pausen.append,
        )

    assert erg.exit_code == ssh_beobachten.SSH_EXIT_ABBRUCH
    assert erg.neuverbindungen == 2
    assert pausen == [5.0, 5.0]
    assert "Connection reset by peer" in caplog.text


def test_fehlendes_ssh_bricht_sofort_ohne_wiederholung_ab(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fehlt das Programm ``ssh`` selbst (FileNotFoundError), wird nicht in einer Schleife gewartet."""
    from to_spawn import ssh_beobachten

    def fehlt(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("ssh nicht gefunden")

    monkeypatch.setattr(starter, "starte", fehlt)
    pausen: list[float] = []
    with caplog.at_level("ERROR", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "sessions 603",
            pause_s=5.0,
            max_neuverbindungen=3,
            schlafen=pausen.append,
        )

    assert erg.exit_code == ssh_beobachten.SSH_EXIT_ABBRUCH
    assert erg.neuverbindungen == 0
    assert pausen == []
    assert "nicht aufrufbar" in caplog.text.lower()


def test_status_schleife_uebersteht_aussetzer_und_endet_bei_muster(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Takt-Modus (Status lesen / Warten auf Urteil): übersteht Aussetzer in Runde 2 und trifft Muster in Runde 3."""
    from to_spawn import ssh_beobachten

    zaehler = tmp_path / "runde.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    print('STAND: laeuft')\n"
        "    sys.exit(0)\n"
        "if n == 2:\n"
        "    sys.stderr.write('ssh: Could not resolve hostname bau-server\\n')\n"
        "    sys.exit(255)\n"
        "print('STAND: URTEIL gruen')\n"
        "sys.exit(0)\n"
    )

    pausen: list[float] = []
    zeilen: list[str] = []
    with caplog.at_level("INFO", logger="to_spawn.ssh_beobachten"):
        erg = ssh_beobachten.beobachten(
            "bau-server",
            "sessions 603",
            takt_s=60.0,
            pause_s=15.0,
            bis_muster=r"URTEIL\s+gruen",
            schlafen=pausen.append,
            ausgabe_zeile=zeilen.append,
        )

    assert erg.exit_code == 0
    assert erg.neuverbindungen == 1
    assert "URTEIL gruen" in erg.stdout
    assert pausen == [60.0, 15.0]
    assert any("STAND: laeuft" in z for z in zeilen)
    assert any("URTEIL gruen" in z for z in zeilen)
    assert "Could not resolve hostname" in caplog.text


def test_ssh_beobachten_sperrt_echtes_ssh_unter_test() -> None:
    """Start-Tür (#1406): ohne ``ssh``-Attrappe im Attrappen-Ordner → ``StartGesperrt``."""
    from to_spawn import ssh_beobachten

    with pytest.raises(starter.StartGesperrt, match="ssh"):
        ssh_beobachten.beobachten("bau-server", "true", max_neuverbindungen=0)


# --- 2. CLI to_spawn.py ssh-beobachten ---------------------------------------------------


def test_cli_ssh_beobachten_registriert_und_verbindet_neu(tmp_path: Path) -> None:
    """``to_spawn.py ssh-beobachten`` ist im CLI-Parser registriert und übersteht Exit 255 als Subprozess."""
    ap = cli_parser.baue_parser()
    args = ap.parse_args(
        ["ssh-beobachten", "--ziel", "srv-test", "--pause", "0", "--max-neuverbindungen", "3", "--", "sessions", "603"]
    )
    assert args.befehl == "ssh-beobachten"
    assert args.ziel == "srv-test"
    assert args.kommando == ["sessions", "603"]

    zaehler = tmp_path / "cli_versuch.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.write('Broken pipe\\n')\n"
        "    sys.exit(255)\n"
        "print('CLI_OK')\n"
        "sys.exit(0)\n"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "ssh-beobachten",
            "--ziel",
            "bau-server",
            "--pause",
            "0",
            "--max-neuverbindungen",
            "2",
            "--",
            "sessions 603",
        ],
        cwd=repo,
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert "CLI_OK" in lauf.stdout
    assert "Broken pipe" in lauf.stderr or "verbinde" in lauf.stderr.lower()


# --- 3. to_spawn.warten: SSH-Exit 255 löst Neuverbindung aus statt fehlgeschlagen --------


@pytest.mark.parametrize(
    ("code", "plattform", "befehl", "erwartet"),
    [
        (255, "linux", ["ssh", "bau-server", "warte"], True),
        (255, "win32", ["ssh", "bau-server", "warte"], True),
        (255, "win32", ["ssh.exe", "bau-server", "warte"], True),
        (255, "win32", ["C:\\Windows\\System32\\OpenSSH\\ssh.exe", "bau-server", "warte"], True),
        (255, "linux", ["/usr/bin/ssh", "bau-server", "warte"], True),
        (1, "linux", ["ssh", "bau-server", "warte"], False),
        (0, "win32", ["ssh", "bau-server", "warte"], False),
        (255, "linux", ["python", "skript.py"], False),
        (255, "win32", ["python.exe", "skript.py"], False),
    ],
)
def test_warten_erkennt_ssh_abbruch_exit_255(code: int, plattform: str, befehl: list[str], erwartet: bool) -> None:
    """Exit 255 gilt genau bei ``ssh``-Befehlen als Verbindungsabbruch, nie bei anderen Befehlen."""
    assert warten.ist_abbruch_exit(code, plattform=plattform, befehl=befehl) is erwartet


def test_warten_verbindet_bei_ssh_exit_255_automatisch_neu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``warten.ausfuehren`` mit ``ssh``-Befehl: Exit 255 → Log-Zeile, Pause, Neuverbindung, Versuch 2 grün."""
    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    zaehler = tmp_path / "ssh_warten.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.write('client_loop: send disconnect: Broken pipe\\n')\n"
        "    sys.exit(255)\n"
        "print('Urteil vom Server: gruen')\n"
        "sys.exit(0)\n"
    )

    pausen: list[float] = []
    with caplog.at_level("INFO", logger="to_spawn.warten"):
        erg = warten.ausfuehren(
            repo,
            "603",
            ["ssh", "bau-server", "warte_urteil"],
            max_versuche=3,
            pause_s=12.0,
            schlafen=pausen.append,
        )

    assert erg.exit == warten.EXIT_OK, erg.zeile
    assert erg.status == "fertig"
    assert pausen == [12.0]
    assert "Urteil vom Server: gruen" in erg.zeile
    assert "SSH-Verbindung" in caplog.text
    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 1
    assert eintraege[0]["exit_code"] == 255
    assert "SSH" in eintraege[0]["grund"]


# --- 4. to_spawn.umzug: _warte_auf_server übersteht SSH-Aussetzer mit Log-Zeile ----------


def test_umzug_warte_auf_server_loggt_ssh_abbruch_und_verbindet_neu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bricht SSH während ``_warte_auf_server`` ab (Exit 255), folgt eine Warn-Log-Zeile und Neuverbindung."""
    zaehler = tmp_path / "umzug_ssh.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.write('Connection timed out\\n')\n"
        "    sys.exit(255)\n"
        "befehl = sys.argv[-1]\n"
        "if 'tmux list-windows' in befehl:\n"
        "    print('bau 603')\n"
        "    sys.exit(0)\n"
        "print('#603 läuft seit 12:00 pid 10 session 11 Titel')\n"
        "sys.exit(0)\n"
    )

    monkeypatch.setattr(umzug, "BEWEIS_TAKT", 0.0)
    monkeypatch.setattr(umzug, "BEWEIS_MAX", 5.0)
    with caplog.at_level("WARNING", logger="to_spawn.umzug"):
        ok = umzug._warte_auf_server("600", "603", ziel="bau-server", ordner="~/repo")

    assert ok is True
    assert "verbinde" in caplog.text.lower() or "ssh" in caplog.text.lower()
    assert "255" in caplog.text or "abgebrochen" in caplog.text.lower()


# --- 5. skripte/spec_stand.py: server_head verbindet nach SSH-Aussetzer neu --------------


def test_spec_stand_server_head_verbindet_nach_ssh_abbruch_neu(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``spec_stand.server_head`` übersteht einen einmaligen SSH-Aussetzer (Exit 255) mit Log-Zeile."""
    zaehler = tmp_path / "head_ssh.txt"
    _ssh_attrappe(
        "import sys\n"
        "from pathlib import Path\n"
        f"z = Path({str(zaehler)!r})\n"
        "n = int(z.read_text()) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.write('Broken pipe\\n')\n"
        "    sys.exit(255)\n"
        "print('abc1234')\n"
        "sys.exit(0)\n"
    )

    spec = importlib.util.spec_from_file_location("_spec_stand_603", SKILL / "skripte" / "spec_stand.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    with caplog.at_level("WARNING"):
        sha = modul.server_head({"ssh_ziel": "bau-server", "server_repo": "~/repo"}, pause_s=0.0)

    assert sha == "abc1234"
    assert "verbinde" in caplog.text.lower()
