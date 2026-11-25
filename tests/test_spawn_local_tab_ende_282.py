"""#282 Teil C: Windows-Terminal-Tab aus ``spawn_local.ps1`` schließt bei Exit 0 automatisch.

Bei Exit 0 (z. B. „nichts zu tun“ aus ``bau.py`` / ``startstelle.betreten``) beendet sich
``pwsh`` trotz ``-NoExit``, sodass Windows Terminal (``wt``) den Tab schließt statt ihn als
Leiche stehen zu lassen. Bei Exit ≠ 0 oder PowerShell-Fehler bleibt der Tab offen, damit die
Fehlermeldung lesbar bleibt. Der ``-Command``-Text darf kein ``;`` enthalten (``wt`` trennt
Tabs an ``;``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPT = Path(os.environ.get("SPAWN_LOCAL_SKRIPT") or SKILL / "spawn_local.ps1")
PWSH = shutil.which("pwsh")
PS51 = shutil.which("powershell")
PY = shutil.which("python") or shutil.which("python3")

SPEC = 9282
TICKET_A = 9283
TICKET_B = 9284

ATTRAPPE = f"""
import os, sys
from pathlib import Path


class config:
    @staticmethod
    def lade(repo):
        return repo

    @staticmethod
    def pfad(repo, name):
        return Path(repo) / "docs" / "agents" / "manifests"


if __name__ == "__main__":
    if sys.argv[1] == "zu-bauen":
        ausgabe = os.environ.get("ATTRAPPE_ZU_BAUEN_AUSGABE", "{TICKET_A}")
        if ausgabe:
            print(ausgabe)
    sys.exit(0)
"""


def _popen_flags() -> dict[str, int]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _trockenlauf_wt_zeile(
    tmp_path: Path,
    *,
    tickets: tuple[int, ...] = (TICKET_A,),
    ps_exe: str | None = None,
) -> str:
    """Führt ``spawn_local.ps1 -DryRun`` echt aus und liefert die ausgegebene ``wt …``-Zeile."""
    skill = tmp_path / "skill"
    skill.mkdir(exist_ok=True)
    shutil.copy(SKRIPT, skill / "spawn_local.ps1")
    (skill / "to_spawn.py").write_text(ATTRAPPE, encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "bau.py").write_text("", encoding="utf-8")
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True, exist_ok=True)
    (manifeste / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "feature": "Probe", "tickets": {str(t): {"title": f"T{t}"} for t in tickets}}),
        encoding="utf-8",
    )

    attrappen_bin = tmp_path / "bin"
    attrappen_bin.mkdir(exist_ok=True)
    wt_marke = tmp_path / "wt_aufgerufen.txt"
    (attrappen_bin / "wt.cmd").write_text(f'@echo %* > "{wt_marke}"\r\n', encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = str(attrappen_bin) + os.pathsep + env.get("PATH", "")
    env["ATTRAPPE_ZU_BAUEN_AUSGABE"] = "\n".join(str(t) for t in tickets)
    env["PYTHONIOENCODING"] = "utf-8"

    lauf = subprocess.run(
        [
            str(ps_exe or PWSH),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(skill / "spawn_local.ps1"),
            "-Spec",
            str(SPEC),
            "-OhneWache",
            "-DryRun",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        **_popen_flags(),
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert not wt_marke.exists(), "Trockenlauf darf kein Fenster öffnen"
    wt_zeilen = [z.strip() for z in lauf.stdout.splitlines() if z.strip().startswith("wt ")]
    assert len(wt_zeilen) == 1, ausgabe
    return wt_zeilen[0]


def _command_werte(wt_zeile: str) -> list[str]:
    return re.findall(r'-Command "([^"]+)"', wt_zeile)


def _pwsh_tab_probe(befehl: str, *, warte_s: float = 2.5) -> tuple[bool, int | None]:
    """Startet ``pwsh -NoProfile -NoExit -Command <befehl>`` mit offenem stdin (= interaktiver Tab).

    Liefert ``(beendet, returncode)``. Bleibt der Prozess offen, wird er sauber beendet.
    """
    prozess = subprocess.Popen(
        [str(PWSH), "-NoProfile", "-NoExit", "-Command", befehl],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_popen_flags(),
    )
    try:
        prozess.wait(timeout=warte_s)
        return True, prozess.returncode
    except subprocess.TimeoutExpired:
        prozess.kill()
        prozess.wait(timeout=10)
        return False, None
    finally:
        for strom in (prozess.stdin, prozess.stdout, prozess.stderr):
            if strom is not None:
                strom.close()


def test_spawn_local_tab_befehl_schliesst_bei_exit_0_ohne_semikolon() -> None:
    """Statische Prüfung (auch unter Linux): Exit 0 schließt, Fehler bleibt offen, kein ``;`` im Tab-Befehl."""
    text = SKRIPT.read_text(encoding="utf-8-sig")
    treffer = re.findall(r'\$parts \+= @\("new-tab".*?"-Command",\s*"`"(.*?)`""\)', text)
    assert len(treffer) == 1, "new-tab-Zeile mit -Command in spawn_local.ps1 nicht gefunden"
    cmd_literal = treffer[0]
    assert ";" not in cmd_literal, f"wt trennt Tabs an ';': {cmd_literal}"
    assert "`$LASTEXITCODE -eq 0" in cmd_literal, cmd_literal
    assert "exit 0" in cmd_literal, cmd_literal
    assert '"-NoExit"' in text, "-NoExit bleibt erhalten, damit Fehler im Tab lesbar bleiben"


@pytest.mark.skipif(sys.platform != "win32", reason="spawn_local.ps1-Lauf nur unter Windows")
@pytest.mark.skipif(PWSH is None or PY is None, reason="pwsh oder python fehlt")
def test_trockenlauf_mehrere_tabs_nur_ein_semikolon_als_tab_trenner(tmp_path: Path) -> None:
    wt_zeile = _trockenlauf_wt_zeile(tmp_path, tickets=(TICKET_A, TICKET_B))
    assert wt_zeile.count(";") == 1, f"Genau ein Tab-Trenner ';' bei 2 Tabs erwartet: {wt_zeile}"
    befehle = _command_werte(wt_zeile)
    assert len(befehle) == 2, wt_zeile
    for nr, befehl in zip((TICKET_A, TICKET_B), befehle, strict=True):
        assert befehl.startswith(f"bau {nr} "), befehl
        assert ";" not in befehl, befehl
        assert "$LASTEXITCODE -eq 0" in befehl, befehl
        assert "exit 0" in befehl, befehl


@pytest.mark.skipif(sys.platform != "win32", reason="spawn_local.ps1-Lauf nur unter Windows")
@pytest.mark.skipif(PS51 is None or PY is None, reason="Windows PowerShell 5.1 oder python fehlt")
def test_trockenlauf_ps51_erhaelt_lastexitcode_literal(tmp_path: Path) -> None:
    wt_zeile = _trockenlauf_wt_zeile(tmp_path, tickets=(TICKET_A,), ps_exe=PS51)
    befehle = _command_werte(wt_zeile)
    assert len(befehle) == 1, wt_zeile
    assert "$LASTEXITCODE -eq 0" in befehle[0], befehle[0]
    assert ";" not in befehle[0], befehle[0]


@pytest.mark.skipif(sys.platform != "win32", reason="spawn_local.ps1-Lauf nur unter Windows")
@pytest.mark.skipif(PWSH is None or PY is None, reason="pwsh oder python fehlt")
def test_pwsh_tab_schliesst_bei_exit_0_und_bleibt_bei_fehler(tmp_path: Path) -> None:
    """Echter ``pwsh -NoExit``-Lauf mit dem erzeugten Tab-Befehl aus ``spawn_local.ps1``."""
    wt_zeile = _trockenlauf_wt_zeile(tmp_path, tickets=(TICKET_A,))
    befehle = _command_werte(wt_zeile)
    assert len(befehle) == 1, wt_zeile
    tab_befehl = befehle[0]

    py_pfad = str(PY).replace("'", "''")
    # 1) Exit 0 („nichts zu tun“) → pwsh endet von selbst mit 0 (Tab schließt).
    fn_ok = f"function bau {{ & '{py_pfad}' -c \"import sys; print('bau {TICKET_A}: nichts zu tun'); sys.exit(0)\" }}"
    beendet, rc = _pwsh_tab_probe(f"{fn_ok}\n{tab_befehl}", warte_s=6.0)
    assert beendet and rc == 0, f"Exit 0 muss Tab schließen (beendet={beendet}, rc={rc}, cmd={tab_befehl})"

    # 2) Exit 0 mit stderr-Ausgabe → schließt ebenfalls sauber.
    fn_stderr = f"function bau {{ & '{py_pfad}' -c \"import sys; sys.stderr.write('info\\n'); sys.exit(0)\" }}"
    beendet, rc = _pwsh_tab_probe(f"{fn_stderr}\n{tab_befehl}", warte_s=6.0)
    assert beendet and rc == 0, f"Exit 0 mit stderr muss Tab schließen (beendet={beendet}, rc={rc})"

    # 3) Exit 2 (z. B. gh-Fehler fail-closed, mit `return` wie im Profil aus install.ps1) → Tab bleibt offen.
    fn_err2 = f"function bau {{ & '{py_pfad}' -c 'import sys; sys.exit(2)'; return }}"
    beendet, rc = _pwsh_tab_probe(f"{fn_err2}\n{tab_befehl}", warte_s=2.0)
    assert not beendet, f"Exit 2 muss Tab offen halten (beendet={beendet}, rc={rc})"

    # 4) Fehlende Profil-Funktion (PowerShell-Fehler) → Tab bleibt offen.
    beendet, rc = _pwsh_tab_probe(tab_befehl, warte_s=2.0)
    assert not beendet, f"PowerShell-Fehler muss Tab offen halten (beendet={beendet}, rc={rc})"
