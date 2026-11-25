"""#649: Hausmeister-Tests unter Windows hängen nicht an echten Prozessen des Rechners.

Ursache: Unter Windows prüft :func:`hausmeister_windows.kein_prozess` sowohl die
CIM-Prozessliste (PowerShell) als auch per :func:`prozessbaum.arbeitsordner_in_windows`
die PEB-cwd jedes laufenden Prozesses. Steht auf dem Runner zufällig ein fremder Prozess im
Start oder Ende ohne cwd-Griff im PEB (``drin=None``), hält der Hausmeister den Test-Ordner
auf der sicheren Seite fest — der Test kippt zufällig (gesehen an PID 9648 in #649).

Abhilfe an EINER Stelle: ``_hausmeister_nur_im_test`` in ``tests/conftest.py`` schirmt
``hausmeister_windows.kein_prozess`` (kein echtes PowerShell) und
``prozessbaum.arbeitsordner_in_windows`` (nur eigene Kindprozesse des laufenden Tests samt
Enkeln, nie fremde Prozesse des Rechners) ab. Eigene Kindprozesse — auch solche mit
unlesbarem PEB — bleiben weiter sichtbar; echte PowerShell-Stubs (``_POWERSHELL != "powershell"``)
und direkte Aufrufe von ``hausmeister_windows._prozesse()`` bleiben unberührt.
"""

# ruff: noqa: F811 — ``welt`` und ``repos`` kommen als Fixtures aus #1368 bzw. #176.
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_hausmeister_1368 import Welt, welt  # noqa: F401
from test_hausmeister_ticket_repo_176 import APP, SKILL, Repos, repos  # noqa: F401

from conftest import attrappe
from to_spawn import hausmeister, hausmeister_windows, prozessbaum


def _windows_peb_welt(
    monkeypatch: pytest.MonkeyPatch,
    momentaufnahme: dict[int, tuple[int, int]],
    *,
    cwd_text: dict[int, str | Exception],
    drin: dict[int, bool | Exception],
    fremd: set[int] | None = None,
) -> None:
    """Stellt den Windows-Zweig samt PEB-Pfad portabel (auch auf Linux) nach."""
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "win32")
    monkeypatch.setattr(hausmeister_windows, "sys", SimpleNamespace(platform="win32"))
    altbestand = hausmeister.altbestand_pfad()
    altbestand.parent.mkdir(parents=True, exist_ok=True)
    altbestand.write_text('{"freigegeben": false, "ordner": []}\n', encoding="utf-8")
    monkeypatch.setattr(prozessbaum, "_ordner_von", lambda pfad: ((1, 100), rf"\\?\{pfad}"))
    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", lambda: dict(momentaufnahme))

    def _arbeitsordner(pid: int) -> str | None:
        wert = cwd_text.get(pid)
        if isinstance(wert, Exception):
            raise wert
        return wert

    def _liegt_drin(pid: int, _ziel: object, _kennungen: object) -> bool | None:
        wert = drin.get(pid)
        if isinstance(wert, Exception):
            raise wert
        return wert

    monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", _arbeitsordner)
    monkeypatch.setattr(prozessbaum, "_arbeitsordner_drin", _liegt_drin)
    monkeypatch.setattr(prozessbaum, "_ist_fremd_windows", lambda pid: pid in (fremd or set()))
    monkeypatch.setattr(prozessbaum, "_ist_weg_windows", lambda _pid: False)


def test_fremder_rechnerprozess_ohne_cwd_griff_sperrt_welt_nicht(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Genau der #649-Befund: fremder Prozess 9648 ohne cwd-Griff darf ``welt`` nicht sperren."""
    wt = welt.worktree(649)
    welt.ticket(649, "CLOSED", 48)
    ich = os.getpid()
    _windows_peb_welt(
        monkeypatch,
        {ich: (1, 100), 9648: (1, 200)},
        cwd_text={ich: r"C:\test", 9648: r"C:\runner"},
        drin={ich: False, 9648: OSError("Prozess 9648: kein cwd-Griff im PEB")},
    )

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not wt.exists()


def test_eigener_kindprozess_und_enkel_im_ordner_sperren_weiter(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Vom Test gestartete Kinder (und Enkel, z. B. MSYS) im Ordner halten den Ordner weiter."""
    wt = welt.worktree(650)
    welt.ticket(650, "CLOSED", 48)
    ich = os.getpid()
    kind, enkel = 99001, 99002
    _windows_peb_welt(
        monkeypatch,
        {ich: (1, 100), 9648: (1, 150), kind: (ich, 200), enkel: (kind, 250)},
        cwd_text={ich: r"C:\test", 9648: r"C:\runner", kind: r"C:\test", enkel: str(wt)},
        drin={
            ich: False,
            9648: OSError("Prozess 9648: kein cwd-Griff im PEB"),
            kind: False,
            enkel: True,
        },
    )

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt" and str(enkel) in r.grund, r.grund
    assert wt.is_dir()


def test_eigener_kindprozess_mit_unlesbarem_peb_sperrt_auch_mit_repos(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein ``if b.drin``-Filter mehr in ``Repos``: eigener Kindprozess mit ``drin=None`` bleibt."""
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    wt = repos.worktree(APP, 651)
    repos.ticket(APP, 651, "CLOSED", 48)
    ich, kind = os.getpid(), 99003
    _windows_peb_welt(
        monkeypatch,
        {ich: (1, 100), 9648: (1, 150), kind: (ich, 200)},
        cwd_text={
            ich: r"C:\test",
            9648: r"C:\runner",
            kind: OSError(299, "ReadProcessMemory gescheitert"),
        },
        drin={ich: False, 9648: OSError("Prozess 9648: kein cwd-Griff im PEB")},
    )

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt" and r.fehler and str(kind) in r.grund, r.grund
    assert "9648" not in r.grund, r.grund
    assert wt.is_dir()


def test_recycelte_eltern_pid_zaehlt_nicht_als_eigenes_kind(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein fremder Prozess, der vor ``os.getpid()`` startete, ist trotz gleicher Eltern-PID fremd."""
    wt = welt.worktree(652)
    welt.ticket(652, "CLOSED", 48)
    ich = os.getpid()
    _windows_peb_welt(
        monkeypatch,
        {ich: (1, 500), 9648: (ich, 100)},  # Startzeit 100 < 500 ⇒ recycelte PID
        cwd_text={ich: r"C:\test", 9648: r"C:\runner"},
        drin={ich: False, 9648: OSError("Prozess 9648: kein cwd-Griff im PEB")},
    )

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not wt.exists()


def test_kein_echtes_powershell_ohne_attrappe_aber_attrappe_und_direktaufruf_wirken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standard-``powershell`` startet in ``kein_prozess`` nicht; eine Test-Attrappe schon."""
    ordner = tmp_path / "wt-6494"
    ordner.mkdir()
    monkeypatch.setattr(hausmeister_windows, "sys", SimpleNamespace(platform="linux"))  # nur CIM-Teil prüfen

    aufrufe: list[list[str]] = []
    echt_run = subprocess.run

    def spion(befehl: list[str], *a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(list(befehl))
        return echt_run(befehl, *a, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(hausmeister_windows.subprocess, "run", spion)

    # 1. Standardwert „powershell“ ⇒ leer, kein Prozess-Start
    hausmeister_windows.kein_prozess(ordner)
    assert aufrufe == []

    # 2. Gesetzte Test-Attrappe (wie in test_hausmeister_1373_windows) ⇒ läuft und findet Prozess
    daten = json.dumps([{"ProcessId": 7777, "CommandLine": f"cmd {ordner}", "ExecutablePath": None}])
    ps = attrappe(tmp_path / "bin", "ps_stub", f"print({daten!r})\n")
    monkeypatch.setattr(hausmeister_windows, "_POWERSHELL", str(ps))
    with pytest.raises(hausmeister_windows.Bleibt, match="7777"):
        hausmeister_windows.kein_prozess(ordner)
    assert len(aufrufe) == 1

    # 3. Direkter Aufruf von ``_prozesse()`` (wie in test_hausmeister_1496_prozessliste) bleibt frei
    ergebnis = hausmeister_windows._prozesse()
    assert ergebnis == [(7777, f"cmd {ordner}", None)]
    assert len(aufrufe) == 2


def test_peb_pruefung_sieht_fremden_prozess_9648_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direkte Probe auf den PEB-Pfad: fremder Prozess 9648 ohne cwd-Griff löst kein Bleibt aus."""
    ordner = tmp_path / "wt-6495"
    ordner.mkdir()
    ich = os.getpid()
    monkeypatch.setattr(prozessbaum, "_ordner_von", lambda pfad: ((1, 100), rf"\\?\{pfad}"))
    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", lambda: {ich: (1, 100), 9648: (1, 200)})
    monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", lambda pid: r"C:\runner")
    monkeypatch.setattr(
        prozessbaum,
        "_arbeitsordner_drin",
        lambda pid, _z, _k: (
            (_ for _ in ()).throw(OSError(f"Prozess {pid}: kein cwd-Griff im PEB")) if pid == 9648 else False
        ),
    )
    monkeypatch.setattr(prozessbaum, "_ist_weg_windows", lambda _pid: False)

    hausmeister_windows._kein_arbeitsordner_drin(ordner)
