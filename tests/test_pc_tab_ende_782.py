"""#782 (Windows-Teil): Keine toten Tabs ohne Kind-Prozess und keine doppelten Tabs.

Prüft die drei Windows-Teile aus #782:
1. ``pc_fenster.lauf_aus`` (und ``pc_fenster.tab_pid_aus``) erkennt ``spawn_local.ps1``-Tabs
   (``pwsh -NoExit -Command "bau <N>"`` sowie die Exit-0-Form aus #282), ohne fremde
   interaktive ``pwsh``-Tabs oder Tabs mit weiteren Kindern zu treffen.
2. ``spawn.neustart --beenden`` unter Windows beendet über ``pc_fenster.baum_beenden``
   den alleinigen Tab-``pwsh`` (falls vorhanden) statt nur ``claude``/``bau.py`` per
   ``os.kill``; Linux-Verhalten bleibt unverändert.
3. ``spawn.wt_tab_befehl`` schließt den Tab bei Exit 0 von selbst (ohne ``;`` im
   ``-Command``-Text) und hält ihn nur bei Fehler ≠ 0 über ``-NoExit`` offen — inkl.
   echter ``pwsh -NoExit``-Probe auf dem GitHub-Windows-Läufer.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import pc_fenster, prozessbaum, respawn, spawn

_FT = 11644473600.0
T0 = 1_790_000_000.0
PWSH = shutil.which("pwsh")
PY = shutil.which("python") or shutil.which("python3")


def _ft(unix: float) -> int:
    return int((unix + _FT) * 1e7)


def _prozesse(pwsh_cmd: str, ticket: int = 1477, *, mit_claude: bool = True, **extra: tuple[int, str, int]) -> dict[int, tuple[int, str, int]]:
    daten: dict[int, tuple[int, str, int]] = {
        100: (4, r"C:\Program Files\WindowsApps\WindowsTerminal.exe", _ft(T0 - 30)),
        200: (100, pwsh_cmd, _ft(T0 - 20)),
        201: (200, r"C:\windows\system32\conhost.exe 0x4", _ft(T0 - 20)),
        300: (200, rf'"C:\Python313\python.exe" ./scripts/bau.py {ticket}', _ft(T0 - 10)),
    }
    if mit_claude:
        daten[400] = (
            300,
            rf'"C:\Users\x\.local\bin\claude.exe" --settings C:\tmp\x-bau\{ticket}-20261122-204500\settings.json',
            _ft(T0),
        )
    daten.update({int(k.removeprefix("p")): v for k, v in extra.items()})
    return daten


# --- 1. pc_fenster.lauf_aus + tab_pid_aus + abloesen -------------------------


@pytest.mark.parametrize(
    "pwsh_cmd",
    [
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477"',
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477 && & { if ($LASTEXITCODE -eq 0) { exit 0 } }"',
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command bau 1477',
        r""" "C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "python 'C:\repo\scripts\bau.py' 1477 --sofort && & { if ($LASTEXITCODE -eq 0) { exit 0 } }" """.strip(),
    ],
)
def test_lauf_aus_erkennt_spawn_local_und_wt_tab(pwsh_cmd: str) -> None:
    lauf = pc_fenster.lauf_aus(_prozesse(pwsh_cmd), 1477)
    assert lauf.tab_pid == 200
    assert lauf.bau_pid == 300
    assert lauf.session_pid == 400


@pytest.mark.parametrize(
    "pwsh_cmd",
    [
        r'"C:\Program Files\PowerShell\7\pwsh.exe"',
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 14770"',
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "umbau 1477"',
    ],
)
def test_lauf_aus_ignoriert_interaktiven_oder_fremden_pwsh(pwsh_cmd: str) -> None:
    lauf = pc_fenster.lauf_aus(_prozesse(pwsh_cmd), 1477)
    assert lauf.tab_pid is None
    assert lauf.bau_pid == 300
    assert lauf.session_pid == 400


def test_lauf_aus_spawn_local_tab_mit_weiterem_kind_wird_nicht_mitbeendet() -> None:
    fremd = (200, r'"C:\Python313\python.exe" anderes.py', _ft(T0 - 5))
    lauf = pc_fenster.lauf_aus(
        _prozesse(r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477"', p250=fremd),
        1477,
    )
    assert lauf.tab_pid is None
    assert lauf.bau_pid == 300


def test_tab_pid_aus_findet_spawn_local_tab_auch_ohne_claude() -> None:
    """Während ``bau.py`` wartet (noch kein ``claude``), findet ``tab_pid_aus`` trotzdem den Tab-``pwsh``."""
    prozesse = _prozesse(
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477 && & { if ($LASTEXITCODE -eq 0) { exit 0 } }"',
        mit_claude=False,
    )
    assert pc_fenster.tab_pid_aus(prozesse, 1477, 300) == 200


def test_abloesen_mit_echtem_lauf_aus_beendet_spawn_local_tab(tmp_path: Path) -> None:
    """``pc_fenster.abloesen`` beendet bei einem ``spawn_local.ps1``-Tab den Eltern-``pwsh`` (200), nicht nur ``bau.py`` (300)."""
    prozesse = _prozesse(
        r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command "bau 1477 && & { if ($LASTEXITCODE -eq 0) { exit 0 } }"'
    )
    beendet: list[int] = []
    gestartet: list[int] = []

    class _FakePcWerkzeug(pc_fenster.PcWerkzeug):
        def __init__(self) -> None:
            self._abfragen = 0

        def alte_session(self, repo: Path, spec: int, ticket: int) -> pc_fenster.Alte:
            self._abfragen += 1
            if self._abfragen == 1:
                return pc_fenster.Alte(bau_pid=300, session_pid=400)
            return pc_fenster.Alte(bau_pid=500, session_pid=600)

        def lauf(self, ticket: int) -> pc_fenster.Lauf:
            return pc_fenster.lauf_aus(prozesse, ticket)

        def baum_beenden(self, pid: int) -> bool:
            beendet.append(pid)
            return True

        def tab_starten(self, repo: Path, ticket: int, auftrag: str) -> None:
            gestartet.append(ticket)

        def jetzt(self) -> float:
            return T0 + 600.0

        def schlafen(self, s: float) -> None:
            pass

    erg = pc_fenster.abloesen(respawn.Auftrag(spec=1330, ticket=1477, repo=tmp_path, warte_max=30.0), _FakePcWerkzeug())
    assert erg.exit == respawn.EXIT_OK
    assert beendet == [200], f"Tab-pwsh 200 muss beendet werden, nicht nur bau.py 300: {beendet}"
    assert gestartet == [1477]


# --- 2. spawn.neustart --beenden unter Windows -------------------------------


def test_neustart_beenden_windows_beendet_alleinigen_tab_pwsh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``spawn.neustart --beenden`` unter Windows beendet den alleinigen Tab-``pwsh`` per ``baum_beenden``."""
    monkeypatch.setattr(spawn.sys, "platform", "win32")
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))

    aufrufe = {"stand": 0}

    def _eintrag(_spec: int, _ticket: int) -> SimpleNamespace:
        aufrufe["stand"] += 1
        if aufrufe["stand"] == 1:
            return SimpleNamespace(zustand="läuft 5m", pid=300, session_pid=400)
        return SimpleNamespace(zustand="aus", pid=None, session_pid=None)

    beendet: list[int] = []
    gestartet: list[list[str]] = []

    monkeypatch.setattr(spawn, "ticket_eintrag", _eintrag)
    monkeypatch.setattr(pc_fenster, "tab_pid", lambda ticket, bau_pid: 200 if (ticket, bau_pid) == (1477, 300) else None)
    monkeypatch.setattr(pc_fenster, "baum_beenden", lambda pid: (beendet.append(pid), True)[1])
    monkeypatch.setattr(
        spawn.starter,
        "starte",
        lambda befehl, **_kw: (gestartet.append(list(befehl)), SimpleNamespace(returncode=0))[1],
    )
    monkeypatch.setattr(spawn.os, "kill", lambda *_a: pytest.fail("os.kill darf unter Windows nicht genutzt werden"))

    rc = spawn.neustart(tmp_path, 1330, 1477, {}, ziel="local", beenden=True)
    assert rc == 0
    assert beendet == [200], f"Alleiniger Tab-pwsh (200) muss per baum_beenden beendet werden: {beendet}"
    assert len(gestartet) == 1 and gestartet[0][0] == "wt"


def test_neustart_beenden_windows_ohne_alleinigen_tab_beendet_bau_py(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Läuft ``bau.py`` in einem geteilten ``pwsh``-Tab, beendet ``neustart --beenden`` nur ``bau.py`` (Baum)."""
    monkeypatch.setattr(spawn.sys, "platform", "win32")
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))

    aufrufe = {"stand": 0}

    def _eintrag(_spec: int, _ticket: int) -> SimpleNamespace:
        aufrufe["stand"] += 1
        if aufrufe["stand"] == 1:
            return SimpleNamespace(zustand="läuft 5m", pid=300, session_pid=400)
        return SimpleNamespace(zustand="aus", pid=None, session_pid=None)

    beendet: list[int] = []
    monkeypatch.setattr(spawn, "ticket_eintrag", _eintrag)
    monkeypatch.setattr(pc_fenster, "tab_pid", lambda _ticket, _bau_pid: None)
    monkeypatch.setattr(pc_fenster, "baum_beenden", lambda pid: (beendet.append(pid), True)[1])
    monkeypatch.setattr(spawn.starter, "starte", lambda *_a, **_kw: SimpleNamespace(returncode=0))

    rc = spawn.neustart(tmp_path, 1330, 1477, {}, ziel="local", beenden=True)
    assert rc == 0
    assert beendet == [300]


def test_neustart_beenden_linux_bleibt_unveraendert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linux-Verhalten von ``spawn.neustart --beenden`` bleibt unverändert (``os.kill(session_pid, 15)``)."""
    monkeypatch.setattr(spawn.sys, "platform", "linux")
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))

    aufrufe = {"stand": 0}

    def _eintrag(_spec: int, _ticket: int) -> SimpleNamespace:
        aufrufe["stand"] += 1
        if aufrufe["stand"] == 1:
            return SimpleNamespace(zustand="läuft 5m", pid=300, session_pid=400)
        return SimpleNamespace(zustand="aus", pid=None, session_pid=None)

    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(spawn, "ticket_eintrag", _eintrag)
    monkeypatch.setattr(spawn.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(pc_fenster, "baum_beenden", lambda _pid: pytest.fail("baum_beenden ist Windows-only"))
    monkeypatch.setattr(spawn, "_neustart_lokal_befehl", lambda *_a: (["echo", "ok"], False))
    monkeypatch.setattr(spawn.capo, "_starte_folge_runde", lambda *_a: "")

    rc = spawn.neustart(tmp_path, 1330, 1477, {}, ziel="local", beenden=True)
    assert rc == 0
    assert kills == [(400, 15)]


# --- 3. spawn.wt_tab_befehl schließt bei Exit 0, bleibt bei Fehler offen -----


def test_wt_tab_befehl_schliesst_bei_exit_0_ohne_semikolon(tmp_path: Path) -> None:
    """Statisch (Linux + Windows): ``wt_tab_befehl`` schließt bei Exit 0, behält ``-NoExit`` für Fehler, kein ``;``."""
    cmd = spawn.wt_tab_befehl(tmp_path, 1477, "Weiter ab Handoff; los", "--remote-control")
    assert "pwsh" in cmd and "-NoExit" in cmd and "-Command" in cmd
    innen = cmd[cmd.index("-Command") + 1]
    assert ";" not in innen, f"wt trennt Tabs an ';': {innen}"
    assert "$LASTEXITCODE -eq 0" in innen, innen
    assert "exit 0" in innen, innen


def _pwsh_tab_probe(befehl: str, warte_s: float = 5.0) -> tuple[bool, int | None]:
    """Startet ``pwsh -NoProfile -NoExit -Command <befehl>`` mit offenem stdin (= interaktiver Tab)."""
    prozess = subprocess.Popen(
        [str(PWSH), "-NoProfile", "-NoExit", "-Command", befehl],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **prozessbaum.fensterlos(),
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


@pytest.mark.skipif(sys.platform != "win32", reason="echte pwsh-Probe nur unter Windows")
@pytest.mark.skipif(PWSH is None or PY is None, reason="pwsh oder python fehlt")
def test_wt_tab_befehl_pwsh_schliesst_bei_exit_0_und_bleibt_bei_fehler(tmp_path: Path) -> None:
    """Echter ``pwsh -NoExit``-Lauf auf dem GitHub-Windows-Läufer mit dem Befehl aus ``spawn.wt_tab_befehl``."""
    skript = tmp_path / "scripts" / "bau.py"
    skript.parent.mkdir(parents=True)

    # 1) Regulärer Exit 0 → pwsh endet von selbst (Tab schließt).
    skript.write_text("import sys; print('fertig'); sys.exit(0)\n", encoding="utf-8")
    cmd_ok = spawn.wt_tab_befehl(tmp_path, 1477, "")
    innen_ok = cmd_ok[cmd_ok.index("-Command") + 1]
    beendet, rc = _pwsh_tab_probe(innen_ok, warte_s=6.0)
    assert beendet and rc == 0, f"Exit 0 muss Tab schließen (beendet={beendet}, rc={rc}, cmd={innen_ok})"

    # 2) Fehler Exit 2 → pwsh bleibt wegen -NoExit offen (Fehlermeldung lesbar).
    skript.write_text("import sys; print('fehler', file=sys.stderr); sys.exit(2)\n", encoding="utf-8")
    cmd_err = spawn.wt_tab_befehl(tmp_path, 1477, "")
    innen_err = cmd_err[cmd_err.index("-Command") + 1]
    beendet, rc = _pwsh_tab_probe(innen_err, warte_s=2.0)
    assert not beendet, f"Exit 2 muss Tab offen halten (beendet={beendet}, rc={rc}, cmd={innen_err})"
