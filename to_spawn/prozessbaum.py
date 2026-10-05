"""Prozessbaum ermitteln, prüfen und beenden — an genau einer Stelle.

respawn (alte Session ablösen) und aufpasser (Fenster schließen/fortsetzen) beenden
beide den ganzen Prozessbaum eines tmux-Panes. Der Staffel-Hook findet die eigene
Claude-Session in der Vorfahren-Kette und beendet sie: ``session_vorfahr`` und
``session_beenden`` — die einzigen Teile, die auch unter Windows laufen (#501). Wie der Baum aus ``/proc`` gelesen wird,
was „lebt“ heißt, wie eine Claude-Session erkannt wird und wie Signale stufenweise
eskalieren, steht nur hier. Die Aufrufer wählen nur Stufen (Signal + Wartezeit),
Reihenfolge der PIDs und worauf gewartet wird.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PROC = Path("/proc")

Stufe = tuple[signal.Signals, float]
"""Ein Signal und wie lange danach höchstens gewartet wird (Sekunden)."""


def _kinder() -> dict[int, list[int]]:
    """Eltern-PID → Kind-PIDs aus ``/proc/*/stat``; ``/proc`` selbst unlesbar = Fehler."""
    kinder: dict[int, list[int]] = {}
    for eintrag in _PROC.iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # ``pid (comm) zustand ppid …`` — comm darf Leerzeichen und Klammern enthalten.
        rest = stat[stat.rfind(")") + 2 :].split()
        if len(rest) < 2:
            continue
        kinder.setdefault(int(rest[1]), []).append(int(eintrag.name))
    return kinder


def baum(pid: int) -> list[int]:
    """``pid`` selbst und alle Nachfahren (Breite zuerst, ``pid`` vorn)."""
    kinder = _kinder()
    raus, warteschlange = [], [pid]
    while warteschlange:
        p = warteschlange.pop(0)
        raus.append(p)
        warteschlange.extend(kinder.get(p, []))
    return raus


def lebt(pid: int) -> bool:
    """Prozess existiert noch (``/proc/<pid>`` vorhanden, auch Zombies)."""
    return (_PROC / str(pid)).exists()


def _proc(pid: int, datei: str) -> bytes:
    """Rohinhalt von ``/proc/<pid>/<datei>`` (Naht für Tests)."""
    return (_PROC / str(pid) / datei).read_bytes()


def ist_claude(pid: int) -> bool:
    """Ist ``pid`` eine Claude-Session — nativ (``claude``) oder per npm (``node … claude``)?

    Prozess weg (``FileNotFoundError``/``ProcessLookupError``) heißt „kein Claude“.
    Unlesbar aus anderem Grund zählt als Claude: lieber warten als eine lebende
    Session für beendet halten.
    """
    try:
        name = _proc(pid, "comm").decode(errors="replace").strip()
        argv = [teil.decode(errors="replace") for teil in _proc(pid, "cmdline").split(b"\0")]
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError as fehler:
        log.warning("/proc/%s nicht lesbar (%s) — zählt als Claude.", pid, fehler)
        return True
    argv0 = argv[0] if argv else ""
    if name == "claude" or Path(argv0).name == "claude":
        return True
    if name != "node" and Path(argv0).name != "node":
        return False
    skript = argv[1] if len(argv) > 1 else ""
    return Path(skript).name == "claude" or skript.endswith("claude-code/cli.js")


def senden(pids: Sequence[int], sig: signal.Signals) -> None:
    """Schickt ``sig`` an jede PID in Listenfolge; schon beendete zählen nicht als Fehler."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue


def _warte_tot(
    pids: Sequence[int],
    warte_s: float,
    takt_s: float,
    schlafen: Callable[[float], None],
) -> bool:
    """True, sobald keiner der ``pids`` mehr lebt; höchstens ``warte_s / takt_s`` Takte."""
    for _ in range(round(warte_s / takt_s)):
        if not any(lebt(pid) for pid in pids):
            return True
        schlafen(takt_s)
    return False


def beenden(
    pids: Sequence[int],
    stufen: Sequence[Stufe],
    *,
    warten_auf: Sequence[int] | None = None,
    takt_s: float = 0.1,
    schlafen: Callable[[float], None] | None = None,
    eskalation: Callable[[signal.Signals, list[int]], None] | None = None,
) -> bool:
    """Beendet ``pids`` stufenweise (z. B. SIGTERM, dann SIGKILL).

    Je Stufe bekommen die noch lebenden ``pids`` das Signal — in der Reihenfolge der
    Liste —, dann wird bis zur Wartezeit der Stufe im Takt ``takt_s`` gewartet, bis
    keiner aus ``warten_auf`` (Vorgabe: alle ``pids``) mehr lebt. ``eskalation`` hört
    vor jeder Folgestufe, welches Signal an welche Überlebenden geht (für Logzeilen).
    ``schlafen`` (Vorgabe: ``time.sleep``) ist die Uhr-Naht für Tests.
    Ergebnis: True, wenn am Ende keiner aus ``warten_auf`` mehr lebt.
    """
    schlafen = schlafen or time.sleep
    ziel = list(pids) if warten_auf is None else list(warten_auf)
    for nr, (sig, warte_s) in enumerate(stufen):
        lebende = [pid for pid in pids if lebt(pid)]
        if not lebende:
            return True
        if nr and eskalation is not None:
            eskalation(sig, lebende)
        senden(lebende, sig)
        if _warte_tot(ziel, warte_s, takt_s, schlafen):
            return True
    return not any(lebt(pid) for pid in ziel)


# --- Session der Staffel finden und beenden (Linux + Windows, #501) ----------------------

#: Höchstens so viele Vorfahren werden nach der Session abgesucht.
MAX_VORFAHREN = 12
#: Frist, die eine Session zum geordneten Beenden bekommt, bevor SIGKILL folgt (Linux).
NACHLAUF_SEKUNDEN = 20.0
#: Programme, die nur Befehle weiterreichen (``sh -c <hook>``) — nie die Session.
WRAPPER_NAMEN = ("sh", "bash", "dash", "zsh", "env")
#: Kommandozeilen, die nie die Session sind (Launcher, Hook selbst).
NIE_SESSION = ("bau.py", "staffel_stop.py")
_WINDOWS = sys.platform == "win32"


def ohne_fenster() -> dict[str, Any]:
    """Popen-Optionen für Hilfsprozesse: unter Windows ohne sichtbares Fenster.

    Nie ``DETACHED_PROCESS``: ohne Konsole bekäme jedes Kind eine eigene, und Windows
    Terminal reißt dafür je ein Fenster nach vorn (Befund 27.09.2026).
    """
    if _WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def ist_session_zeile(zeile: str) -> bool:
    """Ist diese Kommandozeile die Claude-Session selbst?

    Ein Teilstring-Treffer auf „claude" genügt nicht: der direkte Vorfahr des
    Hooks ist ``/bin/sh -c <hook-befehl>``, und dieser Befehl kann „claude" im
    Pfad tragen (``~/.claude/…``). Getroffen wird nur ein Programm, das
    ``claude`` heißt oder im Paket ``claude-code`` liegt. Unter Windows zählen
    Anführungszeichen und die Endung ``.exe`` nicht zum Namen.
    """
    teile = [t.strip('"') for t in zeile.split()] if _WINDOWS else zeile.split()
    if not teile:
        return False
    if any(marke in zeile for marke in NIE_SESSION):
        return False
    programm = Path(teile[0]).name
    if _WINDOWS and programm.lower().endswith(".exe"):
        programm = programm[:-4]
    if programm in WRAPPER_NAMEN and "-c" in teile[1:3]:
        return False
    for teil in teile:
        if teil.startswith("-"):
            continue
        pfad = Path(teil)
        if pfad.name.startswith("claude"):
            return True
        if "claude-code" in pfad.parts:
            return True
    return False


def _ppid_linux(pid: int) -> int | None:
    try:
        zeilen = _proc(pid, "status").decode("utf-8", errors="replace")
    except OSError:
        return None
    for zeile in zeilen.splitlines():
        if zeile.startswith("PPid:"):
            try:
                return int(zeile.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _cmdline_linux(pid: int) -> str:
    try:
        rohwert = _proc(pid, "cmdline")
    except OSError:
        return ""
    return rohwert.replace(b"\x00", b" ").decode("utf-8", "replace")


def _vorfahren_linux(pid: int) -> Iterator[tuple[int, str]]:
    """``(pid, Kommandozeile)`` der Vorfahren aus ``/proc``, nächster zuerst."""
    aktuell = _ppid_linux(pid)
    while aktuell is not None:
        yield aktuell, _cmdline_linux(aktuell)
        aktuell = _ppid_linux(aktuell)


#: Alle Prozesse als JSON: p = PID, e = Eltern-PID, c = Kommandozeile, t = Startzeit.
_PS_PROZESSE = (
    # UTF-8-Ausgabe: im OEM-Zeichensatz würden Nicht-ASCII-Zeichen fremder Kommandozeilen
    # (Prompts mit „…“, Emoji) zu 0x1A bzw. ungültigem UTF-8 und brächen das JSON.
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{ "
    "p = [int]$_.ProcessId; e = [int]$_.ParentProcessId; c = [string]$_.CommandLine; "
    "t = $(if ($_.CreationDate) { $_.CreationDate.ToFileTimeUtc() } else { 0 }) } } "
    "| ConvertTo-Json -Compress"
)


def _prozesse_windows() -> dict[int, tuple[int, str, int]]:
    """PID → (Eltern-PID, Kommandozeile, Startzeit) aller Prozesse; Fehler = leer.

    WMI antwortet unter Last gelegentlich leer oder mit Fehler — bis zu drei Versuche.
    """
    daten: Any = None
    for versuch in range(1, 4):
        try:
            aus = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PROZESSE],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                **ohne_fenster(),
            )
            if aus.returncode == 0 and aus.stdout.strip():
                # strict=False: Kommandozeilen dürfen rohe Steuerzeichen tragen (Prompt mit
                # Zeilenumbruch/Tab) — PowerShells ConvertTo-Json maskiert sie nicht immer.
                daten = json.loads(aus.stdout, strict=False)
                break
            log.warning(
                "Prozessliste Versuch %d: Exit %d, %s", versuch, aus.returncode, (aus.stderr or "leer").strip()[:200]
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as fehler:
            log.warning("Prozessliste Versuch %d nicht lesbar: %s", versuch, fehler)
        time.sleep(0.5)
    if daten is None:
        return {}
    if isinstance(daten, dict):
        daten = [daten]
    return {int(d["p"]): (int(d["e"]), d.get("c") or "", int(d.get("t") or 0)) for d in daten}


def _vorfahren_windows(pid: int) -> Iterator[tuple[int, str]]:
    """Wie ``_vorfahren_linux``, aus einer Momentaufnahme von ``Win32_Process``.

    Windows vergibt PIDs neu: ein „Eltern“-Prozess, der nach dem Kind gestartet wurde,
    ist ein Fremder mit recycelter Nummer — dort endet die Kette.
    """
    prozesse = _prozesse_windows()
    kind = prozesse.get(pid)
    while kind is not None:
        eltern_pid, _, kind_start = kind
        eltern = prozesse.get(eltern_pid)
        if eltern is None or eltern_pid == 0 or (eltern[2] and kind_start and eltern[2] > kind_start):
            return
        yield eltern_pid, eltern[1]
        kind = eltern


def session_vorfahr(pid: int, *, grenze: int | None = None) -> int | None:
    """Erster Vorfahr von ``pid``, dessen Kommandozeile die Claude-Session ist.

    Der Launcher (``bau.py``) darf nie getroffen werden — er startet die Folge-Session:
    taucht er auf oder ist die ``grenze`` (PID des Launchers) erreicht, ist das
    Ergebnis ``None``. Oberhalb liegen fremde Sessions, dort wird nie gesucht.
    """
    vorfahren = _vorfahren_windows(pid) if _WINDOWS else _vorfahren_linux(pid)
    for _, (aktuell, zeile) in zip(range(MAX_VORFAHREN), vorfahren):
        if aktuell <= 1 or (grenze is not None and aktuell == grenze):
            return None
        if "bau.py" in zeile:
            return None
        if ist_session_zeile(zeile):
            return aktuell
    return None


def session_beenden(pid: int, protokoll: Path | None = None) -> bool:
    """Beendet die Session ``pid``, ohne darauf zu warten. False = gar nicht angestoßen.

    Der Aufrufer (Stop-Hook) ist selbst ein Kind der Session und darf nicht warten:
    Claude Code läuft erst nach dem Hook weiter. Ein abgelöster Nachläufer erledigt den
    Rest und überlebt das Sterben der Session.

    Linux: SIGTERM sofort, der Nachläufer schickt SIGKILL nach ``NACHLAUF_SEKUNDEN``.
    Windows kennt kein SIGTERM: der Nachläufer wartet, bis der Aufrufer fertig ist, und
    beendet dann den ganzen Baum (``taskkill /T /F`` — auch MCP-Server und Shells).

    ``protokoll``: Datei, an die der Nachläufer eine Ergebnis-Zeile anhängt (seine eigene
    Ausgabe sieht niemand). Ein gescheitertes ``taskkill`` steht dort als ``WARNUNG``.
    """
    if _WINDOWS and not _lebt_windows(pid):
        # Ohne diese Prüfung würde der Nachläufer eine inzwischen recycelte PID samt Baum beenden.
        log.warning("PID %d lebt nicht (mehr) — nichts zu beenden", pid)
        return False
    if not _WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            log.warning("SIGTERM an PID %d fehlgeschlagen: %s", pid, exc)
            return False
    befehl = [sys.executable, str(Path(__file__).resolve()), "--nachlauf", str(pid), str(os.getpid())]
    if protokoll is not None:
        befehl += ["--protokoll", str(protokoll)]
    optionen: dict[str, Any] = ohne_fenster() if _WINDOWS else {"start_new_session": True}
    try:
        subprocess.Popen(
            befehl,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **optionen,
        )
    except OSError as exc:
        log.warning("Nachläufer für PID %d nicht gestartet: %s", pid, exc)
        # Linux: SIGTERM ist raus, die Session endet trotzdem. Windows: nichts passiert.
        return not _WINDOWS
    return True


def _lebt_windows(pid: int) -> bool:
    import ctypes

    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    griff = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not griff:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(griff, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(griff)


def _protokolliere(protokoll: Path | None, zeile: str) -> None:
    """Hängt ``zeile`` mit Zeitstempel an ``protokoll`` — Fehler beim Schreiben sind kein Abbruch."""
    if protokoll is None:
        return
    try:
        protokoll.parent.mkdir(parents=True, exist_ok=True)
        with protokoll.open("a", encoding="utf-8") as datei:
            datei.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {zeile}\n")
    except OSError as exc:
        log.warning("Nachlauf-Protokoll %s nicht schreibbar: %s", protokoll, exc)


def _nachlauf(pid: int, aufrufer: int, frist: float = NACHLAUF_SEKUNDEN, protokoll: Path | None = None) -> int:
    """Notnagel im eigenen Prozess (siehe ``session_beenden``)."""
    ende = time.monotonic() + frist
    if _WINDOWS:
        while time.monotonic() < ende and _lebt_windows(aufrufer):
            time.sleep(0.2)
        erg = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **ohne_fenster()
        )
        if erg.returncode == 0:
            log.info("taskkill /T /F %d → Exit 0", pid)
            _protokolliere(protokoll, f"OK taskkill /T /F {pid} → Exit 0")
        else:
            meldung = (erg.stdout or b"").decode(errors="replace").strip() + (erg.stderr or b"").decode(
                errors="replace"
            ).strip()
            log.warning("taskkill /T /F %d → Exit %d: %s", pid, erg.returncode, meldung)
            _protokolliere(protokoll, f"WARNUNG taskkill /T /F {pid} → Exit {erg.returncode}: {meldung[:200]}")
        return 0
    while time.monotonic() < ende:
        if not lebt(pid):
            _protokolliere(protokoll, f"OK Session {pid} nach SIGTERM beendet")
            return 0
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        _protokolliere(protokoll, f"OK Session {pid} vor SIGKILL beendet ({exc})")
        return 0
    _protokolliere(protokoll, f"OK SIGKILL an Session {pid} nach {frist:.0f} s")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--nachlauf":
        _ziel = Path(sys.argv[5]) if len(sys.argv) >= 6 and sys.argv[4] == "--protokoll" else None
        sys.exit(_nachlauf(int(sys.argv[2]), int(sys.argv[3]), protokoll=_ziel))
    sys.exit(2)
