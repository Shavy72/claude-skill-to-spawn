"""Speicher-Schutz (#257 Paket B): darf jetzt noch eine Claude-Session starten?

Hintergrund: Am 21.09. starb der Bau-Server im OOM-Kill (16 GB RAM, kein Swap,
12 Claude-Sessions + Gate). Seitdem prüft jeder Starter (``bau``, ``wache``,
``spawn_srv.sh``, Aufpasser) vor dem Claude-Start diese zwei Grenzen aus der
Repo-Konfig (``DEFAULTS["speicher"]``):

* ``min_frei_mib`` — so viel MiB müssen laut ``/proc/meminfo`` (``MemAvailable``)
  noch frei sein;
* ``max_sessions`` — höchstens so viele Prozesse mit ``argv[0]``-Basename ``claude``
  dürfen schon laufen (Richtwert 6 je 16 GB).

**Zulassung nach Speicher-Budget (#1184):** :func:`zulassen` ist die Tür der Starter.
Sie prüft zusätzlich zu den zwei Notbremsen oben, ob nach Abzug der schon zugelassenen,
noch anlaufenden Sessions ``reserve_mib + bedarf_mib`` frei bleiben (Block
``zulassung`` in ``config.DEFAULTS``). Zulassungen stehen mit Zeitstempel in einer
gemeinsamen Datei (``~/.to-spawn-zulassung.json``, Test-Tür ``TO_SPAWN_ZULASSUNG_STAND``),
gesperrt per ``flock`` — so lassen zehn gleichzeitig wartende ``bau.py`` nicht zehn
Sessions auf denselben freien Speicher los. Grenzwerte leben nur hier und in
``config.DEFAULTS``; Aufrufer fragen nur „darf ich starten?“.

Nur Standardbibliothek (plus ``to_spawn.config``), nur Linux: auf anderen Systemen gibt es kein Urteil,
also „frei“. Test-Tür: die Umgebungsvariablen ``TO_SPAWN_SPEICHER_MEMINFO``
(Pfad einer meminfo-Datei) und ``TO_SPAWN_SPEICHER_PROC`` (Ordner im Aufbau von
``/proc``) ersetzen den echten ``/proc`` — so bleibt das Ergebnis in Tests
unabhängig von den Sessions, die gerade auf der Testmaschine laufen.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from to_spawn import config

log = logging.getLogger("to_spawn.speicher")

#: Vorgabe, falls die Konfig den Block ``speicher`` nicht kennt — eine Quelle: ``config.DEFAULTS``.
VORGABE: dict[str, int] = dict(config.DEFAULTS["speicher"])

#: Vorgabe des Budget-Blocks ``zulassung`` (#1184) — eine Quelle: ``config.DEFAULTS``.
ZULASSUNG_VORGABE: dict[str, int] = dict(config.DEFAULTS["zulassung"])

#: Exit-Code der CLI (``to_spawn.py speicher``) und der Starter, wenn kein Platz ist.
EXIT_VOLL = 5

#: Kernstück der Wartezeile von :func:`auf_platz_warten` (``<wer> wartet auf Speicher …``).
#: respawn erkennt daran auf dem Bildschirm, dass bau.py noch vor dem Claude-Start wartet.
WARTE_TEXT = "wartet auf Speicher"

#: Laufende Nummer je Prozess für eindeutige Scope-Namen in :func:`slice_praefix`.
_UNIT_ZAEHLER = itertools.count(1)


def _meminfo_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_MEMINFO") or "/proc/meminfo")


def _proc_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_PROC") or "/proc")


def frei_mib(meminfo: Path | None = None) -> int | None:
    """Freier Speicher in MiB (``MemAvailable`` aus ``/proc/meminfo``), ``None`` = unbekannt.

    ``meminfo`` = Datei im Aufbau von ``/proc/meminfo``; ohne Angabe gilt
    ``TO_SPAWN_SPEICHER_MEMINFO`` bzw. ``/proc/meminfo``.
    """
    datei = meminfo if meminfo is not None else _meminfo_pfad()
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for zeile in text.splitlines():
        if not zeile.startswith("MemAvailable:"):
            continue
        teile = zeile.split()
        if len(teile) < 2 or not teile[1].isdigit():
            return None
        return int(teile[1]) // 1024  # kB → MiB
    return None


def claude_sessions(proc: Path | None = None) -> int | None:
    """Zahl der laufenden Claude-Prozesse (``argv[0]``-Basename ``claude``), ``None`` = unbekannt.

    ``proc`` = Ordner im Aufbau von ``/proc`` (Unterordner je PID mit ``cmdline``);
    ohne Angabe gilt ``TO_SPAWN_SPEICHER_PROC`` bzw. ``/proc``.
    """
    ordner = proc if proc is not None else _proc_pfad()
    try:
        eintraege = list(ordner.iterdir())
    except OSError:
        return None
    anzahl = 0
    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        try:
            roh = (eintrag / "cmdline").read_bytes()
        except OSError:
            continue  # Prozess schon weg oder nicht lesbar — zählt nicht
        teile = roh.split(b"\0")
        argv0 = teile[0].decode("utf-8", errors="replace")
        if Path(argv0).name != "claude":
            continue
        if b"-p" in teile[1:] or b"--print" in teile[1:]:
            continue  # Einmal-Helfer (``claude -p``) einer Session — keine eigene Sitzung
        anzahl += 1
    return anzahl


def _gb_text(mib: int) -> str:
    """1200 MiB → „1,2 GB“, 2048 MiB → „2 GB“ (eine Nachkommastelle, deutsches Komma)."""
    gb = round(mib / 1024, 1)
    if gb == int(gb):
        return f"{int(gb)} GB"
    return f"{gb:.1f}".replace(".", ",") + " GB"


def grenzen(konfig: dict[str, Any] | None) -> dict[str, int]:
    """Die drei Zahlen aus ``konfig["speicher"]``, fehlende aus der Vorgabe."""
    block = (konfig or {}).get("speicher") if isinstance(konfig, dict) else None
    werte = dict(VORGABE)
    if isinstance(block, dict):
        for name in VORGABE:
            wert = block.get(name)
            if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert >= 0:
                werte[name] = int(wert)
    return werte


def staffel_s(konfig: dict[str, Any] | None) -> int:
    """Pause in Sekunden zwischen zwei Fenster-Starts."""
    return grenzen(konfig)["staffel_s"]


def platz_frei(
    konfig: dict[str, Any] | None,
    frei: int | None = None,
    sessions: int | None = None,
) -> tuple[bool, str]:
    """(darf starten?, Satz dazu) — die Entscheidung aller Starter.

    ``frei``/``sessions`` = Messwerte für Tests; ohne Angabe wird gemessen. Kein
    Linux oder Messung unmöglich → kein Urteil, also frei (lieber starten als
    grundlos blockieren). Der Satz ist für Menschen: „Speicher knapp: 1,2 GB frei,
    Mindestmaß 2 GB“ oder „schon 6 Claude-Sessions (Obergrenze 6)“.
    """
    if sys.platform != "linux":
        return True, "Speicher: kein Urteil (kein Linux) — Start erlaubt"
    werte = grenzen(konfig)
    if frei is None:
        frei = frei_mib()
    if sessions is None:
        sessions = claude_sessions()
    if frei is None and sessions is None:
        return True, "Speicher: kein Urteil (/proc nicht lesbar) — Start erlaubt"
    if frei is not None and frei < werte["min_frei_mib"]:
        return False, (f"Speicher knapp: {_gb_text(frei)} frei, Mindestmaß {_gb_text(werte['min_frei_mib'])}")
    if sessions is not None and sessions >= werte["max_sessions"]:
        return False, f"schon {sessions} Claude-Sessions (Obergrenze {werte['max_sessions']})"
    frei_text = _gb_text(frei) if frei is not None else "unbekannt"
    sessions_text = str(sessions) if sessions is not None else "unbekannt"
    return True, (
        f"Speicher frei: {frei_text} frei, {sessions_text} Claude-Sessions "
        f"(Obergrenze {werte['max_sessions']}, Mindestmaß {_gb_text(werte['min_frei_mib'])})"
    )


def budget(konfig: dict[str, Any] | None) -> dict[str, int]:
    """Die Zahlen aus ``konfig["zulassung"]``, fehlende aus der Vorgabe (#1184)."""
    block = (konfig or {}).get("zulassung") if isinstance(konfig, dict) else None
    werte = dict(ZULASSUNG_VORGABE)
    if isinstance(block, dict):
        for name in ZULASSUNG_VORGABE:
            wert = block.get(name)
            if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert >= 0:
                werte[name] = int(wert)
    return werte


def _stand_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_ZULASSUNG_STAND") or Path.home() / ".to-spawn-zulassung.json")


def budget_urteil(konfig: dict[str, Any] | None, frei: int, anlaufend: int) -> tuple[bool, str]:
    """Reine Rechnung: reicht ``frei`` (MiB) nach Abzug von ``anlaufend`` vorgemerkten Sessions?

    Regel: ``frei − anlaufend × bedarf ≥ reserve + bedarf``. Die Reserve gehört sshd,
    tmux, Aufseher und dem Deploy-Gate — Sessions bekommen nur, was darüber liegt.
    """
    werte = budget(konfig)
    vorgemerkt = anlaufend * werte["bedarf_mib"]
    rest = frei - vorgemerkt
    noetig = werte["reserve_mib"] + werte["bedarf_mib"]
    text = (
        f"{_gb_text(frei)} frei, {_gb_text(vorgemerkt)} für {anlaufend} anlaufende Sessions "
        f"vorgemerkt, nötig {_gb_text(noetig)} (Bedarf {_gb_text(werte['bedarf_mib'])} "
        f"+ Reserve {_gb_text(werte['reserve_mib'])})"
    )
    if rest < noetig:
        return False, f"Speicher-Budget voll: {text}"
    return True, f"Speicher-Budget frei: {text}"


def _starts_lesen(datei: Path) -> list[float]:
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []  # fehlend/kaputt = keine Vormerkungen; schlimmstenfalls eine Staffel zu früh
    starts = roh.get("starts") if isinstance(roh, dict) else None
    if not isinstance(starts, list):
        return []
    return [float(t) for t in starts if isinstance(t, (int, float)) and not isinstance(t, bool)]


#: Mindestabstand zweier Aufräum-Läufe aus der Platten-Sperre (Stempel-Datei, alle Starter).
#: ponytail: fester Abstand, eigener Konfig-Key erst, wenn 30 min nicht passen.
PLATTE_RAEUMEN_ABSTAND_S = 1800


def platte_frei_gb(pfad: Path | None = None) -> float | None:
    """Freie GB auf der Platte von ``pfad`` (Vorgabe Home); ``None`` = nicht messbar.

    ``TO_SPAWN_PLATTE=aus`` = Test-Tür: kein Urteil. Testprozesse (wache/bau als Subprozess)
    erben nur Env — ohne sie mäße ein Test mit ``HOME`` in einem kleinen tmpfs „knapp“
    und startete echtes Aufräumen (docker prune, journalctl, /tmp).
    """
    if os.environ.get("TO_SPAWN_PLATTE") == "aus":
        return None
    try:
        return shutil.disk_usage(pfad or Path.home()).free / 1024**3
    except OSError:
        return None


def _raeumen_stempel() -> Path:
    return _stand_pfad().with_name(".to-spawn-aufraeumen.stand")


def platte_urteil(konfig: dict[str, Any] | None, *, raeumen: bool = True) -> tuple[bool, str]:
    """(genug Platte?, Satz dazu) — unter ``platte_min_gb`` erst einmal aufräumen (#1184).

    ``raeumen=False`` = nur fragen. Aufgeräumt wird höchstens alle
    :data:`PLATTE_RAEUMEN_ABSTAND_S` Sekunden, sonst räumte jeder Wartezyklus neu auf.
    Nicht messbar → kein Urteil, also frei.
    """
    grenze = budget(konfig)["platte_min_gb"]
    frei = platte_frei_gb()
    if frei is None or frei >= grenze:
        return True, "Platte: genug frei"
    stempel = _raeumen_stempel()
    try:
        zuletzt = stempel.stat().st_mtime
    except OSError:
        zuletzt = 0.0
    if raeumen and time.time() - zuletzt >= PLATTE_RAEUMEN_ABSTAND_S:
        from to_spawn import aufraeumen  # spät: aufraeumen braucht speicher nicht, hält Import schlank

        log.warning("Platte knapp (%.1f GB frei, Mindestmaß %s GB) — räume auf", frei, grenze)
        try:
            stempel.touch()
        except OSError as fehler:
            log.warning("Aufräum-Stempel nicht schreibbar: %s", fehler)
        try:
            aufraeumen.taeglich()
        except OSError as fehler:
            log.warning("Aufräumen gescheitert: %s", fehler)
        frei = platte_frei_gb()
        if frei is None or frei >= grenze:
            return True, "Platte: nach Aufräumen genug frei"
    grund = f"Platte voll: {frei:.1f} GB frei, Mindestmaß {grenze} GB".replace(".", ",", 1)
    log.warning("%s", grund)
    return False, grund


def zulassen(
    konfig: dict[str, Any] | None,
    *,
    reservieren: bool = True,
    frei: int | None = None,
    sessions: int | None = None,
    jetzt: float | None = None,
) -> tuple[bool, str]:
    """(darf starten?, Satz dazu) — die Zulassung aller Starter (#1184).

    Erst die Notbremsen aus :func:`platz_frei` (Mindestmaß, ``max_sessions``), dann die
    Platte (:func:`platte_urteil`, räumt nur bei ``reservieren`` auf), dann das
    Budget (:func:`budget_urteil`) gegen alle Zulassungen der letzten ``anlauf_s``
    Sekunden — eine frische Session belegt ihren Speicher erst nach dem Anlauf, ohne
    Vormerkung sähe jeder Wartende denselben freien Speicher. ``reservieren=False`` =
    nur fragen (Aufpasser-Vorprüfung, CLI), ``True`` = bei Ja den Start vormerken.
    Kein Linux / keine Messung → wie :func:`platz_frei` „frei“.
    """
    if sys.platform != "linux":
        return platz_frei(konfig, frei, sessions)
    if frei is None:
        frei = frei_mib()
    ok, grund = platz_frei(konfig, frei, sessions)
    if not ok or frei is None:
        return ok, grund
    ok, grund = platte_urteil(konfig, raeumen=reservieren)
    if not ok:
        return ok, grund
    try:
        import fcntl  # nur Linux
    except ImportError:  # Test-Tür sys.platform="linux" auf Windows
        return budget_urteil(konfig, frei, 0)

    datei = _stand_pfad()
    zeit = time.time() if jetzt is None else jetzt
    anlauf = budget(konfig)["anlauf_s"]
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        griff = open(datei.with_name(datei.name + ".lock"), "a+", encoding="utf-8")  # noqa: SIM115
    except OSError as fehler:
        log.warning("Zulassung: Sperrdatei nicht nutzbar (%s) — nur Budget ohne Vormerkung", fehler)
        return budget_urteil(konfig, frei, 0)
    with griff:
        fcntl.flock(griff, fcntl.LOCK_EX)
        starts = [t for t in _starts_lesen(datei) if 0 <= zeit - t < anlauf]
        ok, text = budget_urteil(konfig, frei, len(starts))
        if ok and reservieren:
            starts.append(zeit)
            tmp = datei.with_name(datei.name + ".neu")
            tmp.write_text(json.dumps({"starts": starts}), encoding="utf-8")
            os.replace(tmp, datei)
    return ok, text


def slice_praefix(
    konfig: dict[str, Any] | None,
    einheit: str,
    *,
    uid: int | None = None,
    hat: Callable[[str], object] | None = None,
    bus_da: Callable[[Path], bool] | None = None,
    anlegen: Callable[[Path], object] | None = None,
) -> list[str]:
    """Präfix-Wörter, die einen Claude-Start in die Bau-Slice legen (#1184), sonst ``[]``.

    ``systemd-run --user --scope --slice=<slice> --unit=<einheit>-<pid>-<n> -- choom -n <n> --
    env TMPDIR=<tmpdir> …`` — ``--scope``, ``choom`` und ``env`` führen per exec aus, die
    PID bleibt die von ``claude`` (Aufpasser und Zählung sehen sie weiter). Kein Linux,
    ``slice`` leer, Werkzeug fehlt oder kein User-Bus → ``[]`` mit Warnung, nie Abbruch.
    Jeder Aufruf liefert einen eigenen Unit-Namen (Zähler je Prozess), damit die nächste
    Staffel-Runde nicht an „unit already exists“ scheitert — darum je Start neu aufrufen.
    ``tmpdir`` wird angelegt; klappt das nicht, fällt nur der ``TMPDIR``-Teil weg.
    ``uid``/``hat``/``bus_da``/``anlegen`` = Test-Türen.
    """
    block = (konfig or {}).get("bau_schutz") if isinstance(konfig, dict) else None
    werte = {**config.DEFAULTS["bau_schutz"], **(block if isinstance(block, dict) else {})}
    if sys.platform != "linux" or not werte.get("slice") or os.environ.get("TO_SPAWN_BAU_SLICE") == "aus":
        return []  # TO_SPAWN_BAU_SLICE=aus: Test-Tür (Suite auf Linux startet Attrappen, keine Scopes)
    import shutil

    finde = hat or shutil.which
    fehlt = [w for w in ("systemd-run", "choom") if not finde(w)]
    nutzer = os.getuid() if uid is None else uid  # type: ignore[attr-defined,unused-ignore]
    laufzeit = f"/run/user/{nutzer}"  # Text, nicht Path: bleibt auch in Windows-Tests POSIX
    if fehlt or not (bus_da or Path.exists)(Path(laufzeit, "bus")):
        log.warning("Start ohne Bau-Slice: %s", f"fehlt {', '.join(fehlt)}" if fehlt else "kein User-Bus")
        return []
    tmp_teil: list[str] = []
    if werte.get("tmpdir"):
        try:
            (anlegen or (lambda p: p.mkdir(parents=True, exist_ok=True)))(Path(werte["tmpdir"]))
            tmp_teil = ["env", f"TMPDIR={werte['tmpdir']}"]
        except OSError as exc:
            log.warning("TMPDIR %s nicht anlegbar (%s) — Start ohne eigenes TMPDIR", werte["tmpdir"], exc)
    return [
        "env",
        f"XDG_RUNTIME_DIR={laufzeit}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={laufzeit}/bus",
        "systemd-run", "--user", "--scope", "--quiet",
        f"--slice={werte['slice']}",
        f"--unit={einheit}-{os.getpid()}-{next(_UNIT_ZAEHLER)}",
        "--",
        "choom", "-n", str(int(werte["choom"])), "--",
        *tmp_teil,
    ]  # fmt: skip


def auf_platz_warten(
    konfig: dict[str, Any] | None,
    *,
    wer: str = "Session",
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]] = zulassen,
    schlafen: Callable[[float], object] = time.sleep,
    takt_s: int = 60,
    meldung_alle: int = 5,
) -> int:
    """Wartet, bis :func:`platz_frei` „frei“ sagt; gibt die Zahl der Wartezyklen zurück (#257 F1).

    Früher brachen ``bau``/``wache`` bei vollem Speicher mit Exit 5 ab — das tmux-Fenster
    ging zu und der Grund war weg. Jetzt prüft die Schleife alle ``takt_s`` Sekunden neu
    und meldet den Grund im ersten und danach jedem ``meldung_alle``-ten Durchlauf auf
    stderr und im Log. ``pruefen``/``schlafen`` sind Test-Türen. Aufpasser und
    ``spawn_srv.sh`` behalten ihr Exit/Auslassen (sie starten viele Fenster).

    Jeder Zyklus prüft mit den Grenzen, die gerade in ``.to-spawn/config.json`` stehen
    (:func:`config.frisch`): wer ``max_sessions``/``min_frei_mib`` ändert, erreicht auch
    schon wartende Fenster — ohne Neustart (Vorfall 05.10.). Kaputte Datei mitten im
    Warten → die letzten gültigen Werte gelten weiter.
    """
    zyklen = 0
    while True:
        konfig = config.frisch(konfig)
        frei, grund = pruefen(konfig)
        if frei:
            if zyklen:
                text = f"{wer}: Speicher wieder frei nach {zyklen} Wartezyklen — {grund}"
                print(text, file=sys.stderr)
                log.info("%s", text)
            return zyklen
        zyklen += 1
        if zyklen == 1 or zyklen % meldung_alle == 0:
            text = f"{wer} {WARTE_TEXT} (Zyklus {zyklen}, Takt {takt_s} s): {grund}"
            print(text, file=sys.stderr)
            log.info("%s", text)
        schlafen(takt_s)


def cli(konfig: dict[str, Any] | None, nur_staffel: bool = False) -> int:
    """Unterbefehl ``to_spawn.py speicher``: Stand drucken, Exit 0 = frei, 5 = voll.

    ``--staffel`` druckt nur die Sekundenzahl der Staffel (für Shell-Skripte).
    """
    if nur_staffel:
        print(staffel_s(konfig))
        return 0
    ok, grund = zulassen(konfig, reservieren=False)
    print(grund)
    return 0 if ok else EXIT_VOLL
