"""#501 Fälle 2+4: respawn und Leiter am PC (Windows-Terminal-Tab statt tmux).

Geprüft wird ``to_spawn/pc_fenster.py`` an seinen Türen:

* ``lauf_aus`` mit aufgezeichneten Prozesslisten (Form wie ``_prozesse_windows``:
  PID → Eltern-PID, Kommandozeile, FILETIME-Start) — reine Rechenlogik.
* ``baum_beenden`` gegen einen echten Wegwerf-Prozessbaum (Eltern + Schläfer-Kind).
* Tab-Start über die eine Start-Funktion; nur der externe Dienst ``wt.exe`` wird
  aufgezeichnet, der Befehl muss der aus ``spawn.wt_tab_befehl`` sein.
* Weiche: tmux antwortet → tmux-Werkzeug, sonst PC-Werkzeug.
* Tote Session → ``still_s`` None → „kein Fenster“ (echte Prozessliste).
* Handoff zählt nur, wenn er nach dem Start der alten Session geschrieben wurde.
* Leiter am PC: Ablösung erst nach ``PC_ABLOESE_MIN`` Stille, an der Kontext-Grenze sofort.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from to_spawn import aufseher_stand, leiter, pc_fenster, prozessbaum, respawn, spawn  # noqa: E402

WINDOWS = sys.platform == "win32"
_FT = 11644473600.0  # Sekunden zwischen 1601 und 1970


def _ft(unix: float) -> int:
    """Unix-Sekunden → FILETIME (100 ns seit 1601), wie WMI sie liefert."""
    return int((unix + _FT) * 1e7)


T0 = 1_790_000_000.0  # Startzeit der aufgezeichneten Bau-Session

#: Aufgezeichnet nach dem Muster eines echten ``bau 501``-Tabs am PC (05.10.2026):
#: WindowsTerminal → pwsh (Tab) → python bau.py → claude.exe → MCP-Server (startet mit).
_PWSH = (
    r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoExit -Command python '
    r"'C:\dev\repo\scripts\bau.py' 501 --sofort --remote-control"
)
_BAU = r'"C:\Python313\python.exe" C:\dev\repo\scripts\bau.py 501 --sofort --remote-control'
_CLAUDE = r'"C:\Users\x\.local\bin\claude.exe" --remote-control'
_MCP = r'"C:\Program Files\nodejs\node.exe" C:\Users\x\mcp\server.js'


def _liste(**extra: tuple[int, str, int]) -> dict[int, tuple[int, str, int]]:
    basis = {
        100: (4, r"C:\Program Files\WindowsApps\WindowsTerminal.exe", _ft(T0 - 30)),
        200: (100, _PWSH, _ft(T0 - 20)),
        201: (200, r"C:\windows\system32\conhost.exe 0x4", _ft(T0 - 20)),
        300: (200, _BAU, _ft(T0 - 10)),
        400: (300, _CLAUDE, _ft(T0)),
        410: (400, _MCP, _ft(T0 + 2)),
    }
    basis.update({int(k.removeprefix("p")): v for k, v in extra.items()})
    return basis


# ---------------------------------------------------------------- lauf_aus


def test_lauf_aus_findet_tab_bau_session_und_start() -> None:
    lauf = pc_fenster.lauf_aus(_liste(), 501)
    assert lauf.tab_pid == 200
    assert lauf.bau_pid == 300
    assert lauf.session_pid == 400
    assert lauf.start == pytest.approx(T0, abs=1e-3)
    assert lauf.werkzeug_arbeitet is False  # MCP-Server startete mit der Session


def test_lauf_aus_werkzeug_spaet_gestartet_heisst_arbeitet() -> None:
    gate = (400, r'"C:\Program Files\PowerShell\7\pwsh.exe" -c pytest -n 8', _ft(T0 + 900))
    lauf = pc_fenster.lauf_aus(_liste(p420=gate), 501)
    assert lauf.werkzeug_arbeitet is True


def test_lauf_aus_tab_mit_weiterem_kind_wird_nicht_mitbeendet() -> None:
    fremd = (200, r'"C:\Python313\python.exe" anderes.py', _ft(T0 - 5))
    lauf = pc_fenster.lauf_aus(_liste(p250=fremd), 501)
    assert lauf.tab_pid is None  # pwsh trägt mehr als diese Session
    assert lauf.bau_pid == 300


def test_lauf_aus_recycelte_pid_zaehlt_nicht() -> None:
    liste = _liste()
    liste[400] = (300, _CLAUDE, _ft(T0 - 3600))  # „Kind“ älter als bau.py → recycelte PID
    lauf = pc_fenster.lauf_aus(liste, 501)
    assert lauf.session_pid is None


def test_lauf_aus_anderes_ticket_nicht_getroffen() -> None:
    lauf = pc_fenster.lauf_aus(_liste(), 50)
    assert lauf == pc_fenster.Lauf(None, None, None, None, False)


# ---------------------------------------------------------------- baum_beenden

_BAUM = (
    "import subprocess,sys,time; "
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
    "print(p.pid, flush=True); time.sleep(60)"
)


@pytest.mark.skipif(not WINDOWS, reason="taskkill-Baum nur am PC")
def test_baum_beenden_beendet_echten_baum_mit_kind() -> None:
    eltern = subprocess.Popen(
        [sys.executable, "-c", _BAUM], stdout=subprocess.PIPE, text=True, **prozessbaum.ohne_fenster()
    )
    try:
        assert eltern.stdout is not None
        kind = int(eltern.stdout.readline().strip())
        assert prozessbaum._lebt_windows(eltern.pid) and prozessbaum._lebt_windows(kind)
        assert pc_fenster.baum_beenden(eltern.pid) is True
        assert not prozessbaum._lebt_windows(eltern.pid)
        ende = time.monotonic() + 5
        while prozessbaum._lebt_windows(kind) and time.monotonic() < ende:
            time.sleep(0.2)
        assert not prozessbaum._lebt_windows(kind), "Kind überlebt den Baum-Abbau"
    finally:
        if eltern.poll() is None:
            eltern.kill()


@pytest.mark.skipif(not WINDOWS, reason="nur am PC")
def test_baum_beenden_schon_weg_ist_true() -> None:
    p = subprocess.Popen([sys.executable, "-c", "pass"], **prozessbaum.ohne_fenster())
    p.wait()
    assert pc_fenster.baum_beenden(p.pid) is True


# ---------------------------------------------------------------- wt-Start


class _WtAufnahme:
    """Zeichnet Aufrufe des externen Dienstes ``wt.exe`` auf (Antwort wie echtes wt: Exit 0)."""

    def __init__(self, exit_code: int = 0) -> None:
        self.aufrufe: list[tuple[list[str], dict[str, object]]] = []
        self.exit_code = exit_code

    def __call__(self, befehl: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append((list(befehl), kw))
        return subprocess.CompletedProcess(befehl, self.exit_code, "", "" if not self.exit_code else "0x80070002")


def test_tab_start_nutzt_befehl_aus_wt_tab_befehl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    auftrag = "Weiter ab Handoff docs/handoffs/HANDOFF_x_501.md; los"
    pc_fenster.PcWerkzeug().tab_starten(tmp_path, 501, auftrag)
    assert len(wt.aufrufe) == 1
    befehl, kw = wt.aufrufe[0]
    assert befehl == spawn.wt_tab_befehl(tmp_path, 501, auftrag, "--remote-control")
    assert befehl[:2] == ["wt", "-w"] and "bau 501" in befehl
    assert kw.get("cwd") == str(tmp_path)


def test_tab_start_wt_fehler_wird_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_fenster.subprocess, "run", _WtAufnahme(exit_code=1))
    with pytest.raises(OSError, match="wt Exit 1"):
        pc_fenster.tab_starten(tmp_path, 501, "")


# ---------------------------------------------------------------- Weiche


class _TmuxAntwortet:
    def fenster_liste(self) -> list[object]:
        return []


class _TmuxFehlt:
    def fenster_liste(self) -> list[object]:
        from to_spawn.tmux_aufruf import TmuxFehler

        raise TmuxFehler("list-panes", "tmux nicht gefunden")


def test_weiche_tmux_antwortet_bleibt_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_fenster, "am_pc", lambda: True)
    tmux = _TmuxAntwortet()
    w = pc_fenster.werkzeug(tmux)
    assert w is tmux
    assert pc_fenster.kann_tippen(w) is True


def test_weiche_tmux_antwortet_nicht_nimmt_pc_weg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_fenster, "am_pc", lambda: True)
    w = pc_fenster.werkzeug(_TmuxFehlt())
    assert isinstance(w, pc_fenster.PcWerkzeug)
    assert pc_fenster.kann_tippen(w) is False


def test_weiche_bau_server_fragt_tmux_nie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_fenster, "am_pc", lambda: False)
    tmux = _TmuxFehlt()
    assert pc_fenster.werkzeug(tmux) is tmux


@pytest.mark.skipif(not WINDOWS or bool(os.environ.get("TMUX")), reason="echter PC ohne tmux")
def test_weiche_echt_am_pc_ohne_tmux() -> None:
    assert isinstance(respawn.werkzeug_fuer_rechner(), pc_fenster.PcWerkzeug)


# ---------------------------------------------------------------- tote Session


@pytest.mark.skipif(not WINDOWS, reason="echte Windows-Prozessliste")
def test_tote_session_ist_kein_fenster(tmp_path: Path) -> None:
    # Ticket ohne bau.py in der echten Prozessliste; Sitzungsdatei liegt trotzdem da.
    ticket = 987_651
    datei = tmp_path / ".to-spawn" / "sessions" / f"{ticket}.json"
    datei.parent.mkdir(parents=True)
    datei.write_text(json.dumps({"session_id": "abc", "cwd": str(tmp_path)}), encoding="utf-8")
    still = pc_fenster.still_s(tmp_path, ticket, time.time())
    assert still is None
    assert aufseher_stand._pc_lage(still) == (aufseher_stand.KEIN_FENSTER, None)


# ---------------------------------------------------------------- Handoff nur nach Session-Start


def _handoff(ordner: Path, name: str, mtime: float) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    p = ordner / name
    p.write_text("# Handoff\nweiter bei X\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


class _PcFake(pc_fenster.PcWerkzeug):
    """Rechenlogik-Fake: feste Prozess-Momentaufnahme, nichts wird beendet oder gestartet."""

    def __init__(self, start: float) -> None:
        self.start = start

    def alte_session(self, repo: Path, spec: int, ticket: int) -> pc_fenster.Alte:
        return pc_fenster.Alte(300, 400)

    def lauf(self, ticket: int) -> pc_fenster.Lauf:
        return pc_fenster.Lauf(200, 300, 400, self.start, False)

    def jetzt(self) -> float:
        return self.start + 4000


def _repo_mit_wt(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    basis = tmp_path / "wts"
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({"worktree_basis": basis.as_posix()}), encoding="utf-8")
    return repo, basis / "wt-501"


def test_handoff_vor_session_start_wird_ignoriert(tmp_path: Path) -> None:
    repo, wt = _repo_mit_wt(tmp_path)
    start = time.time() - 3600
    _handoff(wt / respawn.HANDOFF_ORDNER, "HANDOFF_alt_501.md", start - 600)
    erg = pc_fenster.abloesen(respawn.Auftrag(repo, 77, 501, dry_run=True), _PcFake(start))
    assert erg.exit == respawn.EXIT_OK
    assert "ohne Handoff" in erg.zeile and "HANDOFF_alt_501" not in erg.zeile


def test_handoff_nach_session_start_wird_genommen(tmp_path: Path) -> None:
    repo, wt = _repo_mit_wt(tmp_path)
    start = time.time() - 3600
    _handoff(wt / respawn.HANDOFF_ORDNER, "HANDOFF_alt_501.md", start - 600)
    _handoff(wt / respawn.HANDOFF_ORDNER, "HANDOFF_neu_501.md", start + 600)
    erg = pc_fenster.abloesen(respawn.Auftrag(repo, 77, 501, dry_run=True), _PcFake(start))
    assert "HANDOFF_neu_501.md" in erg.zeile
    assert "dry-run (PC)" in erg.zeile


# ---------------------------------------------------------------- Leiter am PC


def test_leiter_pc_stille_unter_grenze_loest_nicht_ab() -> None:
    s = leiter.fuer_rechner(leiter.Schritt("anstupsen", "still"), False, leiter.PC_ABLOESE_MIN - 1)
    assert s.aktion == "nichts"


def test_leiter_pc_stille_ab_grenze_loest_ab() -> None:
    s = leiter.fuer_rechner(leiter.Schritt("anstupsen", "still"), False, leiter.PC_ABLOESE_MIN)
    assert s.aktion == "respawn"


def test_leiter_pc_kontext_grenze_loest_sofort_ab() -> None:
    s = leiter.fuer_rechner(leiter.Schritt("handoff", "Kontext 260k"), False, 0)
    assert s.aktion == "respawn" and leiter.PC_VERMERK in s.grund


def test_leiter_server_bleibt_unveraendert() -> None:
    schritt = leiter.Schritt("anstupsen", "still")
    assert leiter.fuer_rechner(schritt, True, 5) is schritt


# ---------------------------------------------------------------- echte Prozessliste


@pytest.mark.skipif(not WINDOWS, reason="echte Windows-Prozessliste")
def test_echte_prozessliste_traegt_eigenen_prozess() -> None:
    # Befund 05.10.2026: eine Kommandozeile mit Steuerzeichen machte die ganze Liste leer.
    liste = prozessbaum._prozesse_windows()
    assert os.getpid() in liste
    assert liste[os.getpid()][0] == os.getppid()
