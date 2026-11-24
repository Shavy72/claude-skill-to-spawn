"""Speicher-Schutz (#257 Paket B): darf jetzt noch eine Claude-Session starten?

Hintergrund: Am 21.09. starb der Bau-Server im OOM-Kill (16 GB RAM, kein Swap,
12 Claude-Sessions + Gate). Seitdem prüft jeder Starter (``bau``, ``wache``,
``spawn_srv.sh``, Aufpasser) vor dem Claude-Start diese zwei Grenzen aus der
Repo-Konfig (``DEFAULTS["speicher"]``):

* ``min_frei_mib`` — so viel MiB müssen laut ``/proc/meminfo`` (``MemAvailable``)
  noch frei sein;
* ``max_sessions`` — höchstens so viele Prozesse mit ``argv[0]``-Basename ``claude``
  dürfen schon je Spec-Sitzung laufen (Richtwert 6 je 16 GB).

Wer zählt gegen ``max_sessions`` (#409)? Nennt der Starter seine tmux-Sitzung
(:func:`platz_frei` mit ``sitzung=``, :func:`platz_frei_hier` aus dem eigenen Fenster),
zählen nur Claude-Prozesse dieser Sitzung — erkannt an der Variable ``TMUX``, die tmux
jedem Prozess eines Fensters mitgibt. Prozesse mit unlesbarer Umgebung zählen
vorsichtshalber mit. Dazu gilt dann die Rechner-Grenze :data:`RECHNER_FAKTOR` ×
``max_sessions`` je 16 GB ``MemTotal`` (bei 6 also 9) über alle Claude-Prozesse des
Rechners, damit viele Specs nebeneinander den Rechner nicht überladen. ``min_frei_mib``
misst immer maschinenweit. Ohne Sitzung (oder wenn tmux nicht antwortet) zählt die ganze
Maschine gegen ``max_sessions`` wie früher. Diese Regel lebt nur hier.

Was belegt einen Platz (#460)? Nur arbeitende Sessions: eine Session, die laut
``~/.claude/sessions/<pid>.json`` still ist (``status: idle``), bremst nur über
``min_frei_mib``; ohne lesbaren Status zählt sie vorsichtshalber. Anzeige-Fenster (Prozess
mit :data:`ANZEIGE_VARIABLE` in der Umgebung, der Leitstand) zählen nie. Dazu zählt jeder
vergebene, noch nicht verfallene Platz (:data:`RESERVIERUNG_S`): :func:`auf_platz_warten`
prüft und belegt unter einer Datei-Sperre in einem Schritt, so starten zwei wartende
Fenster nie auf denselben freien Platz.

Nur Standardbibliothek (plus ``to_spawn.config``), nur Linux: auf anderen Systemen gibt es kein Urteil,
also „frei“. Test-Tür: die Umgebungsvariablen ``TO_SPAWN_SPEICHER_MEMINFO``
(Pfad einer meminfo-Datei) und ``TO_SPAWN_SPEICHER_PROC`` (Ordner im Aufbau von
``/proc``) ersetzen den echten ``/proc`` — so bleibt das Ergebnis in Tests
unabhängig von den Sessions, die gerade auf der Testmaschine laufen. Ebenso
``TO_SPAWN_SPEICHER_SESSIONS`` (Ordner der Session-JSONs) und ``TO_SPAWN_SPEICHER_PLATZ``
(Datei der vergebenen Plätze).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from to_spawn import config, sperre

log = logging.getLogger("to_spawn.speicher")

#: Vorgabe, falls die Konfig den Block ``speicher`` nicht kennt — eine Quelle: ``config.DEFAULTS``.
VORGABE: dict[str, int] = dict(config.DEFAULTS["speicher"])

#: Exit-Code der CLI (``to_spawn.py speicher``) und der Starter, wenn kein Platz ist.
EXIT_VOLL = 5
#: Exit-Code der CLI, wenn der Hausmeister die Platte zu voll meldet (#1369).
EXIT_PLATTE = 7
#: Takt von :func:`auf_platte_warten` — der Hausmeister-Lauf fragt gh, also selten.
PLATTE_TAKT_S = 900

#: Kernstück der Wartezeilen von :func:`auf_platz_warten` (``<wer> wartet auf Speicher …``)
#: und :func:`auf_platte_warten` (``<wer> wartet auf Speicher auf der Platte …``).
#: respawn erkennt daran auf dem Bildschirm, dass bau.py noch vor dem Claude-Start wartet.
WARTE_TEXT = "wartet auf Speicher"


def _meminfo_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_MEMINFO") or "/proc/meminfo")


def _proc_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_PROC") or "/proc")


def _sessions_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_SESSIONS") or Path.home() / ".claude" / "sessions")


def _platz_pfad() -> Path:
    eigen = os.environ.get("TO_SPAWN_SPEICHER_PLATZ")
    if eigen:
        return Path(eigen)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "to-spawn" / "platz.json"


#: Umgebungs-Variable eines Anzeige-Fensters (Leitstand): dessen Claude-Prozess belegt nie einen Platz (#460).
ANZEIGE_VARIABLE = "TO_SPAWN_ANZEIGE_FENSTER"
#: So lange (Sekunden) gilt ein von :func:`auf_platz_warten` vergebener Platz als belegt, auch
#: wenn noch kein arbeitender Claude-Prozess dazu sichtbar ist (#460).
# ponytail: Platz verfällt nach fester Zeit und zählt bis dahin neben der neuen Session doppelt
# (vorsichtig); genauer wäre Freigabe, sobald der Kind-Prozess des Starters arbeitet.
RESERVIERUNG_S = 120


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


#: Rechner-Grenze (#409): je 16 GB Gesamtspeicher dürfen 1,5 × ``max_sessions`` Claude-Sessions
#: auf dem ganzen Rechner laufen — bei 16 GB und 6 also 9. Bewusst kein Konfig-Wert.
RECHNER_FAKTOR = 1.5
#: Bezugsgröße der Grenzen in GB; ohne ``MemTotal`` gilt sie als Gesamtspeicher.
BEZUG_GB = 16


def gesamt_kib(meminfo: Path | None = None) -> int | None:
    """Gesamtspeicher in kB (``MemTotal``), ``None`` = unbekannt. Quelle wie :func:`frei_mib`."""
    datei = meminfo if meminfo is not None else _meminfo_pfad()
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for zeile in text.splitlines():
        teile = zeile.split()
        if len(teile) >= 2 and teile[0] == "MemTotal:" and teile[1].isdigit():
            return int(teile[1])
    return None


def rechner_grenze(max_sessions: int, gesamt: int | None) -> int:
    """Höchstzahl Claude-Sessions auf dem ganzen Rechner: 1,5 × ``max_sessions`` je 16 GB.

    ``gesamt`` = ``MemTotal`` in kB; auf ganze GB gerundet (ein 16-GB-Rechner meldet etwas
    weniger), mindestens 1 GB. Unbekannt → 16 GB.
    """
    gb = max(1, round(gesamt / 1024 / 1024)) if gesamt else BEZUG_GB
    return int(RECHNER_FAKTOR * max_sessions * gb / BEZUG_GB)


#: Eine tmux-Sitzung als (Socket-Pfad, Sitzungs-Nummer) — so steht sie in ``TMUX``.
Sitzung = tuple[str, str]
#: Sitzung, die es (noch) nicht gibt: kein Prozess gehört ihr.
KEINE_SITZUNG: Sitzung = ("", "")


def _sitzung_aus_tmux_wert(wert: str | None) -> Sitzung | None:
    """``TMUX``-Wert ``<socket>,<server-pid>,<nr>`` → (Socket, Nummer); sonst ``None``."""
    teile = (wert or "").rsplit(",", 2)
    if len(teile) != 3 or not teile[0] or not teile[2].isdigit():
        return None
    return teile[0], teile[2]


def _gehoert_zu(eintrag: Path, sitzung: Sitzung) -> bool:
    """Lebt der Prozess in ``sitzung``? Unlesbare Umgebung → ja (nicht nachweislich fremd)."""
    try:
        roh = (eintrag / "environ").read_bytes()
    except OSError:
        return True
    for teil in roh.split(b"\0"):
        if teil.startswith(b"TMUX="):
            return _sitzung_aus_tmux_wert(teil[5:].decode("utf-8", errors="replace")) == sitzung
    return False  # außerhalb von tmux — fremd


def _ist_anzeige(eintrag: Path) -> bool:
    """Trägt der Prozess :data:`ANZEIGE_VARIABLE` in der Umgebung? Unlesbar → nein."""
    try:
        roh = (eintrag / "environ").read_bytes()
    except OSError:
        return False
    return any(teil.startswith(f"{ANZEIGE_VARIABLE}=".encode()) for teil in roh.split(b"\0"))


def _ist_still(pid: str) -> bool:
    """Meldet die Session-JSON des Prozesses ``status: idle``? Keine/kaputte JSON → nein."""
    try:
        roh = json.loads((_sessions_pfad() / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(roh, dict) and str(roh.get("pid") or pid) == pid and roh.get("status") == "idle"


def sitzung_nach_name(name: str, tmux_socket: str | None = None) -> Sitzung | None:
    """tmux-Sitzung ``name`` (z. B. ``spec-12``) auf dem Server ``tmux_socket`` (``-L``).

    Gibt es sie (noch) nicht oder läuft kein tmux-Server, kommt :data:`KEINE_SITZUNG`
    (dann läuft dort auch keine eigene Session). Scheitert die Suche selbst (tmux fehlt,
    Zeitüberschreitung, anderer Fehler), kommt ``None`` — dann zählt der ganze Rechner,
    statt die Grenze still abzuschalten. tmux-Befehl wie überall aus ``TO_SPAWN_TMUX``.
    """
    from to_spawn import tmux_aufruf  # spät: zieht capo und damit viele Module

    argumente = [*(["-L", tmux_socket] if tmux_socket else [])]
    argumente += ["list-sessions", "-F", "#{session_name}\t#{socket_path}\t#{session_id}"]
    try:
        ausgabe = tmux_aufruf.aufrufen(*argumente)
    except tmux_aufruf.TmuxFehler as fehler:
        if "no server running" in fehler.stderr or "error connecting to" in fehler.stderr:
            return KEINE_SITZUNG  # kein tmux-Server = auch keine Sitzung
        log.warning("tmux-Sitzung %s nicht ermittelbar (%s) — zähle den ganzen Rechner", name, fehler)
        return None
    for zeile in ausgabe.splitlines():
        teile = zeile.split("\t")
        if len(teile) == 3 and teile[0] == name:
            return teile[1], teile[2].lstrip("$")
    return KEINE_SITZUNG


def claude_sessions(proc: Path | None = None, sitzung: Sitzung | None = None) -> int | None:
    """Zahl der arbeitenden Claude-Prozesse (``argv[0]``-Basename ``claude``), ``None`` = unbekannt.

    Stille Sessions und Anzeige-Fenster zählen nicht (#460, Regel im Modul-Kopf).
    ``proc`` = Ordner im Aufbau von ``/proc`` (Unterordner je PID mit ``cmdline``, ``environ``);
    ohne Angabe gilt ``TO_SPAWN_SPEICHER_PROC`` bzw. ``/proc``. ``sitzung`` = nur Prozesse
    dieser tmux-Sitzung zählen (#409); ``None`` = alle der Maschine.
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
        if sitzung is not None and not _gehoert_zu(eintrag, sitzung):
            continue  # fremde Session — bremst nur über min_frei_mib (#409)
        if _ist_anzeige(eintrag) or _ist_still(eintrag.name):
            continue  # Anzeige-Fenster / stille Session — bremst nur über min_frei_mib (#460)
        anzahl += 1
    return anzahl


def _sitzung_text(sitzung: Sitzung | None) -> str:
    return ",".join(sitzung) if sitzung else ""


def _vergeben(jetzt: float) -> list[dict[str, Any]]:
    """Noch gültige vergebene Plätze ``{"bis": <Zeit>, "sitzung": "<socket>,<nr>"}``; Fehler → keine."""
    try:
        roh = json.loads(_platz_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(roh, list):
        return []
    return [p for p in roh if isinstance(p, dict) and isinstance(p.get("bis"), (int, float)) and p["bis"] > jetzt]


def _vergebene_zahl(sitzung: Sitzung | None) -> int:
    """Gültige vergebene Plätze — in ``sitzung`` bzw. (``None``) auf dem ganzen Rechner."""
    plaetze = _vergeben(time.time())
    if sitzung is None:
        return len(plaetze)
    return sum(p.get("sitzung") == _sitzung_text(sitzung) for p in plaetze)


def _mit_vergebenen(anzahl: int | None, sitzung: Sitzung | None) -> int | None:
    return None if anzahl is None else anzahl + _vergebene_zahl(sitzung)


def _platz_vergeben(sitzung: Sitzung | None) -> None:
    """Einen Platz für :data:`RESERVIERUNG_S` Sekunden belegen (nur unter der Platz-Sperre rufen)."""
    jetzt = time.time()
    plaetze = [*_vergeben(jetzt), {"bis": jetzt + RESERVIERUNG_S, "sitzung": _sitzung_text(sitzung)}]
    datei = _platz_pfad()
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        neu = datei.with_name(f"{datei.name}.{os.getpid()}.neu")
        neu.write_text(json.dumps(plaetze), encoding="utf-8")
        neu.replace(datei)
    except OSError as fehler:
        log.warning("Platz nicht vermerkt (%s): %s — Start trotzdem", datei, fehler)


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
    *,
    sitzung: str | None = None,
    tmux_socket: str | None = None,
) -> tuple[bool, str]:
    """(darf starten?, Satz dazu) — die Entscheidung aller Starter.

    ``sitzung`` = tmux-Sitzung des Laufs (``spec-<S>``): nur ihre Claude-Sessions zählen
    gegen ``max_sessions`` (#409); ``tmux_socket`` = ``-L``-Name ihres tmux-Servers.
    Ohne ``sitzung`` (oder wenn tmux nicht antwortet) zählt die ganze Maschine. Mit
    ``sitzung`` gilt zusätzlich die Rechner-Grenze (:func:`rechner_grenze`) über alle
    Claude-Sessions. Der RAM-Wert gilt immer maschinenweit.

    ``frei``/``sessions`` = Messwerte für Tests; ohne Angabe wird gemessen. Vorgegebene
    Werte (Parameter oder ``TO_SPAWN_SPEICHER_MEMINFO``/``TO_SPAWN_SPEICHER_PROC``)
    gelten auf jeder Plattform; selbst gemessen wird nur unter Linux. Kein Wert →
    kein Urteil, also frei (lieber starten als grundlos blockieren). Der Satz ist für
    Menschen: „Speicher knapp: 1,2 GB frei, Mindestmaß 2 GB“ oder „schon 6
    Claude-Sessions in spec-12 (Obergrenze 6)“.
    """
    return _urteil(
        konfig, frei, sessions, lambda: sitzung_nach_name(sitzung, tmux_socket) if sitzung else None, sitzung
    )


def platz_frei_hier(konfig: dict[str, Any] | None) -> tuple[bool, str]:
    """:func:`platz_frei` für die tmux-Sitzung, in der dieser Prozess läuft (``TMUX``).

    Für Starter im eigenen Fenster (``bau``, ``wache``). Außerhalb von tmux (z. B. am
    PC) zählt die ganze Maschine wie bei :func:`platz_frei` ohne Sitzung.
    """
    eigen = _sitzung_aus_tmux_wert(os.environ.get("TMUX"))
    return _urteil(konfig, None, None, lambda: eigen, "dieser tmux-Sitzung" if eigen else None)


def _urteil(
    konfig: dict[str, Any] | None,
    frei: int | None,
    sessions: int | None,
    welche: Callable[[], Sitzung | None],
    wo: str | None,
) -> tuple[bool, str]:
    """Gemeinsamer Kern von :func:`platz_frei`/:func:`platz_frei_hier`; ``welche`` erst beim Zählen."""
    linux = sys.platform == "linux"
    werte = grenzen(konfig)
    if frei is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_MEMINFO")):
        frei = frei_mib()
    rechner: int | None = None  # alle Claude-Sessions des Rechners — nur mit Sitzung gemessen
    if sessions is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_PROC")):
        sitzung = welche()
        sessions = _mit_vergebenen(claude_sessions(sitzung=sitzung), sitzung)
        if sitzung is None:
            wo = None  # keine/unbekannte Sitzung → der ganze Rechner zählt gegen max_sessions
        else:
            rechner = _mit_vergebenen(claude_sessions(), None)
    if frei is None and sessions is None:
        grund = "/proc nicht lesbar" if linux else "kein Linux"
        return True, f"Speicher: kein Urteil ({grund}) — Start erlaubt"
    wo_text = f" in {wo}" if wo else ""
    if frei is not None and frei < werte["min_frei_mib"]:
        return False, (f"Speicher knapp: {_gb_text(frei)} frei, Mindestmaß {_gb_text(werte['min_frei_mib'])}")
    if sessions is not None and sessions >= werte["max_sessions"]:
        return False, f"schon {sessions} Claude-Sessions{wo_text} (Obergrenze {werte['max_sessions']})"
    rechner_text = ""
    if rechner is not None:
        grenze = rechner_grenze(werte["max_sessions"], gesamt_kib())
        if rechner >= grenze:
            return False, f"Rechner voll: schon {rechner} Claude-Sessions auf dem Rechner (Obergrenze {grenze})"
        rechner_text = f", Rechner {rechner} von {grenze}"
    frei_text = _gb_text(frei) if frei is not None else "unbekannt"
    sessions_text = str(sessions) if sessions is not None else "unbekannt"
    return True, (
        f"Speicher frei: {frei_text} frei, {sessions_text} Claude-Sessions{wo_text} "
        f"(Obergrenze {werte['max_sessions']}{rechner_text}, Mindestmaß {_gb_text(werte['min_frei_mib'])})"
    )


def _pruefen_und_belegen(
    konfig: dict[str, Any] | None,
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]],
    sitzung: Sitzung | None,
) -> tuple[bool, str]:
    """``pruefen`` und bei „frei“ :func:`_platz_vergeben` unter der Platz-Sperre (#460).

    Sperre nicht öffenbar → ohne Sperre weiter (lieber starten als grundlos blockieren, Warnung);
    nach 30 s noch belegt → diesmal „nicht frei“, der nächste Zyklus versucht es neu.
    """
    try:
        with sperre.halte(_platz_pfad().with_suffix(".sperre"), warten=30) as gehalten:
            if not gehalten:
                return False, "Platz-Vergabe gerade belegt (anderer Start)"
            frei, grund = pruefen(konfig)
            if frei:
                _platz_vergeben(sitzung)
            return frei, grund
    except OSError as fehler:
        log.warning("Platz-Sperre nicht nutzbar (%s) — prüfe ohne Sperre", fehler)
        return pruefen(konfig)


def auf_platz_warten(
    konfig: dict[str, Any] | None,
    *,
    wer: str = "Session",
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]] = platz_frei,
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

    Prüfen und Belegen sind ein Schritt (#460): unter der Platz-Sperre prüft die Schleife und
    vermerkt bei „frei“ einen Platz in der tmux-Sitzung dieses Prozesses (dort startet die
    Session) — jeder spätere Prüfer zählt ihn mit, bis er nach :data:`RESERVIERUNG_S` verfällt.
    """
    zyklen = 0
    eigen = _sitzung_aus_tmux_wert(os.environ.get("TMUX"))
    while True:
        konfig = config.frisch(konfig)
        frei, grund = _pruefen_und_belegen(konfig, pruefen, eigen)
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


def _platte_pruefen(loeschen: bool = True) -> tuple[bool, str]:
    """Start-Check der Platte — die Entscheidung trifft der Hausmeister (#1369).

    ``loeschen=False``: nur messen (Probelauf, Status), nie aufräumen.
    """
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    return hausmeister.start_erlaubt(loeschen=loeschen)


def _platte_knapp() -> bool:
    """Nur messen: herrscht Platznot (der Start-Check räumt dann lange auf)?

    Messung unmöglich oder Fehler ⇒ ``True`` (lieber eine Warte-Zeile zu viel)."""
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    try:
        anteil = hausmeister._frei_anteil()
    except Exception:  # Messfehler: wie Platznot behandeln, Start-Check entscheidet
        log.exception("Platte: Vorab-Messung gescheitert")
        return True
    return anteil is None or hausmeister._ist_platznot(anteil)


def auf_platte_warten(
    *,
    wer: str = "Session",
    pruefen: Callable[[], tuple[bool, str]] = _platte_pruefen,
    schlafen: Callable[[float], object] = time.sleep,
    takt_s: int = PLATTE_TAKT_S,
    knapp: Callable[[], bool] | None = None,
) -> int:
    """Wartet, bis der Hausmeister die Platte für einen Start frei gibt; Rückgabe = Wartezyklen.

    Wie :func:`auf_platz_warten`: das Fenster bleibt offen, der Grund steht auf stderr
    und im Log. ``pruefen``/``schlafen``/``knapp`` sind Test-Türen. Bei genug Platz
    bleibt alles still — „wartet“ erscheint nur, wenn wirklich gewartet wird.
    ``knapp=None``: mit dem echten Start-Check misst :func:`_platte_knapp`; mit einer
    eingesetzten Prüf-Tür gilt Platznot (die Tür spielt den langen Hausmeister-Lauf).
    """
    if knapp is None:
        knapp = _platte_knapp if pruefen is _platte_pruefen else (lambda: True)
    # Bei Platznot schon vor der ersten Prüfung eine Zeile mit WARTE_TEXT: der
    # Hausmeister räumt darin synchron auf (gh/git, Minuten) — respawn erkennt daran
    # das Warten. Ohne Platznot ist die Prüfung kurz, eine Vorab-Zeile wäre falsch.
    if knapp():
        vorab = f"{wer} {WARTE_TEXT} auf der Platte: Hausmeister prüft"
        print(vorab, file=sys.stderr)
        log.info("%s", vorab)
    zyklen = 0
    while True:
        frei, grund = pruefen()
        if frei:
            if zyklen:
                text = f"{wer}: Speicher auf der Platte wieder frei nach {zyklen} Wartezyklen — {grund}"
                print(text, file=sys.stderr)
                log.info("%s", text)
            return zyklen
        zyklen += 1
        text = f"{wer} {WARTE_TEXT} auf der Platte (Zyklus {zyklen}, Takt {takt_s} s): {grund}"
        print(text, file=sys.stderr)
        log.info("%s", text)
        schlafen(takt_s)


def cli(
    konfig: dict[str, Any] | None,
    nur_staffel: bool = False,
    trocken: bool = False,
    sitzung: str | None = None,
) -> int:
    """Unterbefehl ``to_spawn.py speicher``: Stand drucken, Exit 0 = frei, 5 = voll,
    7 = Platte voll (Start-Check des Hausmeisters, vor der RAM-Prüfung).

    ``--staffel`` druckt nur die Sekundenzahl der Staffel (für Shell-Skripte).
    ``--trocken`` (Probelauf, Status): nur messen, der Hausmeister löscht nie.
    ``--sitzung spec-<S>``: Obergrenze nur für diese tmux-Sitzung (#409).
    """
    if nur_staffel:
        print(staffel_s(konfig))
        return 0
    platte_ok, platte_grund = _platte_pruefen(loeschen=not trocken)
    if not platte_ok:
        print(platte_grund)
        return EXIT_PLATTE
    ok, grund = platz_frei(konfig, sitzung=sitzung)
    print(grund)
    return 0 if ok else EXIT_VOLL
