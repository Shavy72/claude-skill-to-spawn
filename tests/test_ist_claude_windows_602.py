"""#602: ``prozessbaum.ist_claude`` erkennt Claude-Sessions unter Windows ohne ``/proc``.

Gestellt ist nur die Windows-Prozessliste (Kommandozeile je PID, „lebt noch“) — so, wie
``NtQueryInformationProcess`` sie liefert. Die Erkennung selbst läuft echt.
"""

from __future__ import annotations

import pytest

from to_spawn import prozessbaum

#: PID → Kommandozeile; fehlt die PID, ist der Prozess weg.
LISTE = {
    11: r'"C:\Users\x\.local\bin\claude.exe" --dangerously-skip-permissions',
    12: r'"C:\Program Files\nodejs\node.exe" C:\npm\node_modules\@anthropic-ai\claude-code\cli.js',
    13: r'"C:\Program Files\Git\usr\bin\bash.exe" -l',
    14: r'"C:\Program Files\Git\usr\bin\bash.exe" -c "python C:\x\.claude\hooks\staffel_stop.py"',
    15: r'"C:\Python313\python.exe" C:\dev\wt\to_spawn\bau.py 602',
    16: r'"C:\Program Files\nodejs\node.exe" C:\npm\node_modules\prettier\bin\prettier.cjs',
}
#: Lebt, aber Kommandozeile nicht lesbar.
UNLESBAR = 17


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "_kommandozeile_windows", lambda pid: LISTE.get(pid, ""))
    monkeypatch.setattr(prozessbaum, "_lebt_windows", lambda pid: pid in LISTE or pid == UNLESBAR)

    def kein_proc(pid: int, datei: str) -> bytes:
        raise AssertionError(f"/proc/{pid}/{datei} unter Windows gelesen")

    monkeypatch.setattr(prozessbaum, "_proc", kein_proc)


@pytest.mark.parametrize(
    ("pid", "erwartet"), [(11, True), (12, True), (13, False), (14, False), (15, False), (16, False)]
)
def test_windows_kommandozeile_entscheidet(windows: None, pid: int, erwartet: bool) -> None:
    assert prozessbaum.ist_claude(pid) is erwartet


def test_windows_prozess_weg_ist_kein_claude(windows: None) -> None:
    assert prozessbaum.ist_claude(99) is False


def test_windows_lebt_aber_unlesbar_zaehlt_als_claude(windows: None) -> None:
    assert prozessbaum.ist_claude(UNLESBAR) is True
