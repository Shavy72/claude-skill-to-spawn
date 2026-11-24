"""Stand der Probe-App nach Staging oder in die Übungs-Live-App bringen bzw. wieder aufräumen.

``python skripte/ausrollen.py <staging|live>``: ``app/`` in den Ordner ``<umgebung>/`` kopieren,
dort im Hintergrund starten, per Abruf belegen (Zeile ``Beleg: <url> -> <text>``).
``python skripte/ausrollen.py <staging|live> --aufraeumen``: Prozess beenden, Ordner löschen.

Die Ports stehen nur in ``ports.json`` (von ``anlegen.sh`` geschrieben). Ohne Staging-Port
(Variante ``ohne-staging``) lehnt der Staging-Schritt mit Exit 2 ab.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
log = logging.getLogger("ausrollen")


def _port(umgebung: str) -> int | None:
    ports = json.loads((REPO / "ports.json").read_text(encoding="utf-8"))
    wert = ports.get(umgebung)
    return int(wert) if wert else None


def _abruf(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as antwort:
            return antwort.read().decode("utf-8")
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def _warte(url: str, erreichbar: bool, sekunden: float = 15.0) -> str | None:
    ende = time.monotonic() + sekunden
    while True:
        text = _abruf(url)
        if (text is not None) == erreichbar or time.monotonic() > ende:
            return text
        time.sleep(0.2)


def _fremde_staging_ordner() -> list[Path]:
    """``staging/``-Ordner der anderen Worktrees dieses Repos (laut ``git worktree list``)."""
    try:
        roh = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError) as fehler:
        log.info("Worktree-Liste nicht lesbar (%s) — nur eigene Instanz", fehler)
        return []
    wurzeln = [Path(z[9:]) for z in roh.splitlines() if z.startswith("worktree ")]
    return [w / "staging" for w in wurzeln if w.resolve() != REPO.resolve()]


def _prozess_im_ordner(pid: int, ordner: Path) -> bool:
    """Positiver Beleg: ``pid`` gehört zu ``ordner``.

    Linux: Arbeitsordner des Prozesses ist genau ``ordner``. Windows: Der Prozess wurde
    kurz vor ``server.pid`` gestartet (Startzeit, siehe ``_startzeit_passt``). Sonst nein.
    """
    if os.name == "nt":
        return _startzeit_passt(pid, ordner / "server.pid")
    try:
        return Path(f"/proc/{pid}/cwd").resolve() == ordner.resolve()
    except OSError:
        return False


# --- Windows ohne ``/proc`` (#556) --------------------------------------------------------
# Absichtlich doppelt: Die Probe-Vorlage bleibt eigenständig (nur Standardbibliothek) und
# kann ``to_spawn/prozessbaum.py`` nicht importieren. Vorbild dort: ``_lebt_windows``.

#: ``ausrollen`` schreibt ``server.pid`` unmittelbar nach dem Start des Servers. Die Startzeit
#: des echten Servers liegt daher knapp VOR der Änderungszeit der Datei. Später gestartet =
#: PID wiederverwendet = fremd; viel früher gestartet = ebenfalls nicht der Schreiber.
PID_FENSTER_S = 60.0
#: Uhren-Rundung zwischen Dateisystem- und Prozesszeit.
PID_SCHLUPF_S = 2.0
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED = 0x1000
_WAIT_TIMEOUT = 0x102
_FEHLER_ZUGRIFF = 5
_EPOCHE_DIFF_S = 11644473600  # 1601 (FILETIME) → 1970 (Unix)


def _oeffne_windows(pid: int, recht: int):  # type: ignore[no-untyped-def]
    """``(kernel32, Griff)``; Griff ``None`` = Prozess weg. ``OSError(5)`` = Zugriff verweigert."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    griff = kernel.OpenProcess(recht, False, pid)
    if griff:
        return kernel, griff
    fehler = ctypes.get_last_error()
    if fehler == _FEHLER_ZUGRIFF:
        raise OSError(fehler, "Zugriff verweigert")
    return kernel, None


def _lebt_windows(pid: int) -> bool:
    """Prozess läuft noch; Zugriff verweigert oder Fehler: vorsichtig ``True``."""
    import ctypes
    from ctypes import wintypes

    try:
        kernel, griff = _oeffne_windows(pid, _SYNCHRONIZE | _QUERY_LIMITED)
    except OSError:
        return True
    if griff is None:
        return False
    try:
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        return kernel.WaitForSingleObject(griff, 0) == _WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(griff)


def _startzeit_windows(pid: int) -> float | None:
    """Startzeit (Unix-Sekunden); nicht feststellbar (weg, kein Zugriff) = ``None``."""
    import ctypes
    from ctypes import wintypes

    try:
        kernel, griff = _oeffne_windows(pid, _QUERY_LIMITED)
    except OSError:
        return None
    if griff is None:
        return None
    try:
        zeiten = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        if not kernel.GetProcessTimes(griff, *(ctypes.byref(z) for z in zeiten)):
            return None
        roh = (zeiten[0].dwHighDateTime << 32) | zeiten[0].dwLowDateTime
        return roh / 1e7 - _EPOCHE_DIFF_S
    finally:
        kernel.CloseHandle(griff)


def _startzeit_passt(pid: int, pid_datei: Path) -> bool:
    """Ist ``pid`` der Prozess, der ``pid_datei`` geschrieben hat? Nicht feststellbar = ``False``."""
    start = _startzeit_windows(pid)
    try:
        geschrieben = pid_datei.stat().st_mtime
    except OSError:
        return False
    return start is not None and -PID_SCHLUPF_S <= geschrieben - start <= PID_FENSTER_S


#: Wartezeit (Sekunden), bis ein beendeter Prozess nachweislich weg ist.
WARTE_S = 5.0


def _lebt(pid: int) -> bool:
    """Prozess läuft (Zombie zählt als weg). Ohne ``/proc`` (nicht Linux/Windows): vorsichtig ``True``."""
    if os.name == "nt":
        return _lebt_windows(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return Path("/proc/self").exists() is False
    except OSError:
        return True
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"


def _warte_weg(pid: int) -> bool:
    ende = time.monotonic() + WARTE_S
    while _lebt(pid):
        if time.monotonic() > ende:
            return False
        time.sleep(0.1)
    return True


def _beende(ordner: Path, port: int, pruefen: bool) -> bool:
    """Instanz in ``ordner`` abbauen; ``False`` = etwas blieb stehen (Prozess oder Ordner)."""
    if ordner.is_symlink():
        log.warning("%s ist ein Symlink — nichts beendet, nichts gelöscht", ordner)
        return False
    pid_datei = ordner / "server.pid"
    if pid_datei.is_file():
        try:
            pid = int(pid_datei.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as fehler:
            log.info("PID-Datei %s unlesbar (%s) — Prozess bleibt", pid_datei, fehler)
            return False
        if pid <= 0:
            log.warning("PID %s in %s ist kein Prozessbeleg — nichts beendet, Ordner bleibt", pid, pid_datei)
            return False
        if _lebt(pid) and os.name == "nt" and not pruefen and not _prozess_im_ordner(pid, ordner):
            # Eigener Ordner, alte server.pid, PID neu vergeben (#556): Prozess nicht unser, Ordner weg.
            log.warning("Prozess %s gehört nicht zu %s (PID neu vergeben) — bleibt, Ordner wird abgebaut", pid, ordner)
        elif _lebt(pid):
            if pruefen and not _prozess_im_ordner(pid, ordner):
                log.warning("Prozess %s gehört nicht nachweislich zu %s — bleibt", pid, ordner)
                return False
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as fehler:
                log.info("Prozess %s schon weg (%s)", pid, fehler)
            if not _warte_weg(pid):
                log.error("Prozess %s läuft trotz Signal weiter — %s bleibt", pid, ordner)
                return False
            _warte(f"http://127.0.0.1:{port}/", erreichbar=False)
        else:
            log.info("Prozess %s aus %s ist schon weg — Ordner wird abgebaut", pid, pid_datei)
    if ordner.exists():
        shutil.rmtree(ordner, ignore_errors=True)
    return True


def aufraeumen(umgebung: str, port: int) -> bool:
    """``True`` = alles abgebaut; ``False`` = mindestens eine Instanz blieb stehen (Log sagt welche)."""
    ok = _beende(REPO / umgebung, port, pruefen=False)
    if umgebung == "staging":  # #542: auch verwaiste Instanzen anderer Worktrees dieses Repos
        for ordner in _fremde_staging_ordner():
            ok = _beende(ordner, port, pruefen=True) and ok
    log.info("%s aufgeräumt" if ok else "%s NICHT vollständig aufgeräumt", umgebung)
    return ok


def ausrollen(umgebung: str, port: int) -> int:
    aufraeumen(umgebung, port)
    ordner = REPO / umgebung
    shutil.copytree(REPO / "app", ordner / "app", ignore=shutil.ignore_patterns("__pycache__"))
    optionen: dict[str, object] = {}
    if os.name == "nt":
        optionen["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        optionen["start_new_session"] = True
    with open(ordner / "server.log", "ab") as protokoll:
        prozess = subprocess.Popen(
            [sys.executable, "-m", "app.server", "--port", str(port), "--umgebung", umgebung],
            cwd=ordner,
            stdin=subprocess.DEVNULL,
            stdout=protokoll,
            stderr=subprocess.STDOUT,
            **optionen,  # type: ignore[arg-type]
        )
    (ordner / "server.pid").write_text(str(prozess.pid), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    text = _warte(url, erreichbar=True)
    if text is None or f"({umgebung})" not in text:
        log.error("%s antwortet nicht wie erwartet: %r", url, text)
        aufraeumen(umgebung, port)
        return 1
    print(f"Beleg: {url} -> {text.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("umgebung", choices=("staging", "live"))
    parser.add_argument("--aufraeumen", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = _port(args.umgebung)
    if port is None:
        log.error(
            "Kein Port für %s in ports.json — dieses Repo hat keine Staging-App.", args.umgebung
        )
        return 2
    if args.aufraeumen:
        return 0 if aufraeumen(args.umgebung, port) else 1
    return ausrollen(args.umgebung, port)


if __name__ == "__main__":
    raise SystemExit(main())
