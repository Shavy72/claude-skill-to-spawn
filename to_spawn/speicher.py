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
vorsichtshalber mit. Dazu gilt dann die Rechner-Grenze :data:`ARBEITEND_JE_BEZUG`
je 16 GB ``MemTotal`` (also 12, unabhängig von ``max_sessions``, #284) über alle arbeitenden
Claude-Prozesse des Rechners, damit viele Specs nebeneinander den Rechner nicht überladen. ``min_frei_mib``
misst immer maschinenweit. Ohne Sitzung (oder wenn tmux nicht antwortet) zählt die ganze
Maschine gegen ``max_sessions`` wie früher. Diese Regel lebt nur hier.

Was belegt einen Platz (#460)? Nur arbeitende Sessions: eine Session, die laut
``~/.claude/sessions/<pid>.json`` still ist (``status: idle``), bremst nur über
``min_frei_mib``; ohne lesbaren Status zählt sie vorsichtshalber. Anzeige-Fenster (Prozess
mit :data:`ANZEIGE_VARIABLE` in der Umgebung, der Leitstand) zählen nie. Dazu zählt jeder
vergebene, noch nicht verfallene Platz (:data:`RESERVIERUNG_S`): :func:`auf_platz_warten`
prüft und belegt unter einer Datei-Sperre in einem Schritt, so starten zwei wartende
Fenster nie auf denselben freien Platz.

Gesamtgrenze (#460): stille Sessions belegen trotzdem Speicher. Mit Sitzung gilt darum zusätzlich
:func:`gesamt_grenze` (:data:`GESAMT_FAKTOR` × Rechner-Grenze, bei 12 also 24) über alle
Claude-Prozesse des Rechners — still, arbeitend und Anzeige.

Fremd voll (#460): hat die eigene Sitzung Platz und füllen nur Sessions außerhalb die Rechner- oder
Gesamtgrenze, endet ``to_spawn.py speicher`` mit :data:`EXIT_FREMD_VOLL` statt :data:`EXIT_VOLL`.
Die Grenzen bleiben gleich hart; ``spawn_srv.sh`` öffnet dann nur das Aufseher-Fenster, das selbst
per :func:`auf_platz_warten` wartet, und meldet den Rest als „wartet auf Platz“.

Nur Standardbibliothek (plus ``to_spawn.config``), nur Linux: auf anderen Systemen gibt es kein Urteil,
also „frei“. Test-Tür: die Umgebungsvariablen ``TO_SPAWN_SPEICHER_MEMINFO``
(Pfad einer meminfo-Datei) und ``TO_SPAWN_SPEICHER_PROC`` (Ordner im Aufbau von
``/proc``) ersetzen den echten ``/proc`` — so bleibt das Ergebnis in Tests
unabhängig von den Sessions, die gerade auf der Testmaschine laufen. Ebenso
``TO_SPAWN_SPEICHER_SESSIONS`` (Ordner der Session-JSONs), ``TO_SPAWN_SPEICHER_PLATZ``
(Datei der vergebenen Plätze) und ``TO_SPAWN_SPEICHER_LOADAVG`` (Datei im Aufbau von
``/proc/loadavg``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from to_spawn import config, sperre, werkzeug

log = logging.getLogger("to_spawn.speicher")

#: Vorgabe, falls die Konfig den Block ``speicher`` nicht kennt — eine Quelle: ``config.DEFAULTS``.
VORGABE: dict[str, int] = dict(config.DEFAULTS["speicher"])

#: Exit-Code der CLI (``to_spawn.py speicher``) und der Starter, wenn kein Platz ist.
EXIT_VOLL = 5
#: Exit-Code der CLI, wenn nur Sessions außerhalb der Spec-Sitzung den Rechner füllen (#460):
#: die eigene Sitzung hat Platz, ein Starter, der selbst wartet (Aufseher), darf sein Fenster öffnen.
EXIT_FREMD_VOLL = 3
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


def _loadavg_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_LOADAVG") or "/proc/loadavg")


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
#: Ein von :func:`auf_platz_warten` vergebener Platz (#460) endet, sobald unter dem Starter-Prozess
#: ein Claude-Prozess sichtbar ist (der zählt dann selbst). Bis dahin gilt er, solange der Starter
#: lebt, höchstens :data:`RESERVIERUNG_MAX_S`; ist der Starter nicht (mehr) sichtbar, verfällt er
#: nach :data:`RESERVIERUNG_S` (Start gescheitert, Claude erschien nie).
RESERVIERUNG_S = 120
# ponytail: endet die Claude-Session vor RESERVIERUNG_MAX_S und der Starter lebt weiter (Kette),
# zählt sein Platz bis dahin wieder mit (vorsichtig); genauer wäre ein „Claude gesehen“-Vermerk.
RESERVIERUNG_MAX_S = 600
#: So lange (Sekunden) wartet :func:`_pruefen_und_belegen` auf die Platz-Sperre.
SPERRE_WARTEN_S = 30


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


def last_1m(loadavg: Path | None = None) -> float | None:
    """1-Minuten-Systemlast aus ``/proc/loadavg``, ``None`` = unbekannt (#760).

    ``loadavg`` = Datei im Aufbau von ``/proc/loadavg``; ohne Angabe gilt
    ``TO_SPAWN_SPEICHER_LOADAVG`` bzw. ``/proc/loadavg``.
    """
    datei = loadavg if loadavg is not None else _loadavg_pfad()
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    teile = text.split()
    if not teile:
        return None
    try:
        wert = float(teile[0])
    except ValueError:
        return None
    return wert if math.isfinite(wert) and wert >= 0 else None


#: Rechner-Grenze (#409, #284): je 16 GB Gesamtspeicher dürfen so viele arbeitende Claude-Sessions
#: auf dem ganzen Rechner laufen. Bewusst kein Konfig-Wert und unabhängig von ``max_sessions``:
#: eine Quelle je Rechner, kein Ordner-Effekt. Messung 25.11.2026 (Bau-Server, 16 GB RAM + 16 GB
#: Auslagerung, 50 Messungen à 30 s, PSS + Auslagerung je Claude-Prozessbaum): Bau-Fenster ohne
#: MCP-Server Median 0,43 GB (max 0,53), Aufseher/Leitstand mit MCP-Servern Median 1,05 GB (max 1,21),
#: Schnitt 0,73 GB je Session; bei 19 Sessions 13,2 GB belegt, 3,3 GB RAM + 4,9 GB Auslagerung frei.
#: 24 Sessions ≈ +3,7 GB → ~2,5 GB Luft über dem Mindestmaß (``min_frei_mib``).
ARBEITEND_JE_BEZUG = 12
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


def rechner_grenze(gesamt: int | None) -> int:
    """Höchstzahl arbeitender Claude-Sessions auf dem ganzen Rechner: :data:`ARBEITEND_JE_BEZUG` je 16 GB.

    ``gesamt`` = ``MemTotal`` in kB; auf ganze GB gerundet (ein 16-GB-Rechner meldet etwas
    weniger), mindestens 1 GB. Unbekannt → 16 GB.
    """
    gb = max(1, round(gesamt / 1024 / 1024)) if gesamt else BEZUG_GB
    return max(1, ARBEITEND_JE_BEZUG * gb // BEZUG_GB)


#: Gesamtgrenze (#460): alle Claude-Prozesse des Rechners (still + arbeitend + Anzeige) höchstens
#: so oft die :func:`rechner_grenze` — stille Sessions belegen 250–1160 MB je Stück.
GESAMT_FAKTOR = 2


def gesamt_grenze(gesamt: int | None) -> int:
    """Höchstzahl aller Claude-Prozesse auf dem Rechner: :data:`GESAMT_FAKTOR` × :func:`rechner_grenze`."""
    return GESAMT_FAKTOR * rechner_grenze(gesamt)


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


def _stat(pid: int, proc: Path | None = None) -> tuple[int, int] | None:
    """(ppid, starttime) aus ``<proc>/<pid>/stat`` (Felder 4 und 22), ``None`` = unlesbar."""
    try:
        text = ((proc if proc is not None else _proc_pfad()) / str(pid) / "stat").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    rest = text[text.rfind(")") + 2 :].split()
    if len(rest) < 20 or not rest[1].isdigit() or not rest[19].isdigit():
        return None
    return int(rest[1]), int(rest[19])


def session_json_lesen(pid: int, sessions: Path | None = None, proc: Path | None = None) -> dict[str, Any] | None:
    """Inhalt von ``<sessions>/<pid>.json`` — nur wenn sie zu genau diesem Prozess gehört.

    Eine Quelle für Speicher-Schutz und Aufpasser (#460). ``None`` bei fehlender/kaputter Datei,
    fremdem ``pid``-Feld oder wenn ``procStart`` nicht zur Startzeit des Prozesses passt (alte
    Datei einer wiederverwendeten PID). Ohne ``procStart`` (ältere Claude-Versionen) gilt die Datei.
    ``sessions``/``proc`` ohne Angabe: ``TO_SPAWN_SPEICHER_SESSIONS`` bzw. ``~/.claude/sessions``
    und ``TO_SPAWN_SPEICHER_PROC`` bzw. ``/proc``. Ob der Prozess lebt, prüft der Aufrufer.
    """
    datei = (sessions if sessions is not None else _sessions_pfad()) / f"{pid}.json"
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(roh, dict):
        return None
    try:
        if int(roh.get("pid") or pid) != pid:
            return None
    except (TypeError, ValueError):
        return None
    start = roh.get("procStart")
    if start is not None:
        stat = _stat(pid, proc)
        if stat is None or str(stat[1]) != str(start):
            return None
    return roh


def _ist_still(eintrag: Path) -> bool:
    """Meldet die Session-JSON des Prozesses ``status: idle``? Keine/fremde JSON → nein."""
    roh = session_json_lesen(int(eintrag.name), proc=eintrag.parent)
    return roh is not None and roh.get("status") == "idle"


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


def _claude_eintraege(ordner: Path) -> list[Path] | None:
    """``/proc``-Einträge aller interaktiven Bau-Sessions (``claude`` und ``agy``, ohne ``-p``)."""
    try:
        eintraege = list(ordner.iterdir())
    except OSError:
        return None
    treffer = []
    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        try:
            roh = (eintrag / "cmdline").read_bytes()
        except OSError:
            continue  # Prozess schon weg oder nicht lesbar — zählt nicht
        teile = roh.split(b"\0")
        argv0 = teile[0].decode("utf-8", errors="replace")
        if not werkzeug.ist_session_programm(argv0, windows=False):
            continue
        if b"-p" in teile[1:] or b"--print" in teile[1:]:
            continue  # Einmal-Helfer (``claude -p`` / ``agy -p``) — keine eigene Sitzung
        treffer.append(eintrag)
    return treffer


def claude_sessions(
    proc: Path | None = None, sitzung: Sitzung | None = None, *, mit_stillen: bool = False
) -> int | None:
    """Zahl der arbeitenden Claude-Prozesse (``argv[0]``-Basename ``claude``), ``None`` = unbekannt.

    Stille Sessions und Anzeige-Fenster zählen nicht (#460, Regel im Modul-Kopf);
    ``mit_stillen=True`` zählt sie mit (Gesamtgrenze, :func:`gesamt_grenze`).
    ``proc`` = Ordner im Aufbau von ``/proc`` (Unterordner je PID mit ``cmdline``, ``environ``);
    ohne Angabe gilt ``TO_SPAWN_SPEICHER_PROC`` bzw. ``/proc``. ``sitzung`` = nur Prozesse
    dieser tmux-Sitzung zählen (#409); ``None`` = alle der Maschine.
    """
    eintraege = _claude_eintraege(proc if proc is not None else _proc_pfad())
    if eintraege is None:
        return None
    anzahl = 0
    for eintrag in eintraege:
        if sitzung is not None and not _gehoert_zu(eintrag, sitzung):
            continue  # fremde Session — bremst nur über min_frei_mib (#409)
        if not mit_stillen and (_ist_anzeige(eintrag) or _ist_still(eintrag)):
            continue  # Anzeige / still — bremst über min_frei_mib und die Gesamtgrenze (#460)
        anzahl += 1
    return anzahl


def laufende_sessions(proc: Path | None = None, *, mit_vergebenen: bool = True) -> int | None:
    """Zahl der arbeitenden Bau-Sessions auf dem ganzen Rechner (#760).

    Nutzt :func:`claude_sessions` (``claude`` und ``agy`` ohne ``-p``, ohne Anzeige und
    stille Sessions) und zählt bei ``mit_vergebenen=True`` noch vergebene Start-Plätze mit.
    """
    anzahl = claude_sessions(proc=proc)
    if not mit_vergebenen:
        return anzahl
    return _mit_vergebenen(anzahl, None)


def _sitzung_text(sitzung: Sitzung | None) -> str:
    return ",".join(sitzung) if sitzung else ""


def _vorfahren_von_claude(proc: Path) -> set[int]:
    """PIDs aller Vorfahren sichtbarer Claude-Prozesse (Starter, Sandbox-Hülle …)."""
    vorfahren: set[int] = set()
    for eintrag in _claude_eintraege(proc) or []:
        pid = int(eintrag.name)
        for _ in range(64):  # Schutz gegen Kreise in kaputten /proc-Daten
            stat = _stat(pid, proc)
            if stat is None or stat[0] <= 1 or stat[0] in vorfahren:
                break
            pid = stat[0]
            vorfahren.add(pid)
    return vorfahren


def _vergeben(jetzt: float) -> list[dict[str, Any]]:
    """Noch gültige vergebene Plätze (Regel bei :data:`RESERVIERUNG_S`); Fehler → keine.

    Eintrag ``{"bis": <Zeit>, "sitzung": "<socket>,<nr>", "pid": <Starter>, "start": <starttime>,
    "seit": <Zeit>}``; ``pid``/``start``/``seit`` fehlen in alten Einträgen (dann gilt nur ``bis``).
    """
    try:
        roh = json.loads(_platz_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(roh, list):
        return []
    plaetze = [p for p in roh if isinstance(p, dict) and isinstance(p.get("bis"), (int, float))]
    if not plaetze:
        return []
    proc = _proc_pfad()
    vorfahren = _vorfahren_von_claude(proc) if any(isinstance(p.get("pid"), int) for p in plaetze) else set()
    gueltig = []
    for p in plaetze:
        pid, start, seit = p.get("pid"), p.get("start"), p.get("seit")
        stat = _stat(pid, proc) if isinstance(pid, int) and isinstance(start, int) else None
        if stat is not None and stat[1] == start and isinstance(seit, (int, float)):  # Starter lebt
            if pid in vorfahren:
                continue  # Claude ist da und zählt selbst
            if jetzt < seit + RESERVIERUNG_MAX_S:  # Claude kommt noch
                gueltig.append(p)
        elif p["bis"] > jetzt:
            gueltig.append(p)
    return gueltig


def _vergebene_zahl(sitzung: Sitzung | None) -> int:
    """Gültige vergebene Plätze — in ``sitzung`` bzw. (``None``) auf dem ganzen Rechner."""
    plaetze = _vergeben(time.time())
    if sitzung is None:
        return len(plaetze)
    return sum(p.get("sitzung") == _sitzung_text(sitzung) for p in plaetze)


def _mit_vergebenen(anzahl: int | None, sitzung: Sitzung | None) -> int | None:
    return None if anzahl is None else anzahl + _vergebene_zahl(sitzung)


def _platz_vergeben(sitzung: Sitzung | None) -> None:
    """Einen Platz für diesen Starter-Prozess belegen (nur unter der Platz-Sperre rufen).

    Ein älterer Platz desselben Starters (Kette: nächstes Ticket) wird ersetzt, nie gestapelt.
    """
    jetzt = time.time()
    eigen = os.getpid()
    stat = _stat(eigen)
    neu_platz: dict[str, Any] = {"bis": jetzt + RESERVIERUNG_S, "sitzung": _sitzung_text(sitzung)}
    if stat is not None:  # ohne Startzeit kein sicherer PID-Abgleich → nur die feste Frist
        neu_platz.update(pid=eigen, start=stat[1], seit=jetzt)
    plaetze = [*(p for p in _vergeben(jetzt) if p.get("pid") != eigen), neu_platz]
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
    frei, grund, _fremd = _urteil_art(konfig, frei, sessions, welche, wo)
    return frei, grund


def _urteil_art(
    konfig: dict[str, Any] | None,
    frei: int | None,
    sessions: int | None,
    welche: Callable[[], Sitzung | None],
    wo: str | None,
) -> tuple[bool, str, bool]:
    """:func:`_urteil` plus ``fremd``: „voll“ nur wegen der rechnerweiten Grenzen (#460).

    ``fremd`` ist wahr, wenn RAM und eigene Sitzung Platz haben, aber Sessions außerhalb der
    Sitzung die Rechner- oder Gesamtgrenze füllen — dann wartet ein Starter, der selbst wartet.
    """
    linux = sys.platform == "linux"
    werte = grenzen(konfig)
    if frei is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_MEMINFO")):
        frei = frei_mib()
    # Rechner-weit, nur mit Sitzung gemessen: arbeitende (Rechner-Grenze) und alle (Gesamtgrenze).
    rechner: int | None = None
    alle: int | None = None
    if sessions is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_PROC")):
        sitzung = welche()
        sessions = _mit_vergebenen(claude_sessions(sitzung=sitzung), sitzung)
        if sitzung is None:
            wo = None  # keine/unbekannte Sitzung → der ganze Rechner zählt gegen max_sessions
        else:
            # Fremde arbeitende Sessions zählen bewusst hart mit (#460): der Rechner-Speicher ist
            # einer für alle Specs, eine Sitzung allein schützt nicht vor dem OOM-Kill (21.09.).
            rechner = _mit_vergebenen(claude_sessions(), None)
            alle = _mit_vergebenen(claude_sessions(mit_stillen=True), None)
    if frei is None and sessions is None:
        grund = "/proc nicht lesbar" if linux else "kein Linux"
        return True, f"Speicher: kein Urteil ({grund}) — Start erlaubt", False
    wo_text = f" in {wo}" if wo else ""
    if frei is not None and frei < werte["min_frei_mib"]:
        return False, f"Speicher knapp: {_gb_text(frei)} frei, Mindestmaß {_gb_text(werte['min_frei_mib'])}", False
    if sessions is not None and sessions >= werte["max_sessions"]:
        return False, f"schon {sessions} Claude-Sessions{wo_text} (Obergrenze {werte['max_sessions']})", False
    rechner_text = ""
    if rechner is not None:
        grenze = rechner_grenze(gesamt_kib())
        if rechner >= grenze:
            return False, f"Rechner voll: schon {rechner} Claude-Sessions auf dem Rechner (Obergrenze {grenze})", True
        rechner_text = f", Rechner {rechner} von {grenze}"
    if alle is not None:
        obergrenze = gesamt_grenze(gesamt_kib())
        if alle >= obergrenze:
            return (
                False,
                (
                    f"{GESAMT_VOLL_TEXT}: schon {alle} Claude-Sessions insgesamt "
                    f"(still + arbeitend, Obergrenze {obergrenze})"
                ),
                True,
            )
        rechner_text += f", insgesamt {alle} von {obergrenze}"
    frei_text = _gb_text(frei) if frei is not None else "unbekannt"
    sessions_text = str(sessions) if sessions is not None else "unbekannt"
    return (
        True,
        (
            f"Speicher frei: {frei_text} frei, {sessions_text} Claude-Sessions{wo_text} "
            f"(Obergrenze {werte['max_sessions']}{rechner_text}, Mindestmaß {_gb_text(werte['min_frei_mib'])})"
        ),
        False,
    )


def _pruefen_und_belegen(
    konfig: dict[str, Any] | None,
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]],
    sitzung: Sitzung | None,
) -> tuple[bool, str]:
    """``pruefen`` und bei „frei“ :func:`_platz_vergeben` unter der Platz-Sperre (#460).

    Sperre nicht öffenbar → ohne Sperre weiter (lieber starten als grundlos blockieren, Warnung);
    nach :data:`SPERRE_WARTEN_S` noch belegt → diesmal „nicht frei“, der nächste Zyklus versucht es neu.
    """
    try:
        with sperre.halte(_platz_pfad().with_suffix(".sperre"), warten=SPERRE_WARTEN_S) as gehalten:
            if not gehalten:
                return False, "Platz-Vergabe gerade belegt (anderer Start)"
            frei, grund = pruefen(konfig)
            if frei:
                _platz_vergeben(sitzung)
            return frei, grund
    except OSError as fehler:
        log.warning("Platz-Sperre nicht nutzbar (%s) — prüfe ohne Sperre", fehler)
        return pruefen(konfig)


GESAMT_VOLL_TEXT = "Rechner voll mit stillen Sessions"  # #624: Anlass fürs Platz-Schaffen


def _platz_schaffen() -> object:
    """Bittet den Aufpasser, fertige stille Fenster zu schließen (#624). Fehler ⇒ nur Log."""
    from to_spawn import aufpasser  # spät: aufpasser importiert speicher

    try:
        return aufpasser.platz_schaffen()
    except Exception:  # Warten darf daran nie scheitern
        log.exception("Platz schaffen gescheitert")
        return None


def auf_platz_warten(
    konfig: dict[str, Any] | None,
    *,
    wer: str = "Session",
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]] = platz_frei,
    schlafen: Callable[[float], object] = time.sleep,
    takt_s: int = 60,
    meldung_alle: int = 5,
    platz_schaffen: Callable[[], object] = _platz_schaffen,
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
        if not frei and GESAMT_VOLL_TEXT in grund:
            # #624: stille fertige Fenster belegen die Gesamtgrenze — Aufpasser schließt sie
            # jetzt (er allein kennt die Regeln), dann neu prüfen statt auf den Cron zu warten.
            try:
                platz_schaffen()
            except Exception:  # Test-/Rückruf-Fehler: weiterwarten
                log.exception("Platz schaffen gescheitert")
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


def _platte_pruefen(loeschen: bool = True, nur_messen: bool = False) -> tuple[bool, str]:
    """Start-Check der Platte — die Entscheidung trifft der Hausmeister (#1369).

    ``loeschen=False``: nur messen (Probelauf, Status), nie aufräumen.
    ``nur_messen=True``: kein Rundgang (#617), nur Urteil aus der Messung.
    """
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    return hausmeister.start_erlaubt(loeschen=loeschen, nur_messen=nur_messen)


AUFRAEUM_ABSTAND_S = 600  # #617: so lange gilt „dieser Start hat schon geräumt“


def _stempel(sitzung: str) -> Path:
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in sitzung)
    return hausmeister._log_pfad().parent / f"start-geraeumt-{name}.stempel"


def _platte_je_start(sitzung: str | None) -> tuple[bool, str]:
    """Hausmeister räumt höchstens einmal je Start (#617): hat diese Sitzung vor kurzem
    geräumt (Stempel jünger als :data:`AUFRAEUM_ABSTAND_S`), misst die Prüfung nur und
    räumt erst bei „voll“ erneut. Stempel unlesbar/unschreibbar ⇒ wie bisher räumen."""
    if sitzung:
        try:
            stempel = _stempel(sitzung)
            if 0 <= time.time() - stempel.stat().st_mtime < AUFRAEUM_ABSTAND_S:  # Zukunft = Uhr-Sprung
                ok, grund = _platte_pruefen(nur_messen=True)
                if ok:
                    return ok, grund
        except OSError:
            pass
    ergebnis = _platte_pruefen()
    if sitzung:
        try:
            stempel = _stempel(sitzung)
            stempel.parent.mkdir(parents=True, exist_ok=True)
            stempel.touch()
        except OSError:
            log.warning("Platte: Stempel für %s nicht schreibbar", sitzung)
    return ergebnis


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
    3 = voll nur durch Sessions außerhalb der Sitzung (:data:`EXIT_FREMD_VOLL`, #460), 7 = Platte voll (Start-Check des Hausmeisters, vor der RAM-Prüfung).

    ``--staffel`` druckt nur die Sekundenzahl der Staffel (für Shell-Skripte).
    ``--trocken`` (Probelauf, Status): nur messen, der Hausmeister löscht nie.
    ``--sitzung spec-<S>``: Obergrenze nur für diese tmux-Sitzung (#409).
    """
    if nur_staffel:
        print(staffel_s(konfig))
        return 0
    platte_ok, platte_grund = _platte_pruefen(loeschen=False) if trocken else _platte_je_start(sitzung)
    if not platte_ok:
        print(platte_grund)
        return EXIT_PLATTE
    ok, grund, fremd = _urteil_art(konfig, None, None, lambda: sitzung_nach_name(sitzung) if sitzung else None, sitzung)
    print(grund)
    if ok:
        return 0
    return EXIT_FREMD_VOLL if fremd else EXIT_VOLL
