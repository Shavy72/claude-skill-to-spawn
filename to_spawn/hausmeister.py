"""Hausmeister (#1368): räumt alte Arbeitsordner (Worktrees, Klone) sicher weg.

Eine Tür: :func:`aufraeumen` entscheidet für EINEN Ordner nach allen Lösch-Regeln
und löscht nur mit ``loeschen=True``. Alle Regeln leben hier — Aufrufer (CLI, Cron)
kennen keine davon. Grundsatz: im Zweifel bleibt der Ordner. Die Tür wirft nie;
jeder Fehler ergibt ``art="bleibt"`` mit dem Fehler als Grund und ``fehler=True``
(„bleibt“ aus einer Regel hat ``fehler=False``; die CLI endet nur bei Fehlern ≠ 0).

Reihenfolge (erste zutreffende Regel gewinnt), Vertrag ``docs/verify-hard/1368/API.md``:
Aufräum-Bereich (aufgelöster Pfad, Symlink bleibt) → Hauptrepo → eigenes Repo →
Ticket-Nummer → Ticket zu → 24 h → keine tmux-Sitzung ``spec-<N>`` → kein Prozess im
Ordner (cwd, exe, offene Dateien) → Prüfung des Ungesicherten → Sicherung → Gegenprobe →
Log → löschen. Vorab: Namen aus :data:`GESCHUETZTE_ORDNER` bleiben immer.

#1536 — Behalten ist die Ausnahme mit echtem Grund. Ordner ohne Ticket-Nummer und Ordner
ganz ohne Git (kein ``.git`` oder verwaister Worktree, dessen Hauptrepo fehlt) bleiben
immer, ``fuer_david=True`` — ohne Git gibt es nichts, wohin sie sich sichern ließen.
Unterordner eines fremden Repos und unlesbare Git-Ordner bleiben. Je Takt schreibt :func:`aufraeumen_alles` eine Zeile
nach :data:`KENNZAHL_DATEI` neben dem Log („x von y gelöscht, z bleiben“, Bleib-Gründe
gezählt) — eigene Datei, damit Leser des Ordner-Logs nur Ordner-Zeilen sehen.

Ungesichert heißt: geänderte/neue Dateien oder Commits auf keinem Remote. Vorher
``git fetch --prune origin`` (veraltete Tracking-Refs täuschen „gepusht“ vor), dann
zählt nur ``--remotes=origin``. Beim Klon (``.git`` ist Ordner) zählen alle lokalen
Refs außer ``refs/remotes`` (Zweige, Tags, Notes) + HEAD, denn mit dem Klon sterben
sie alle; ein Stash bleibt. Beim verknüpften Worktree (``.git`` ist Datei) nur HEAD:
die übrigen Refs gehören dem Hauptrepo und überleben das ``worktree remove`` — sie
dort mitzusichern hieße fremde Arbeit als „Sicherung #N“ hochzuladen. Den lokalen Zweig
des Worktrees räumt :func:`_loeschen` danach über die Zweig-Tür (#1439) im Hauptrepo
weg, nach deren Regeln. Gesichert wird über :func:`to_spawn.aufpasser.sichern` (ohne
``SICHERUNG_AUSSCHLUSS``).

Dateien, die die Sicherung NICHT mitnimmt, gehen beim Löschen verloren — darum:
- Geheimnisse (``SICHERUNG_AUSSCHLUSS``, z. B. ``.env*``, ``*.key``), geändert, neu
  oder ignoriert: dürfen nur weg, wenn das Hauptrepo des Worktrees dieselbe Datei mit
  gleichem Inhalt hat; sonst (und beim Klon immer) bleibt, ``fuer_david=True``.
  Geheimnisse in ungepushten Commits würde die Sicherung mit hochladen ⇒ bleibt, ``fuer_david=True``.
- Ignorierte Dateien: nur bekannte Erzeugnisse (:data:`ERZEUGNISSE`) dürfen weg,
  jede andere ignorierte Datei ⇒ bleibt.
- Datenbanken (``aufpasser.NIE_HOCHLADEN``) lädt die Sicherung nie hoch: weg dürfen nur
  kleine Test-Kopien (:func:`ist_test_db`), jede andere ⇒ bleibt, ``fuer_david=True``.
- Eingebettete Repos und Submodule (Sicherung sähe nur den Gitlink) ⇒ bleibt.

Prozesse kommen aus ``/proc``; unter Windows (Laptop, ``C:/dev/wt-*``, #1373) aus
``Get-CimInstance Win32_Process`` plus einer Umbenennungs-Probe nur im echten Lauf
(Windows-Wissen in :mod:`to_spawn.hausmeister_windows`, Tür bleibt hier). Plattenplatz
misst die Platte der Wurzeln, nicht Home. Altbestand (#1373, E16): Ordner in
:func:`altbestand_pfad` bleiben im echten Lauf, bis der Mensch die Liste freigibt; der
Trockenlauf zeigt die echte Regel. Fehlt die Liste unter Windows, hält der erste echte
Lauf zuerst alle vorhandenen Ordner fest (am Server: keine Wirkung).

Jede Entscheidung schreibt eine JSON-Zeile nach ``TO_SPAWN_HAUSMEISTER_LOG``
(Standard ``~/.claude/to-spawn/hausmeister.jsonl``), beim Löschen VOR dem Löschen;
Log nicht schreibbar ⇒ bleibt. Scheitert danach das Löschen, folgt eine zweite Zeile
„bleibt“. :func:`liste_fuer_david` liest daraus die Ordner, über die der Mensch selbst
entscheiden muss.

Zweite Tür (#1369): :func:`aufraeumen_alles` wendet dieselben Regeln auf alle Ordner
unter den Wurzeln an. Platznot (weniger als :data:`PLATZNOT_ANTEIL` der Platte frei):
Ordner mit geschlossenem Ticket gehen sofort, auch vor Ablauf der 24 h — älteste
Schließung zuerst, nach jeder Löschung neu gemessen; alle übrigen Regeln gelten
unverändert. :func:`start_erlaubt` ist der Start-Check (``to_spawn.py speicher``,
``bau.py``): unter :data:`STARTSPERRE_ANTEIL` frei startet nichts Neues, eine Zeile geht
ins Aufpasser-Log (``cron.out``) und ins Hindernis-Log. Die Grenzen leben nur hier.
Test-Naht ``TO_SPAWN_HAUSMEISTER_PLATTE``: Anteil frei (Zahl) oder Datei mit einem
Anteil je Zeile (jede Messung nimmt den nächsten, der letzte bleibt).
Platte nicht messbar ⇒ Start erlaubt (im Zweifel starten), aber mit Warnung, Zeile in
``cron.out`` und Hindernis ``platte_unmessbar``. Parallele :func:`aufraeumen_alles`-Läufe
schließt eine nicht blockierende Sperre (:data:`SPERR_DATEI` neben dem Log) aus; wer sie
nicht bekommt, räumt nicht auf. gh-Abfragen enden nach :data:`GH_ZEITLIMIT_S` (git hat
300 s in :func:`aufpasser.git_lauf`); Zeitüberschreitung ⇒ bleibt mit Fehler.

Dritte Tür (#1371, E41): :func:`sicherungen_aufraeumen` löscht Zweige
``sicherung/<Zahl>`` auf den eigenen GitHub-Repos 90 Tage (:data:`SICHERUNG_TAGE`)
nach dem Anlegen — andere Zweige nie. Angelegt = ältester Aufpasser-Sicherungs-Commit
der Kette (Betreff „Aufpasser: Sicherung #<N> …“); fehlt er, bleibt der Zweig.
Auch die jüngste Sicherung der Kette muss 90 Tage alt sein: sichert der Aufpasser ein
langes Ticket noch, bleibt der Zweig (sonst Lösch-Schleife, der Aufpasser legt ihn mit
der alten Kette sofort neu an).
Gelöscht wird nur mit Lease auf den gelesenen Stand (neue Sicherung dazwischen ⇒
bleibt). Läuft im Takt von :func:`aufraeumen_alles` mit, höchstens einmal täglich je
Modus (Trockenlauf verbraucht den Tag des echten Laufs nicht). Gelesen wird über einen
Zwischenspeicher nur mit Commits (``--filter=tree:0``) neben dem Log. Jede Löschung =
eine JSON-Zeile im Hausmeister-Log (Feld ``zweig``), vor dem Löschen. Test-Naht
``TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS``: Remote-URLs (Leerzeichen-getrennt, leer = keine).

Vierte Tür (#1439): :func:`zweig_aufraeumen` räumt einen fertigen Zweig ``ticket/<N>-…``
weg — auf dem Remote (Vorgabe) oder lokal im Hauptrepo; immer sichern → prüfen →
löschen. Fertig (:func:`zweig_fertig`, einzige Stelle): die Schlange hat genau diesen
Stand übernommen (``uebernommen_tip``, dann fehlt nichts — nach ihrem Rebase stehen
dieselben Änderungen unter anderen SHAs auf master) oder Ticket #N ist zu; fehlend =
Commits weder auf ``origin/master`` noch auf einem ``sicherung/*``-Zweig, nie
``git branch --merged``. Fehlendes geht erst über :func:`aufpasser.sichern` (``kopf=``,
ohne Arbeitsdateien) nach ``sicherung/<N>``, Gegenprobe, dann Remote-Löschen mit Lease
auf den geprüften Stand bzw. lokal ``branch -D`` (nie, wenn in einem Worktree
ausgecheckt). Ohne Ticket-Nummer bleibt der Zweig und steht in :func:`liste_fuer_david`.
Remote-Zweige liest der Zwischenspeicher der Sicherungs-Zweige. :func:`zweige_aufraeumen`
läuft im Takt von :func:`aufraeumen_alles` höchstens täglich je Modus: je Hauptrepo
``fetch --prune`` + ``worktree prune`` (nur echt), lokale ``ticket/*`` ohne Arbeitsordner,
dann alle ``ticket/*`` der Sicherungs-Remotes. Test-Naht ``TO_SPAWN_HAUSMEISTER_HAUPTREPOS``
(Pfade, Leerzeichen-getrennt, leer = keine). :func:`zweige_probelauf` schreibt die Liste
für den Menschen, ohne etwas zu ändern.

Fünfte Tür (#1374): :func:`halb_angelegt_entfernen` entfernt für den Anleger
(``nest.worktree_anlegen``) einen eben angelegten Worktree, dessen Füllen scheiterte —
ohne die Lösch-Regeln oben, denn er trägt noch keine Arbeit; nur Worktrees, die im
übergebenen Hauptrepo eingetragen sind. Ausnahme zu „Log nicht schreibbar ⇒ bleibt“:
hier wird trotzdem entfernt, ein stehender leerer Ordner wäre schädlicher. Die
Zeile „gelöscht“ folgt hier erst nach dem Entfernen (je Ordner eine Zeile).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from to_spawn import (
    aufpasser,
    config,
    docker_reste,
    gh,
    hausmeister_windows,
    hindernis,
    ticket_zustand,
    uebergabe_sichern,
)
from to_spawn import sperre as _sperre

log = logging.getLogger("to_spawn.hausmeister")

Art = Literal["gelöscht", "gesichert und gelöscht", "bleibt"]


# --- Maschinen-Liste (L1 Etappe 1) ------------------------------------------------
# Repos, Wurzeln und Ordner-Präfixe kommen aus ``config.maschine()``; fehlt sie oder ist sie
# kaputt, gelten ``config.MASCHINE_DEFAULTS`` (= das alte Verhalten). Env-Nähte
# ``TO_SPAWN_HAUSMEISTER_*`` gewinnen. Oberste Regel: nie mehr löschen als vorher —
# Schutzlisten wachsen nur, Unklares heißt „bleibt“. Die Konstanten unten sind die Vorgaben
# (Aliase für Aufrufer und Tests), wirksam sind die Funktionen ``_eigene_repos()`` usw.


def _ticket_repo_aus(repos: list[dict[str, str]]) -> str | None:
    """Das eine Ticket-Repo aller Einträge; ``None`` = keins, mehrere oder unklar.

    Nie aus dem ``slug`` geraten: ein falsches Repo könnte ein fremdes geschlossenes #N
    melden. Leeres ``ticket_repo`` gilt nur, wenn der ``slug`` selbst das ausdrückliche
    Ticket-Repo eines anderen Eintrags ist (Vorgabe: DuoPlus, Skill-Ordner fragen dort).
    """
    explizit = {t for e in repos if (t := (e.get("ticket_repo") or "").strip())}
    if _ohne_ticket_repo(repos):
        return None
    return next(iter(explizit)) if len(explizit) == 1 else None


def _ohne_ticket_repo(repos: list[dict[str, str]]) -> list[dict[str, str]]:
    """Einträge ohne brauchbares Ticket-Repo: kein ``ticket_repo`` und der ``slug`` ist nicht
    das ausdrückliche Ticket-Repo eines Eintrags (z. B. Wegwerf-Klon, #112)."""
    bekannt = {t.lower() for e in repos if (t := (e.get("ticket_repo") or "").strip())}
    return [
        e
        for e in repos
        if not (e.get("ticket_repo") or "").strip() and (e.get("slug") or "").strip().lower() not in bekannt
    ]


def _hauptrepo_namen_aus(repos: list[dict[str, str]]) -> tuple[str, ...]:
    return tuple(Path(p).expanduser().name for e in repos if (p := (e.get("pfad") or "").strip()))


def _remotes_aus(repos: list[dict[str, str]]) -> tuple[str, ...]:
    return tuple(f"https://github.com/{s}" for e in repos if (s := (e.get("slug") or "").strip()))


_VORGABE_REPOS: list[dict[str, str]] = config.MASCHINE_DEFAULTS["repos"]
#: Gemeinsames Ticket-Repo aller Vorgabe-Einträge; seit #235 leer (jedes Repo hat eigene Tickets).
TICKET_REPO = _ticket_repo_aus(_VORGABE_REPOS) or ""
EIGENE_REPOS = tuple(e["slug"].lower() for e in _VORGABE_REPOS if e.get("slug"))
#: Namen der Haupt-Arbeitskopien — nie aufräumen.
HAUPTREPO_NAMEN = _hauptrepo_namen_aus(_VORGABE_REPOS)
#: #1536: Ordnernamen, die immer bleiben (Server und PC), auch ohne Git — dazu kommen die
#: Hauptrepos der Maschinen-Liste (:func:`_geschuetzte_ordner`). Laufende Spec-Klone schützen
#: offenes Ticket und tmux-Sitzung ``spec-<N>``.
GESCHUETZTE_ORDNER = (*HAUPTREPO_NAMEN, *config.HAUSMEISTER_GESCHUETZTE_ORDNER)
#: #1536: Kennzahl je Takt, eigene Datei neben dem Hausmeister-Log (:func:`_kennzahl_pfad`).
KENNZAHL_DATEI = "hausmeister_kennzahl.jsonl"
WURZELN_STANDARD = tuple(config.MASCHINE_DEFAULTS["hausmeister"]["wurzeln"])
ORDNER_PRAEFIXE_STANDARD = tuple(config.MASCHINE_DEFAULTS["hausmeister"]["ordner_praefixe"])


def _maschinen_repos() -> list[dict[str, str]]:
    return config.maschine()["repos"]


def _ticket_repo() -> str | None:
    """Repo für ``gh issue view``; ``None`` = keins oder nicht eindeutig (⇒ Ticket nicht abfragbar).

    Nur noch Rückfall ohne GitHub-origin; je Ordner/Zweig gilt :func:`_ticket_repo_fuer` (#176).
    """
    return _ticket_repo_aus(_maschinen_repos())


def _ticket_repo_fuer(slug: str) -> str | None:
    """Ticket-Repo für einen Ordner mit origin ``slug`` (#112, #176); ``None`` ⇒ nicht bestimmbar.

    Einzige Antwort je Ordner: das ``ticket_repo`` des Eintrags mit diesem ``slug``. Leer gilt
    nur, wenn ``slug`` selbst das ausdrückliche Ticket-Repo eines Eintrags ist (nie geraten).
    Kein Eintrag, leerer ``slug`` oder Eintrag ohne Ticket-Repo ⇒ ``None``; mehrere Repos mit
    eigenen Tickets stören sich nicht. Einträge ohne Ticket-Repo werden einmal gemeldet.
    """
    repos = _maschinen_repos()
    for e in _ohne_ticket_repo(repos):
        _kaputten_eintrag_melden(e)
    gesucht = slug.strip().lower()
    if not gesucht:
        return None
    bekannt = {t.lower(): t for e in repos if (t := (e.get("ticket_repo") or "").strip())}
    treffer = {
        (e.get("ticket_repo") or "").strip() or bekannt.get(gesucht)
        for e in repos
        if (e.get("slug") or "").strip().lower() == gesucht
    }
    # doppelter Eintrag mit verschiedenem Ticket-Repo ⇒ unklar ⇒ bleibt
    return next(iter(treffer)) if len(treffer) == 1 else None


def _kaputten_eintrag_melden(eintrag: dict[str, str]) -> None:
    schluessel = f"ohne-ticket-repo:{eintrag.get('pfad')}:{eintrag.get('slug')}"
    if schluessel not in _ABGELEHNT_GEMELDET:
        _ABGELEHNT_GEMELDET.add(schluessel)
        log.warning(
            "Hausmeister: Eintrag slug=%r pfad=%r in %s hat kein Ticket-Repo — übersprungen, "
            "seine Ordner bleiben. Beheben: ticket_repo eintragen oder Eintrag entfernen (Wegwerf-Klon)",
            eintrag.get("slug"),
            eintrag.get("pfad"),
            config.maschine_datei(),
        )


def _eigene_repos() -> frozenset[str]:
    """Slugs (klein), deren Ordner der Hausmeister aufräumen darf."""
    return frozenset(s.lower() for e in _maschinen_repos() if (s := e["slug"].strip()))


def _hauptrepo_namen() -> tuple[str, ...]:
    """Vorgaben plus Maschinen-Liste: eine kürzere Liste hieße mehr löschen."""
    return tuple(dict.fromkeys((*HAUPTREPO_NAMEN, *_hauptrepo_namen_aus(_maschinen_repos()))))


def _geschuetzte_ordner() -> tuple[str, ...]:
    """Eingebaute Liste + Hauptrepos + ``hausmeister.geschuetzte_ordner`` — nur addierend."""
    roh = config.maschine()["hausmeister"].get("geschuetzte_ordner")
    extra = [n.strip() for n in roh if isinstance(n, str) and n.strip()] if isinstance(roh, list) else []
    return tuple(dict.fromkeys((*GESCHUETZTE_ORDNER, *_hauptrepo_namen(), *extra)))


MINDEST_ZU_SEKUNDEN = 24 * 3600
#: Bleib-Grund (und Lösch-Grund-Anfang) für Ordner ohne Ticket-Nummer (#1536, #235).
OHNE_NUMMER = "keine Ticket-Nummer"
#: Unter diesem Anteil freier Platte gilt Platznot: fertige Ordner gehen vor Ablauf der 24 h.
PLATZNOT_ANTEIL = 0.15
#: #1491: Zweige löscht der Hausmeister nur, wenn diese Variable genau „1“ ist (:func:`zweige_loeschen_an`).
ZWEIGE_SCHALTER = "TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN"
#: Grund/Log-Text, wenn der Schalter fehlt (eine Formulierung für Takt, Zweig-Tür und Senke).
ZWEIGE_AUS = f"Zweig-Löschen aus ({ZWEIGE_SCHALTER}=1 fehlt)"
#: Unter diesem Anteil freier Platte startet keine neue Session.
STARTSPERRE_ANTEIL = 0.05
#: Sperrdatei gegen parallele Aufräum-Läufe, im Ordner des Hausmeister-Logs.
SPERR_DATEI = "hausmeister.lock"
#: Zeitlimit je gh-Abfrage in Sekunden.
GH_ZEITLIMIT_S = 60
# Plattform-Naht (Tests stellen "win32" ein); nur _ist_windows() liest sie
_PLATTFORM = sys.platform
#: Ignorierte Dateien, die beim Löschen mit weg dürfen (Git-Glob-Pathspecs).
ERZEUGNISSE = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/.to-spawn/**",
    "**/node_modules/**",
    "**/*.log",
    "**/.coverage",
    "**/htmlcov/**",
    "**/.review/**",  # Belege des Review-Werkzeugs (eigenes .gitignore mit *)
    "data/.secret_key",  # Flask-Sitzungsschlüssel, legt web/config.py selbst neu an
    # #1536: Laufzeit-Reste (Datenbanken nicht hier, sondern :func:`ist_test_db`).
    "**/*.lock",
    "**/*.pyc.[0-9]*",  # halb geschriebenes Kompilat (Windows)
    "logs/**",
    "graphify-out/**",
    # Fix-Runde 2: in den Daten-Ordnern nur Vorschaubilder — Uploads/Reels halten den Ordner.
    "data/content_studio/**/.thumb_*",
    "data/reel_street/**/.thumb_*",
)
#: #1536: Datenbanken, die als Test-Kopie weg dürfen — Pfad relativ zum Arbeitsordner
#: (samt ``-wal``/``-shm``). So legen App und Tests sie an (``account_db.DB_PATH`` u. a.).
TEST_DB_PFADE = ("accounts.db", "data/funnel.db", "data/rum.db", "web/test.db")
#: #1536: größer ist keine Test-Kopie mehr (Bau-Server 21.11.: größte 1,6 MB).
TEST_DB_GRENZE_BYTES = 50 * 1024 * 1024


def ist_test_db(repo: Path, pfad: str) -> bool:
    """#1536: einzige Regel, welche Datenbank-Datei (``aufpasser.NIE_HOCHLADEN``) Wegwerf-Ware
    ist: bekannter Pfad aus :data:`TEST_DB_PFADE` (auch ``-wal``/``-shm``) UND höchstens
    :data:`TEST_DB_GRENZE_BYTES` groß. Nicht messbar ⇒ keine Test-Ware (im Zweifel bleibt)."""
    for endung in ("-wal", "-shm"):
        if pfad.endswith(".db" + endung):
            pfad_db = pfad[: -len(endung)]
            break
    else:
        pfad_db = pfad
    if pfad_db not in TEST_DB_PFADE:
        return False
    try:
        return (repo / pfad).lstat().st_size <= TEST_DB_GRENZE_BYTES
    except OSError:
        return False


# #1526: Ticket-Nummer 1–4999 (8080, 20261121, 993675 sind Port/Datum/Hash, keine Tickets).
# ponytail: feste Obergrenze, anheben, wenn die Tickets an 4000 heranreichen.
_TICKET_NR = r"([1-9]\d{0,2}|[1-4]\d{3})"
# Ohne Präfix (nackt, t/a) nur 3–4 Ziffern: 1, 12, a7 sind keine Ticket-Ordner.
_TICKET_NR_LANG = r"([1-9]\d{2}|[1-4]\d{3})"
# Jahr-Monat am Namensende (wt-2026-11) ist ein Datum, keine Ticket-Form.
_KEIN_JAHR_MONAT = r"(?!20\d\d-(?:0?[1-9]|1[0-2])$)"


def _name_muster_fuer(praefixe: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Ticket-Formen im Ordnernamen; ``praefixe`` sind Text (kein Regex), leer = ohne Präfix-Form."""
    muster: list[re.Pattern[str]] = []
    if praefixe:
        # #1526: nur bekannte Formen, Name bis zum Ende verankert — im Zweifel keine Nummer.
        # wt-1292f, skill-1371-basis, wt-1313-3, wt-1375-7c: Präfix, Zahl, dann Ende, ein
        # Buchstabe, -<wort> oder kurzes -<1–2 Ziffern>[buchstabe]
        # (skill-4821f3c ist ein Commit-Hash-Rest, wt-2026-11-21 ein Datum, kein Ticket).
        wahl = "|".join(map(re.escape, praefixe))
        muster.append(
            re.compile(rf"^(?:{wahl})-{_KEIN_JAHR_MONAT}{_TICKET_NR}(?:[a-z]|-\d{{1,2}}[a-z]?|-[a-z][a-z0-9-]*)?$")
        )
    # t1490a, a1279 (Aufseher): nur t/a als Präfix (x500, v2026-backup sind keine Tickets)
    muster.append(re.compile(rf"^[ta]{_TICKET_NR_LANG}[a-z]?$"))
    # 1489, 1331-linux: nackte Zahl nur allein oder mit einem Wort aus enger Liste
    # (2026-11-21-sicherung, 8080-notizen, 805_fremd_backup sind keine Tickets).
    # Hinter einem Wort zählt sie nie: schlange-einspielen-1121 ist ein Datum.
    muster.append(re.compile(rf"^{_TICKET_NR_LANG}(?:-(?:linux|lauf|breit|master))?$"))
    return tuple(muster)


#: Vorgabe (Alias für Tests); wirksam ist :func:`_name_muster`.
_NAME_MUSTER = _name_muster_fuer(ORDNER_PRAEFIXE_STANDARD)


def _name_muster() -> tuple[re.Pattern[str], ...]:
    """Ticket-Formen mit den Ordner-Präfixen der Maschinen-Liste (``hausmeister.ordner_praefixe``)."""
    roh = config.maschine()["hausmeister"]["ordner_praefixe"]
    praefixe = tuple(p.strip() for p in roh if isinstance(p, str) and p.strip())
    return _NAME_MUSTER if praefixe == ORDNER_PRAEFIXE_STANDARD else _name_muster_fuer(praefixe)


#: #248/#347 W3: ``20JJ-<Ziffer>`` direkt nach dem Präfix ist datumsartig, nie eine Ticket-Nummer
#: (``l1/2026-11-21-stand``, ``fix/2026-11``, auch ``ticket/2026-13-x``) — ohne Nummer bleibt der Zweig.
_KEIN_DATUM = r"(?!20\d\d-\d)"
_ZWEIG_MUSTER = re.compile(rf"^(?:(?:ticket|fix|wip|aufseher)/|fix-|abschluss-){_KEIN_DATUM}{_TICKET_NR}(?:-.*)?$")
#: #235: Etappen-Zweig ``l<Etappe>/<N>[buchstabe]-…`` (``l4/176-…``, ``l1/214a-…``); erst ab
#: 2 Ziffern — ``l3/3b-1b-…`` ist ein Etappen-Schritt, kein Issue, ``l1/p7f-…`` ebenso.
_ZWEIG_MUSTER_ETAPPE = re.compile(rf"^l\d+/{_KEIN_DATUM}([1-9]\d{{1,2}}|[1-4]\d{{3}})[a-z]?(?:-.*)?$")
#: Alle Zweig-Formen mit Ticket-Nummer — die einzige Stelle „Zweigname → Nummer“ (#248, auch
#: für das Zweig-Aufräumen, :func:`_zweig_ticket`); welches Repo die Nummer meint, sagt allein
#: ``origin`` (:func:`_nummer_zaehlt`, im Ordner- wie im Zweig-Pfad, #234).
_ZWEIG_FORMEN = (_ZWEIG_MUSTER, _ZWEIG_MUSTER_ETAPPE)
#: #235 H1: nur diese drei Anfänge sind GitHub; ein fremder Host (``notgithub.com``) oder
#: ein lokaler Pfad mit ``github.com/…`` im Namen ist ein fremdes Repo (nur mit ``.match``).
_GITHUB_SLUG = re.compile(
    r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)([^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)
#: #235 N2: eine Zeile von ``git ls-remote --heads`` (SHA-1 oder SHA-256, Tab, Zweig).
_LS_REMOTE_ZEILE = re.compile(r"([0-9a-f]{40}(?:[0-9a-f]{24})?)\trefs/heads/\S+$")


@dataclass(frozen=True)
class Ergebnis:
    ordner: Path
    art: Art
    grund: str
    ticket: int | None
    sicherung: str | None = None
    fuer_david: bool = False
    trocken: bool = True
    fehler: bool = False  # True = „bleibt“ wegen eines Fehlers, nicht wegen einer Regel


class _Bleibt(Exception):
    """Eine Regel (oder ein Fehler) hält den Ordner fest."""

    def __init__(self, grund: str, *, fuer_david: bool = False, fehler: bool = False) -> None:
        super().__init__(grund)
        self.grund = grund
        self.fuer_david = fuer_david
        self.fehler = fehler


@dataclass(frozen=True)
class Rundgang:
    """Ein Lauf über alle Ordner (:func:`rundgang`).

    ``gelaufen=False`` heißt: es wurde gar nicht aufgeräumt, ``grund`` sagt in Klartext
    warum. ``wartet=True`` ist dabei kein Fehler — ein anderer Lauf hält die Sperre und
    räumt gerade selbst auf. ``fehler`` zählt Ordner, die wegen eines Fehlers blieben.
    """

    gelaufen: bool
    grund: str
    ergebnisse: tuple[Ergebnis, ...] = ()
    wartet: bool = False

    @property
    def fehler(self) -> int:
        return sum(1 for e in self.ergebnisse if e.fehler)

    @property
    def geloescht(self) -> int:
        """Gelöscht (im Probelauf: würde gelöscht)."""
        return sum(1 for e in self.ergebnisse if e.art != "bleibt")

    @property
    def bleibt(self) -> int:
        """Nach den Regeln geblieben (ohne Fehler)."""
        return sum(1 for e in self.ergebnisse if e.art == "bleibt" and not e.fehler)


@dataclass
class _Stand:
    ticket: int | None = None
    sicherung: str | None = None


def aufraeumen(ordner: Path, *, loeschen: bool = False, jetzt: float | None = None) -> Ergebnis:
    """Entscheidet (und löscht mit ``loeschen=True``) einen Arbeitsordner; wirft nie."""
    return _aufraeumen(ordner, loeschen=loeschen, jetzt=jetzt, platznot=False)


def aufraeumen_alles(*, loeschen: bool = False, jetzt: float | None = None) -> list[Ergebnis]:
    """Alle Ordner unter den Wurzeln nach den Regeln von :func:`aufraeumen`; wirft nie.

    Bei Platznot zuerst die Ordner mit der ältesten Ticket-Schließung, ohne 24 h
    Wartezeit, bis wieder genug frei ist (siehe Modul-Kommentar). Läuft schon ein
    anderer Lauf (Sperre), oder scheitert die Ordnersuche: ``[]`` — wer das
    unterscheiden muss, nimmt :func:`rundgang`.
    Mit ``loeschen=True`` gibt derselbe Lauf einmal täglich Docker-Reste frei
    (:mod:`to_spawn.docker_reste`, #1372); Probelauf und Start-Check nie.
    """
    return list(_rundgang_sicher(loeschen=loeschen, jetzt=jetzt, docker=loeschen).ergebnisse)


def rundgang(*, loeschen: bool, jetzt: float | None = None) -> Rundgang:
    """Wie :func:`aufraeumen_alles`, sagt aber, ob überhaupt aufgeräumt wurde; wirft nie.

    Für Aufrufer, die „erledigt“ festhalten (capo-Schlussschritt): belegte Sperre,
    gescheiterte Ordnersuche und jeder unerwartete Fehler ergeben ``gelaufen=False``
    mit Klartext-Grund statt einer leeren Liste, die wie „nichts zu tun“ aussieht.
    """
    return _rundgang_sicher(loeschen=loeschen, jetzt=jetzt, docker=False)


def _rundgang_sicher(*, loeschen: bool, jetzt: float | None, docker: bool) -> Rundgang:
    """Kern von :func:`rundgang`; ``docker=True`` nur aus :func:`aufraeumen_alles`."""
    try:
        return _rundgang(loeschen=loeschen, jetzt=jetzt, docker=docker)
    except Exception as fehler:  # Vertrag: wirft nie
        log.exception("Hausmeister: Rundgang gescheitert")
        return Rundgang(False, f"unerwarteter Fehler ({type(fehler).__name__}: {fehler})")


def _rundgang(*, loeschen: bool, jetzt: float | None, docker: bool) -> Rundgang:
    try:
        sperre = _sperre_nehmen()
    except OSError as fehler:
        log.warning("Hausmeister: Sperre nicht nutzbar (%s) — laufe ohne", fehler)
        sperre = None
    if sperre is False:
        log.info("Hausmeister: anderer Lauf läuft schon — dieser räumt nicht auf")
        return Rundgang(False, "ein anderer Lauf räumt gerade auf (Sperre belegt)", wartet=True)
    try:
        ergebnis = _alle_ordner(loeschen=loeschen, jetzt=jetzt)
        if docker and sperre:  # nur unter gehaltener Sperre: nie zwei Docker-Läufe
            docker_reste.freigeben(_log_pfad().parent, jetzt=jetzt)
        return ergebnis
    finally:
        if sperre:
            sperre.close()  # gibt die Sperre frei


def _alle_ordner(*, loeschen: bool, jetzt: float | None) -> Rundgang:
    try:
        kandidaten = _kandidaten()
        anteil = _frei_anteil()
        if _ist_platznot(anteil):
            # ponytail: Schließzeit kommt hier und in den Regeln je einmal per gh; Cache, falls zu langsam.
            zeiten = {k: _schliesszeit(k) for k in kandidaten}
            kandidaten.sort(key=lambda k: (zeiten[k] is None, zeiten[k] or 0.0, str(k)))
    except Exception as fehler:  # Vertrag: wirft nie
        log.exception("Hausmeister: Aufräumen gescheitert (Ordnersuche/Reihenfolge)")
        _zweige_im_takt(loeschen, jetzt)
        return Rundgang(False, f"Ordnersuche gescheitert ({type(fehler).__name__}: {fehler})")
    ergebnisse: list[Ergebnis] = []
    for ordner in kandidaten:
        erg = _aufraeumen(ordner, loeschen=loeschen, jetzt=jetzt, platznot=_ist_platznot(anteil))
        ergebnisse.append(erg)
        if erg.art != "bleibt" and not erg.trocken:
            anteil = _frei_anteil()
    _kennzahl_schreiben(ergebnisse, trocken=not loeschen)
    _zweige_im_takt(loeschen, jetzt)
    # L5 #145: Netz für vergessene Ablösungen, wirft nie; Probelauf schreibt/lädt nichts (#169).
    uebergabe_sichern.sichern(trocken=not loeschen)
    return Rundgang(True, "", tuple(ergebnisse))


def zweige_loeschen_an() -> bool:
    """#1491: Räumt der Hausmeister Zweige weg? Nur mit ``TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN=1``.

    Die EINE Stelle der Regel: gilt für ``sicherung/*`` und ``ticket/*`` (GitHub und
    Tages-Lauf über die Hauptrepos), für jeden Aufrufer gleich (Aufpasser, capo,
    Start-Check, Handaufruf). Standard aus. Welche Repos, sagen weiter
    ``…_ZWEIG_REPOS``/``…_HAUPTREPOS``. Unberührt: der lokale ``ticket/*``-Zweig eines
    entfernten Worktrees (:func:`_loeschen`, vorher gesichert) und der reine Probelauf.
    """
    return os.environ.get(ZWEIGE_SCHALTER, "").strip() == "1"


def _zweige_im_takt(loeschen: bool, jetzt: float | None) -> None:
    """E41/#1439: Sicherungs- und Ticket-Zweige im Takt von :func:`aufraeumen_alles` (täglich)."""
    if not zweige_loeschen_an():
        log.info("Hausmeister: %s — alle Zweige bleiben", ZWEIGE_AUS)
        return
    try:
        sicherungen_aufraeumen(loeschen=loeschen, jetzt=jetzt)
    except Exception:  # Vertrag: wirft nie — Ordner-Ergebnisse gehen vor
        log.exception("Hausmeister: Sicherungs-Zweige gescheitert")
    try:
        zweige_aufraeumen(loeschen=loeschen, jetzt=jetzt)
    except Exception:  # Vertrag: wirft nie — Ordner-Ergebnisse gehen vor
        log.exception("Hausmeister: Ticket-Zweige gescheitert")


def start_erlaubt(*, loeschen: bool = True) -> tuple[bool, str]:
    """Start-Check: (darf eine neue Session starten?, Satz für Menschen); wirft nie.

    Bei Platznot erst :func:`aufraeumen_alles`, dann neu messen (ohne Platznot lohnt
    der Lauf mit seinen gh-Abfragen nicht — die Platte bliebe über der Startsperre).
    ``loeschen=False`` (Probelauf, Status) misst nur und löscht nie. Messung unmöglich
    oder unerwarteter Fehler → Start erlaubt, mit Spur in cron.out und Hindernis-Log.
    """
    try:
        return _start_pruefen(loeschen)
    except Exception as fehler:  # Vertrag: wirft nie, im Zweifel starten
        log.exception("Hausmeister: Start-Check gescheitert")
        satz = f"Platte: Start-Check gescheitert ({fehler}) — Start erlaubt"
        _spur("hausmeister_fehler", satz)
        return True, satz


def _start_pruefen(loeschen: bool) -> tuple[bool, str]:
    anteil = _frei_anteil()
    zusatz = ""
    if _ist_platznot(anteil):
        rund = rundgang(loeschen=loeschen)
        if not rund.gelaufen and not rund.wartet:
            zusatz = " (Aufräumen gescheitert, siehe Log)"
        anteil = _frei_anteil()
    if anteil is None:
        satz = f"Platte nicht messbar — kein Urteil, Start erlaubt{zusatz}"
        _spur("platte_unmessbar", satz)
        return True, satz
    if anteil >= STARTSPERRE_ANTEIL:
        return True, f"Platte frei: {_prozent(anteil)} frei{zusatz}"
    nach = "trotz gescheitertem Aufräumen" if zusatz else "auch nach dem Aufräumen"
    satz = (
        f"Platte voll: nur {_prozent(anteil)} frei (Startsperre unter "
        f"{_prozent(STARTSPERRE_ANTEIL)}), {nach} — keine neue Session"
    )
    _spur("platte_voll", satz, frei_anteil=round(anteil, 4))
    return False, satz


def _spur(art: str, satz: str, **felder: object) -> None:
    """Eine Zeile in cron.out und im Hindernis-Log."""
    _aufpasser_log(satz)
    hindernis.melden(art, satz, **felder)


def _sperre_nehmen() -> ExitStack | None | bool:
    """Sperrdatei (nicht blockierend, :func:`to_spawn.sperre.halte`): Stapel = Sperre
    gehalten (``close()`` gibt frei), ``False`` = anderer Lauf hält sie. Windows über
    :func:`hausmeister_windows.sperre_nehmen` (dort die msvcrt-Test-Naht)."""
    datei = _log_pfad().parent / SPERR_DATEI
    if _ist_windows():
        return hausmeister_windows.sperre_nehmen(datei)
    stapel = ExitStack()
    if not stapel.enter_context(_sperre.halte(datei)):
        stapel.close()
        return False
    return stapel


def _aufraeumen(ordner: Path, *, loeschen: bool, jetzt: float | None, platznot: bool) -> Ergebnis:
    ordner = Path(os.path.abspath(Path(ordner).expanduser()))
    trocken = not loeschen
    stand = _Stand()
    ziel: tuple[Path, bool] | None = None
    try:
        erg, ziel = _regeln(ordner, trocken, jetzt, stand, platznot)
    except _Bleibt as b:
        erg = _bleibt(ordner, trocken, stand, b)
    except Exception as fehler:  # Vertrag: wirft nie, im Zweifel bleibt
        log.exception("Hausmeister: Fehler bei %s", ordner)
        erg = _bleibt(ordner, trocken, stand, _Bleibt(f"Fehler: {_kurz(fehler)}", fehler=True))
    if ziel is None:
        _log_schreiben(erg)
        return erg
    if not _log_schreiben(erg):
        erg = replace(
            erg,
            art="bleibt",
            grund="Hausmeister-Log nicht schreibbar — ohne Log kein Löschen",
            fehler=True,
        )
        return erg
    try:
        _loeschen(*ziel)
    except (RuntimeError, OSError) as fehler:
        erg = replace(erg, art="bleibt", grund=f"Löschen gescheitert: {_kurz(fehler)}", fehler=True)
        _log_schreiben(erg)
    return erg


def liste_fuer_david(log_datei: Path | None = None) -> list[Path | str]:
    """Ordner aller Log-Zeilen mit ``fuer_david=True``, jeder nur einmal (Reihenfolge des Logs).

    Ordner kommen als ``Path``; Zweig-Zeilen (#1439) als Text ``<remote> <zweig>`` (kein
    ``Path``: der machte aus ``https://`` ein ``https:/``).
    """
    datei = log_datei or _log_pfad()
    try:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    gesehen: dict[Path | str, None] = {}
    for zeile in zeilen:
        try:
            daten = json.loads(zeile)
        except ValueError:
            continue
        if not isinstance(daten, dict) or not daten.get("fuer_david"):
            continue
        if daten.get("ordner"):
            gesehen.setdefault(Path(daten["ordner"]), None)
        elif daten.get("zweig"):
            gesehen.setdefault(f"{daten.get('remote') or ''} {daten['zweig']}", None)
    return list(gesehen)


#: Zweige ``sicherung/<N>`` gehen so viele Tage nach dem Anlegen (E41).
SICHERUNG_TAGE = 90
#: Höchstens ein Lauf je Modus in diesem Abstand.
SICHERUNG_TAKT_SEKUNDEN = 24 * 3600
_TAG = 24 * 3600.0
#: Vorgabe (Alias); wirksam ist :func:`_sicherung_remotes`.
_SICHERUNG_REMOTES = _remotes_aus(_VORGABE_REPOS)
_SICHERUNG_REF = re.compile(r"^refs/sicherung/(\d+)$")


@dataclass(frozen=True)
class ZweigErgebnis:
    """Entscheidung für einen Zweig ``sicherung/<N>`` bzw. ``ticket/*`` (#1439) oder einen Fehler."""

    remote: str  # Remote-URL; bei ``ort="lokal"`` der Pfad des Hauptrepos
    zweig: str  # "sicherung/<N>" / "ticket/…"; leer bei Fehler am ganzen Remote/Repo
    sha: str
    angelegt: float | None
    art: Literal["gelöscht", "gesichert+gelöscht", "bleibt"]
    grund: str
    trocken: bool = True
    fehler: bool = False
    ort: Literal["remote", "lokal"] = "remote"
    fuer_david: bool = False


def sicherungen_aufraeumen(*, loeschen: bool = False, jetzt: float | None = None) -> list[ZweigErgebnis]:
    """Löscht ``sicherung/<N>``-Zweige 90 Tage nach dem Anlegen; wirft nie.

    Nur mit Schalter (:func:`zweige_loeschen_an`), sonst ``[]``. Höchstens einmal
    täglich je Modus, sonst ``[]``. Junge Zweige stehen nur im Ergebnis (ohne
    Log-Zeile), Löschungen und Fehler je eine Log-Zeile.
    """
    if not zweige_loeschen_an():  # #1491: Schalter aus ⇒ nichts, auch kein Takt verbraucht
        return []
    jetzt = time.time() if jetzt is None else jetzt
    modus = "loeschen" if loeschen else "trocken"
    if not _sicherung_takt_faellig(modus, jetzt):
        return []
    ergebnisse: list[ZweigErgebnis] = []
    remotes = _sicherung_remotes()
    erreicht = not remotes
    for remote in remotes:
        try:
            ergebnisse += _sicherungen_eines_remotes(remote, loeschen, jetzt)
            erreicht = True
        except Exception as fehler:  # Vertrag: wirft nie
            log.exception("Hausmeister: Sicherungs-Zweige auf %s", remote)
            erg = ZweigErgebnis(
                remote=remote,
                zweig="",
                sha="",
                angelegt=None,
                art="bleibt",
                grund=f"Fehler: {_kurz(fehler)}",
                trocken=not loeschen,
                fehler=True,
            )
            _zweig_log(erg)
            ergebnisse.append(erg)
    if erreicht:  # kein Remote erreicht ⇒ nächster Takt versucht es wieder
        _sicherung_takt_merken(modus, jetzt)
    return ergebnisse


def _git_ohne_frage(cwd: Path, *args: str) -> str:
    """git ohne Passwort-Abfrage: ohne Zugang scheitert der Aufruf, statt 300 s zu warten."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    return aufpasser.git_lauf(cwd, *args, env=env)


def _sicherung_remotes() -> list[str]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS")
    if roh is None:
        return list(_remotes_aus(_maschinen_repos()))
    return roh.split()  # Leerzeichen-getrennt: URLs enthalten „:“


def _sicherung_ordner() -> Path:
    return _log_pfad().parent / "sicherungs-zweige"


def _sicherung_takt_faellig(modus: str, jetzt: float) -> bool:
    try:
        stand = json.loads((_sicherung_ordner() / "takt.json").read_text("utf-8"))
        zuletzt = float(stand[modus])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    # Uhr zurückgestellt (zuletzt in der Zukunft) ⇒ fällig, sonst hinge der Lauf fest.
    return not 0 <= jetzt - zuletzt < SICHERUNG_TAKT_SEKUNDEN


def _sicherung_takt_merken(modus: str, jetzt: float) -> None:
    datei = _sicherung_ordner() / "takt.json"
    try:
        try:
            stand = json.loads(datei.read_text("utf-8"))
            if not isinstance(stand, dict):
                stand = {}
        except (OSError, ValueError):
            stand = {}
        stand[modus] = jetzt
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(json.dumps(stand), encoding="utf-8")
    except OSError as fehler:
        log.warning("Hausmeister: Takt der Sicherungs-Zweige nicht schreibbar: %s", fehler)


def _sicherung_cache(remote: str) -> Path:
    """Zwischenspeicher je Remote: nur Commits (Bäume/Dateien bleiben auf GitHub)."""
    name = re.sub(r"[^A-Za-z0-9]+", "-", remote).strip("-")[-80:] or "remote"
    cache = _sicherung_ordner() / f"{name}.git"
    if not (cache / "HEAD").is_file():
        cache.mkdir(parents=True, exist_ok=True)
        aufpasser.git_lauf(cache, "init", "-q", "--bare")
    # Jedes Mal setzen (idempotent): eine halb gescheiterte Einrichtung holte sonst
    # beim nächsten Fetch still das ganze Repo samt Dateien.
    for schluessel, wert in (
        ("core.repositoryformatversion", "1"),
        ("extensions.partialClone", "origin"),
        ("remote.origin.promisor", "true"),
        ("remote.origin.partialclonefilter", "tree:0"),
        ("remote.origin.url", remote),
    ):
        aufpasser.git_lauf(cache, "config", schluessel, wert)
    return cache


def _sicherungen_eines_remotes(remote: str, loeschen: bool, jetzt: float) -> list[ZweigErgebnis]:
    cache = _sicherung_cache(remote)
    _git_ohne_frage(
        cache,
        "fetch",
        "-q",
        "--prune",
        "--no-tags",
        "origin",
        "+refs/heads/sicherung/*:refs/sicherung/*",
    )
    roh = aufpasser.git_lauf(cache, "for-each-ref", "--format=%(refname) %(objectname)", "refs/sicherung/")
    ergebnisse = []
    for zeile in roh.splitlines():
        ref, _, sha = zeile.partition(" ")
        treffer = _SICHERUNG_REF.match(ref)
        if not treffer:
            continue  # z. B. sicherung/abc — nie anfassen
        nr = treffer.group(1)  # Ziffern unverändert (``007`` bleibt ``007``)
        try:
            erg = _sicherung_entscheiden(cache, remote, nr, sha, loeschen, jetzt)
        except Exception as fehler:  # ein kaputter Zweig hält die übrigen nicht auf
            log.exception("Hausmeister: Zweig sicherung/%s auf %s", nr, remote)
            erg = ZweigErgebnis(
                remote=remote,
                zweig=f"sicherung/{nr}",
                sha=sha,
                angelegt=None,
                art="bleibt",
                grund=f"Fehler: {_kurz(fehler)}",
                trocken=not loeschen,
                fehler=True,
            )
            _zweig_log(erg)
        ergebnisse.append(erg)
    return ergebnisse


def _sicherung_entscheiden(cache: Path, remote: str, nr: str, sha: str, loeschen: bool, jetzt: float) -> ZweigErgebnis:
    zweig = f"sicherung/{nr}"
    trocken = not loeschen
    zeiten = aufpasser.git_lauf(
        cache,
        "log",
        "--format=%ct",
        "-E",
        f"--grep=^Aufpasser: Sicherung #{nr}( |$)",
        sha,
    ).split()
    if not zeiten:
        return ZweigErgebnis(
            remote,
            zweig,
            sha,
            None,
            "bleibt",
            "Anlegezeit nicht belegbar (kein Aufpasser-Sicherungs-Commit)",
            trocken,
        )
    angelegt = float(min(int(z) for z in zeiten))
    tage = (jetzt - angelegt) / _TAG
    zuletzt = (jetzt - float(max(int(z) for z in zeiten))) / _TAG
    if tage < SICHERUNG_TAGE:
        return ZweigErgebnis(
            remote,
            zweig,
            sha,
            angelegt,
            "bleibt",
            f"erst {tage:.0f} Tage alt (Grenze {SICHERUNG_TAGE})",
            trocken,
        )
    if zuletzt < SICHERUNG_TAGE:
        return ZweigErgebnis(
            remote,
            zweig,
            sha,
            angelegt,
            "bleibt",
            f"vor {tage:.0f} Tagen angelegt, aber zuletzt vor {zuletzt:.0f} Tagen "
            f"gesichert (Grenze {SICHERUNG_TAGE}) — Ticket läuft noch",
            trocken,
        )
    erg = ZweigErgebnis(
        remote,
        zweig,
        sha,
        angelegt,
        "gelöscht",
        f"vor {tage:.0f} Tagen angelegt, zuletzt vor {zuletzt:.0f} Tagen gesichert (Grenze {SICHERUNG_TAGE} Tage)",
        trocken,
    )
    if not _zweig_log(erg):
        return replace(
            erg,
            art="bleibt",
            grund="Hausmeister-Log nicht schreibbar — ohne Log kein Löschen",
            fehler=True,
        )
    if trocken:
        return erg
    try:
        _zweig_loeschen(cache, zweig, sha)
    except RuntimeError as fehler:
        erg = replace(erg, art="bleibt", grund=f"Löschen gescheitert: {_kurz(fehler)}", fehler=True)
        _zweig_log(erg)
    return erg


def _zweig_loeschen(cache: Path, zweig: str, sha: str) -> None:
    """Löscht ``zweig`` auf dem Remote nur, wenn er noch auf ``sha`` steht (Lease).

    #1491: Die Lösch-Senke für GitHub — ohne Schalter (:func:`zweige_loeschen_an`) wirft sie
    :class:`_ZweigLoeschenAus`, bevor irgendein Push abgeht. So kommt kein Aufrufer vorbei.
    """
    if not zweige_loeschen_an():
        raise _ZweigLoeschenAus(ZWEIGE_AUS)
    ref = f"refs/heads/{zweig}"
    _git_ohne_frage(cache, "push", "-q", f"--force-with-lease={ref}:{sha}", "origin", f":{ref}")


def _zweig_log(erg: ZweigErgebnis, *, absicht: bool = False) -> bool:
    """Eine Log-Zeile; ``absicht`` = vor dem Löschen (Art „löscht“, das Ergebnis folgt).

    Im Probelauf nur das Programm-Log, keine Hausmeister-Log-Zeile.
    """
    log.info("Hausmeister: %s %s — %s (%s)", erg.remote, erg.zweig, erg.art, erg.grund)
    if _PROBELAUF.get():
        return True
    angelegt = (
        datetime.fromtimestamp(erg.angelegt).astimezone().isoformat(timespec="seconds")
        if erg.angelegt is not None
        else None
    )
    return _log_zeile(
        {
            "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
            "remote": erg.remote,
            "zweig": erg.zweig,
            "sha": erg.sha,
            "angelegt": angelegt,
            "art": "löscht" if absicht else erg.art,
            "grund": erg.grund,
            "trocken": erg.trocken,
            "fehler": erg.fehler,
            "ort": erg.ort,
            "fuer_david": erg.fuer_david,
        }
    )


# --- Ticket-Zweige (#1439) --------------------------------------------------------

Ort = Literal["remote", "lokal"]
#: Läuft gerade :func:`zweige_probelauf`? Dann schreibt :func:`_zweig_log` nichts ins Log.
_PROBELAUF: ContextVar[bool] = ContextVar("hausmeister_probelauf", default=False)


class _Nachgeschoben(RuntimeError):
    """Zweig steht beim Löschen nicht mehr auf dem geprüften Stand (Lease hielt)."""


class _ZweigLoeschenAus(RuntimeError):
    """#1491: Lösch-Push auf GitHub verlangt, aber der Schalter fehlt — nichts gelöscht."""


@dataclass(frozen=True)
class _ZweigOrt:
    """Wo ein Zweig gelesen wird: Zwischenspeicher des Remotes oder lokales Hauptrepo."""

    cwd: Path
    remote: str  # Remote-URL bzw. Pfad des Hauptrepos (Ergebnis, Log)
    ort: Ort
    haupt: str = "master"  # Hauptzweig des Remotes (:func:`_hauptzweig`)

    def ref(self, zweig: str) -> str:
        return f"refs/zweige/{zweig}" if self.ort == "remote" else f"refs/heads/{zweig}"

    @property
    def haupt_ref(self) -> str:
        return f"refs/haupt/{self.haupt}" if self.ort == "remote" else f"refs/remotes/origin/{self.haupt}"

    def sicherung(self, nr: int) -> str:
        basis = "refs/sicherung" if self.ort == "remote" else "refs/remotes/origin/sicherung"
        return f"{basis}/{nr}"

    @property
    def sicherungen(self) -> str:
        return "refs/sicherung/*" if self.ort == "remote" else "refs/remotes/origin/sicherung/*"


def zweig_fertig(
    repo: Path | str,
    zweig: str,
    *,
    uebernommen_tip: str | None = None,
    ort: Ort = "remote",
    holen: bool = True,
) -> tuple[bool, list[str], str, str]:
    """(übernommen?, fehlende Commits, Grund, geprüfter Stand) für ``zweig`` — die einzige
    „fertig“-Stelle. Der Stand (SHA, leer = Zweig fehlt) ist genau der, auf den sich die
    Antwort bezieht; gelöscht werden darf nur er (Lease), nie ein später gelesener.

    Übernommen = Zweig ``ticket/<N>-…`` und (``uebernommen_tip`` ist genau der aktuelle
    Stand oder Ticket #N zu). Fehlend = Commits weder auf dem Hauptzweig (``origin/HEAD``) noch auf
    einem ``sicherung/*``-Zweig; hat die Schlange genau diesen Stand übernommen, fehlt
    nichts (ihr Rebase brachte dieselben Änderungen unter anderen SHAs auf master).
    Holt vorher den aktuellen Stand (fetch; ``holen=False``, wenn der Aufrufer für den
    ganzen Lauf schon einmal geholt hat). ``repo``: Pfad (bei ``ort="remote"`` zählt
    dessen origin) oder Remote-URL. git-/gh-Fehler ⇒ RuntimeError.
    """
    nr = _zweig_ticket(zweig)
    if nr is None:
        return False, [], "keine Ticket-Nummer im Zweig (nur ticket/<N>-…)", ""
    o = _zweig_ort(repo, ort)
    if holen:
        _zweig_holen(o)
    stand = _zweig_stand(o, zweig)
    if stand is None:
        return False, [], f"{zweig} gibt es {'auf origin' if ort == 'remote' else 'lokal'} nicht", ""
    tip = (uebernommen_tip or "").strip().lower()
    if len(tip) >= 7 and stand.startswith(tip):
        return True, [], f"Schlange hat genau diesen Stand übernommen ({stand[:7]})", stand
    fehlende = _zweig_fehlende(o, stand)
    # #176: Ticket-Repo des Zweig-Repos, nicht das eine der Maschine (ohne GitHub-origin: keins)
    slug = _origin_slug(o.cwd) if ort == "lokal" else _slug(o.remote)
    daten = _ticket_daten(nr, slug)
    if daten is None and not _nummer_zaehlt(slug):  # #234: Grund statt „nicht abfragbar“
        return False, fehlende, f"#{nr} zählt nicht im Ticket-Repo von {slug or 'origin'} (Maschinen-Liste)", stand
    if not isinstance(daten, dict) or "state" not in daten:
        raise RuntimeError(f"Ticket #{nr} nicht abfragbar")
    if not ticket_zustand.endgueltig_zu(daten):
        anders = f"Stand {stand[:7]} ≠ übernommen {tip[:7]}, " if tip else ""
        return False, fehlende, f"{anders}Ticket #{nr} offen", stand
    if fehlende:
        return True, fehlende, f"Ticket #{nr} zu, {len(fehlende)} Commit(s) nur auf dem Zweig", stand
    return True, [], f"Ticket #{nr} zu, alles auf {o.haupt} oder sicherung/*", stand


def zweig_aufraeumen(
    repo: Path | str,
    zweig: str,
    *,
    uebernommen_tip: str | None = None,
    loeschen: bool = False,
    ort: Ort = "remote",
) -> ZweigErgebnis:
    """Räumt einen fertigen Zweig weg: sichern → prüfen → löschen; wirft nie.

    Trocken (``loeschen=False``) ändert nichts, auch nicht die Sicherung; ``art`` zeigt
    dann, was der echte Lauf täte. Jede Entscheidung = eine Zeile im Hausmeister-Log.
    """
    return _zweig_aufraeumen(repo, zweig, uebernommen_tip, loeschen, ort, holen=True)


def _zweig_aufraeumen(
    repo: Path | str, zweig: str, tip: str | None, loeschen: bool, ort: Ort, *, holen: bool
) -> ZweigErgebnis:
    """:func:`zweig_aufraeumen`; ``holen=False`` im Tageslauf (dort ein Fetch je Repo/Remote)."""
    try:
        return _zweig_entscheiden(repo, zweig, tip, not loeschen, ort, holen)
    except Exception as fehler:  # Vertrag: wirft nie
        log.exception("Hausmeister: Zweig %s in %s", zweig, repo)
        erg = ZweigErgebnis(str(repo), zweig, "", None, "bleibt", f"Fehler: {_kurz(fehler)}", not loeschen, True, ort)
        _zweig_log(erg)
        return erg


def zweige_aufraeumen(*, loeschen: bool = False, jetzt: float | None = None) -> list[ZweigErgebnis]:
    """Täglicher Lauf über alle ``ticket/*``-Zweige (Hauptrepos lokal + Remotes); wirft nie.

    Nur mit Schalter (:func:`zweige_loeschen_an`), sonst ``[]``. Höchstens einmal
    täglich je Modus, sonst ``[]``. Erreicht der Lauf kein Repo und keinen Remote,
    bleibt der Tag offen (nächster Takt versucht es wieder).
    """
    if not zweige_loeschen_an():  # #1491: Schalter aus ⇒ nichts, auch kein Takt verbraucht
        return []
    jetzt = time.time() if jetzt is None else jetzt
    modus = "zweige-loeschen" if loeschen else "zweige-trocken"
    if not _sicherung_takt_faellig(modus, jetzt):
        return []
    ergebnisse, erreicht = _zweige_lauf(loeschen)
    if erreicht:
        _sicherung_takt_merken(modus, jetzt)
    return ergebnisse


def zweige_probelauf(datei: Path) -> Path:
    """Probelauf (nie löschend): eine Zeile je Zweig mit Gruppe nach ``datei``; ``wip/*`` gezählt."""
    token = _PROBELAUF.set(True)  # Probelauf-Funde gehören nicht ins Hausmeister-Log
    try:
        ergebnisse, _ = _zweige_lauf(False)
    finally:
        _PROBELAUF.reset(token)
    zeit = datetime.now().astimezone().isoformat(timespec="seconds")
    zeilen = [f"Probelauf Ticket-Zweige (#1439), {zeit} — nichts gelöscht, nichts gesichert", ""]
    zeilen += [_probe_zeile(e) for e in ergebnisse]
    zeilen += [""] + _wip_zaehlen()
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return datei


def _zweig_entscheiden(
    repo: Path | str, zweig: str, tip: str | None, trocken: bool, ort: Ort, holen: bool
) -> ZweigErgebnis:
    def ergebnis(art: Art | str, grund: str, sha: str = "", **extra: bool) -> ZweigErgebnis:
        erg = ZweigErgebnis(quelle, zweig, sha, None, art, grund, trocken, ort=ort, **extra)  # type: ignore[arg-type]
        _zweig_log(erg)
        return erg

    quelle = str(repo)
    nr = _zweig_ticket(zweig)
    if nr is None:
        return ergebnis("bleibt", "keine Ticket-Nummer im Zweig — der Mensch entscheidet", fuer_david=True)
    o = _zweig_ort(repo, ort)
    quelle = o.remote
    if ort == "lokal" and (wo := _ausgecheckt_in(o.cwd, zweig)):
        return ergebnis("bleibt", f"in Arbeitsordner {wo} ausgecheckt")
    # Ab hier nur noch der geprüfte Stand: Sicherung, Gegenprobe und Lease beziehen sich
    # auf ``sha``; steht der Zweig beim Löschen anders, bleibt er (_Nachgeschoben).
    fertig, fehlende, grund, sha = zweig_fertig(repo, zweig, uebernommen_tip=tip, ort=ort, holen=holen)
    if not fertig or not sha:
        return ergebnis("bleibt", grund, sha)
    if ort == "remote" and not zweige_loeschen_an():  # #1491: vor Sicherungs-Push und Absicht-Zeile
        return ergebnis("bleibt", ZWEIGE_AUS, sha, fehler=not trocken)
    art = "gesichert+gelöscht" if fehlende else "gelöscht"
    if fehlende and ort == "lokal":  # Remote-Commits liegen schon auf GitHub, lokale nicht
        leck = aufpasser.git_lauf(
            o.cwd, "log", "--format=", "--name-only", sha, "--not", o.haupt_ref,
            f"--glob={o.sicherungen}", "--", *_glob(aufpasser.SICHERUNG_AUSSCHLUSS),
        ).split()  # fmt: skip
        if leck:
            return ergebnis("bleibt", f"Geheimnis-Datei {leck[0]} in ungesichertem Commit", sha, fuer_david=True)
        try:  # #169: dieselbe Sperre wie der Zweig-Upload, Übergaben nie auf sicherung/<N>
            gesperrt = uebergabe_sichern.gesperrte_pfade(o.cwd, sha, "--not", o.haupt_ref, f"--glob={o.sicherungen}")
        except OSError as fehler:
            return ergebnis("bleibt", f"Prüfung gesperrter Pfade gescheitert: {_kurz(fehler)}", sha, fehler=True)
        if gesperrt:
            log.error("Sicherung von %s verweigert: %s nie öffentlich", zweig, ", ".join(gesperrt))
            grund = f"Sicherung verweigert: {', '.join(gesperrt)} nie öffentlich"
            return ergebnis("bleibt", grund, sha, fehler=True, fuer_david=True)
    if trocken:
        return ergebnis(art, grund, sha)
    if fehlende:
        try:
            _zweig_sichern(o, nr, sha)
        except RuntimeError as fehler:
            return ergebnis("bleibt", f"Sicherung gescheitert: {_kurz(fehler)}", sha, fehler=True)
        if _zweig_fehlende(o, sha):  # Gegenprobe gegen den frisch geholten Remote-Stand
            return ergebnis("bleibt", "nach der Sicherung fehlen noch Commits", sha, fehler=True)
        grund = f"{grund}, gesichert auf sicherung/{nr}"
    erg = ZweigErgebnis(quelle, zweig, sha, None, art, grund, False, ort=ort)  # type: ignore[arg-type]
    if not _zweig_log(erg, absicht=True):  # Absicht vor dem Löschen, Ergebnis danach
        return replace(erg, art="bleibt", grund="Hausmeister-Log nicht schreibbar — ohne Log kein Löschen", fehler=True)
    try:
        _zweig_weg(o, zweig, sha)
    except _Nachgeschoben as fehler:
        return ergebnis("bleibt", f"nach der Prüfung nachgeschoben, nächster Lauf entscheidet neu ({fehler})", sha)
    except RuntimeError as fehler:
        return ergebnis("bleibt", f"Löschen gescheitert: {_kurz(fehler)}", sha, fehler=True)
    _zweig_log(erg)
    return erg


def _zweige_lauf(loeschen: bool) -> tuple[list[ZweigErgebnis], bool]:
    """Ein Lauf über Hauptrepos (lokal) und Remotes; (Ergebnisse, irgendwas erreicht?)."""
    ergebnisse: list[ZweigErgebnis] = []
    hauptrepos, remotes = _hauptrepos(), _sicherung_remotes()
    erreicht = not (hauptrepos or remotes)
    for repo in hauptrepos:
        try:
            _git_ohne_frage(repo, "fetch", "-q", "--prune", "origin")
            if loeschen:  # trocken ändert nichts, auch keine Worktree-Verwaltung
                aufpasser.git_lauf(repo, "worktree", "prune")
            zweige = _refs(repo, "refs/heads/ticket/", "refs/heads/")
            belegt = _ausgecheckte(repo)
            erreicht = True
        except (RuntimeError, OSError) as fehler:  # ein Repo hält die übrigen nicht auf
            ergebnisse.append(_zweig_fehler(str(repo), "lokal", loeschen, fehler))
            continue
        for zweig in zweige:
            if zweig not in belegt:
                ergebnisse.append(_zweig_aufraeumen(repo, zweig, None, loeschen, "lokal", holen=False))
    for remote in remotes:
        try:
            o = _zweig_ort(remote, "remote")
            _zweig_holen(o)
            zweige = _refs(o.cwd, "refs/zweige/ticket/", "refs/zweige/")
            erreicht = True
        except (RuntimeError, OSError) as fehler:
            ergebnisse.append(_zweig_fehler(remote, "remote", loeschen, fehler))
            continue
        # Ein Fetch je Remote (oben); die Lease beim Löschen fängt Nachgeschobenes ab.
        ergebnisse += [_zweig_aufraeumen(remote, z, None, loeschen, "remote", holen=False) for z in zweige]
    return ergebnisse, erreicht


def _zweig_fehler(remote: str, ort: Ort, loeschen: bool, fehler: Exception) -> ZweigErgebnis:
    log.warning("Hausmeister: Ticket-Zweige in %s: %s", remote, fehler)
    erg = ZweigErgebnis(remote, "", "", None, "bleibt", f"Fehler: {_kurz(fehler)}", not loeschen, True, ort)
    _zweig_log(erg)
    return erg


def _hauptrepos() -> list[Path]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_HAUPTREPOS")
    if roh is not None:
        return [Path(p).expanduser() for p in roh.split()]
    pfade = (Path(p).expanduser() for e in _maschinen_repos() if (p := e["pfad"].strip()))
    return [p for p in dict.fromkeys(pfade) if (p / ".git").is_dir()]


def _zweig_ticket(zweig: str) -> int | None:
    """Nummer eines ``ticket/<N>-…``-Zweigs (nur diese räumt der Zweig-Pfad), aus ``_ZWEIG_FORMEN``."""
    return _nummer(zweig, _ZWEIG_FORMEN) if zweig.startswith("ticket/") else None


def _zweig_ort(repo: Path | str, ort: Ort) -> _ZweigOrt:
    if ort == "lokal":
        return _ZweigOrt(Path(repo), str(repo), "lokal", _hauptzweig(Path(repo), str(repo)))
    url = str(repo)
    if Path(url).is_dir():
        url = aufpasser.git_lauf(Path(url), "config", "--get", "remote.origin.url").strip()
    cache = _sicherung_cache(url)
    return _ZweigOrt(cache, url, "remote", _hauptzweig(cache, url))


#: Hauptzweig je Remote/Repo, nur erfolgreich ermittelte (ein ls-remote je Lauf).
_HAUPTZWEIGE: dict[str, str] = {}


def _hauptzweig(cwd: Path, schluessel: str) -> str:
    """Hauptzweig von ``origin`` in ``cwd`` — die einzige Stelle dafür (Skill-Repo: ``main``).

    Lokal ``origin/HEAD``, sonst ``ls-remote --symref``; nicht ermittelbar ⇒ ``master``.
    """
    kopf = (_git_oder_none(cwd, "symbolic-ref", "-q", "refs/remotes/origin/HEAD") or "").strip()
    if kopf.startswith("refs/remotes/origin/"):
        return kopf.removeprefix("refs/remotes/origin/")
    if schluessel in _HAUPTZWEIGE:
        return _HAUPTZWEIGE[schluessel]
    try:
        aus = _git_ohne_frage(cwd, "ls-remote", "--symref", "origin", "HEAD")
    except RuntimeError:
        return "master"
    for zeile in aus.splitlines():
        teile = zeile.split()
        if len(teile) >= 2 and teile[0] == "ref:" and teile[1].startswith("refs/heads/"):
            _HAUPTZWEIGE[schluessel] = teile[1].removeprefix("refs/heads/")
            return _HAUPTZWEIGE[schluessel]
    return "master"


def _kurz(fehler: Exception) -> str:
    """Erste Zeile eines Fehlers als Grund — nie git-Hilfetext über mehrere Zeilen."""
    return (str(fehler).strip().splitlines() or [type(fehler).__name__])[0]


def _zweig_holen(o: _ZweigOrt) -> None:
    if o.ort == "lokal":
        _git_ohne_frage(o.cwd, "fetch", "-q", "--prune", "origin")
        return
    _git_ohne_frage(
        o.cwd, "fetch", "-q", "--prune", "--no-tags", "origin",
        f"+refs/heads/{o.haupt}:{o.haupt_ref}",
        "+refs/heads/sicherung/*:refs/sicherung/*",
        "+refs/heads/ticket/*:refs/zweige/ticket/*",
    )  # fmt: skip


def _zweig_stand(o: _ZweigOrt, zweig: str) -> str | None:
    return _git_oder_none(o.cwd, "rev-parse", "--verify", "-q", f"{o.ref(zweig)}^{{commit}}") or None


def _zweig_fehlende(o: _ZweigOrt, sha: str) -> list[str]:
    """Commits von ``sha``, die weder auf dem Hauptzweig noch auf einem ``sicherung/*`` liegen."""
    return aufpasser.git_lauf(o.cwd, "rev-list", sha, "--not", o.haupt_ref, f"--glob={o.sicherungen}").split()


def _zweig_sichern(o: _ZweigOrt, nr: int, sha: str) -> None:
    """``sha`` auf ``sicherung/<nr>`` (Aufpasser-Weg, #1371 liest die Anlegezeit), dann holen."""
    if o.ort == "remote":  # eigener Zwischenspeicher: maßgeblich ist der Remote-Stand
        _git_oder_none(o.cwd, "update-ref", "-d", f"refs/heads/sicherung/{nr}")
    zusatz = [o.sicherung(nr)] if _git_oder_none(o.cwd, "rev-parse", "--verify", "-q", o.sicherung(nr)) else []
    aufpasser.sichern(o.cwd, nr, kopf=sha, zusatz_koepfe=zusatz)
    _git_ohne_frage(o.cwd, "fetch", "-q", "origin", f"+refs/heads/sicherung/{nr}:{o.sicherung(nr)}")


def _zweig_weg(o: _ZweigOrt, zweig: str, sha: str) -> None:
    """Löscht nur, wenn der Zweig noch auf ``sha`` steht; sonst :class:`_Nachgeschoben`."""
    if o.ort == "remote":
        try:
            _zweig_loeschen(o.cwd, zweig, sha)
        except RuntimeError as fehler:
            if "stale info" in str(fehler) or "[rejected]" in str(fehler):
                raise _Nachgeschoben(str(fehler)) from None
            raise
        return
    if wo := _ausgecheckt_in(o.cwd, zweig):
        raise _Nachgeschoben(f"inzwischen in {wo} ausgecheckt")
    if _zweig_stand(o, zweig) != sha:
        raise _Nachgeschoben("lokal weitergeschrieben")
    aufpasser.git_lauf(o.cwd, "branch", "-D", zweig)


def _refs(repo: Path, praefix: str, ab: str) -> list[str]:
    roh = aufpasser.git_lauf(repo, "for-each-ref", "--format=%(refname)", praefix)
    return [r[len(ab) :] for r in roh.split()]


def _worktree_eintraege(repo: Path) -> list[tuple[str, str | None]]:
    """(Arbeitsordner, Zweig oder ``None``) je Eintrag von ``git worktree list --porcelain``;
    der erste Eintrag ist immer der Haupt-Arbeitsbaum des Repos."""
    eintraege: list[tuple[str, str | None]] = []
    for zeile in aufpasser.git_lauf(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            eintraege.append((zeile[len("worktree ") :], None))
        elif zeile.startswith("branch refs/heads/") and eintraege:
            eintraege[-1] = (eintraege[-1][0], zeile[len("branch refs/heads/") :])
    return eintraege


def _ausgecheckte(repo: Path) -> dict[str, str]:
    """Zweig → Arbeitsordner aller in ``repo`` ausgecheckten Zweige (auch das Hauptrepo)."""
    return {zweig: ordner for ordner, zweig in _worktree_eintraege(repo) if zweig}


def _eingetragene_worktrees() -> list[Path]:
    """#196: alle laut git eingetragenen Arbeitsordner der Hauptrepos, wo immer sie liegen
    (auch außerhalb der Wurzeln, z. B. ``C:\\c\\dev\\…`` nach einem Pfad-Fehler) — ohne den
    Haupt-Arbeitsbaum, nur vorhandene Ordner. Ein kaputtes Repo fällt mit Log-Zeile weg."""
    gefunden: list[Path] = []
    for repo in _hauptrepos():
        try:
            eintraege = _worktree_eintraege(repo)
        except (RuntimeError, OSError) as fehler:
            log.warning("Hausmeister: worktree-Liste von %s unlesbar: %s", repo, _kurz(fehler))
            continue
        for ordner, _zweig in eintraege[1:]:
            pfad = Path(os.path.abspath(ordner))
            if pfad.is_dir():
                gefunden.append(pfad)
    return gefunden


def _pfad_schluessel(pfad: Path) -> str:
    """Vergleichs-Schlüssel eines Ordners: aufgelöst, unter Windows ohne Groß/klein."""
    try:
        echt = pfad.resolve()
    except (OSError, RuntimeError):
        echt = pfad
    return os.path.normcase(str(echt))


def _ausgecheckt_in(repo: Path, zweig: str) -> str | None:
    return _ausgecheckte(repo).get(zweig)


def _probe_zeile(e: ZweigErgebnis) -> str:
    wo = f"{e.remote} [{e.ort}]"
    if not e.zweig:
        return f"{wo}: Fehler — {e.grund}"
    if e.art == "gelöscht":
        gruppe = f"löschen ({e.grund})"
    elif e.art == "gesichert+gelöscht":
        gruppe = f"sichern+löschen ({e.grund})"
    elif _zweig_ticket(e.zweig) is None:
        gruppe = "ohne Ticket-Nummer"
    else:
        gruppe = f"bleibt: {e.grund}"
    return f"{wo} {e.zweig}: {gruppe}"


def _wip_zaehlen() -> list[str]:
    """Nur zählen (#1439, Out of Scope: wip/* löschen) — je Hauptrepo und Remote eine Zeile."""
    zeilen = []
    for repo in _hauptrepos():
        try:
            n = len(_refs(repo, "refs/heads/wip/", "refs/heads/"))
            zeilen.append(f"{repo} [lokal] wip/*: {n} Zweig(e), nur gezählt — bleiben")
        except RuntimeError as fehler:
            zeilen.append(f"{repo} [lokal] wip/*: nicht zählbar ({fehler})")
    for remote in _sicherung_remotes():
        try:
            roh = _git_ohne_frage(_sicherung_cache(remote), "ls-remote", "--heads", "origin", "refs/heads/wip/*")
            zeilen.append(f"{remote} [remote] wip/*: {len(roh.splitlines())} Zweig(e), nur gezählt — bleiben")
        except RuntimeError as fehler:
            zeilen.append(f"{remote} [remote] wip/*: nicht zählbar ({fehler})")
    return zeilen


# --- Regeln ---------------------------------------------------------------------


def _bleibt(ordner: Path, trocken: bool, stand: _Stand, b: _Bleibt) -> Ergebnis:
    return Ergebnis(
        ordner,
        "bleibt",
        b.grund,
        stand.ticket,
        stand.sicherung,
        fuer_david=b.fuer_david,
        trocken=trocken,
        fehler=b.fehler,
    )


def _regeln(
    ordner: Path, trocken: bool, jetzt: float | None, stand: _Stand, platznot: bool
) -> tuple[Ergebnis, tuple[Path, bool] | None]:
    """Alle Regeln; Rückgabe (Ergebnis, Lösch-Ziel oder None). „bleibt“ = ``_Bleibt``."""
    # 1 Aufräum-Bereich (nur der aufgelöste Pfad zählt), Symlink nie
    if not _im_bereich(ordner):
        raise _Bleibt("nicht im Aufräum-Bereich")
    if ordner.is_symlink():
        raise _Bleibt(f"Symlink (zeigt auf {os.readlink(ordner)})")
    if not ordner.is_dir():
        raise _Bleibt("Ordner fehlt")
    echt = ordner.resolve()
    if {ordner.name, echt.name} & set(_geschuetzte_ordner()):
        raise _Bleibt("geschützter Ordner")
    if not trocken:  # Trockenlauf zeigt die echte Regel (Altbestand-Liste für den Menschen)
        _kein_altbestand(echt)

    # 2/3 Git-Ordner? Hauptrepo? Eigenes Repo? (None = gar kein Git, #1536)
    ist_worktree = _git_ordner_pruefen(echt)
    if ist_worktree is None:  # #1536: ohne Git nichts sicherbar ⇒ bleibt immer
        raise _Bleibt("kein eigener Git-Ordner (nicht sicherbar)", fuer_david=True)

    # 4 Ticket-Nummer aus Name und Zweig (None = keine, #1536)
    stand.ticket = ticket = _ticket_nummer(ordner, echt)
    # 4b #135: die Nummer zählt nur im Ticket-Repo selbst (skill-103 ist kein App-Ticket #103)
    slug = _origin_slug(echt)
    # #176: eigener Ordner ohne bestimmbares Ticket-Repo ⇒ bleibt (nie über „fremd, aber fertig“)
    if ticket is not None and slug.lower() in _eigene_repos() and _ticket_repo_fuer(slug) is None:
        raise _Bleibt("Ticket-Repo nicht bestimmbar (Maschinen-Liste)", fuer_david=True, fehler=True)
    fremd = ticket is not None and not _nummer_zaehlt(slug)
    if fremd:
        stand.ticket = None

    # 5/6 Ticket zu, seit mindestens 24 h; keine laufende Spec-Sitzung
    if ticket is not None and not fremd:
        _ticket_lange_zu(ticket, jetzt, platznot, _origin_slug(echt))
        _keine_spec_sitzung(ticket)

    # 7 Prozess im Ordner
    _kein_prozess(echt)

    # 7b #1536/#235: ohne Ticket-Nummer bleibt, was frisch ist (nach Prozess-Regel: der Grund
    # zeigt den Prozess); älter darf er nur weg, wenn nichts zu sichern ist (8b unten)
    if ticket is None:
        _ohne_nummer_nicht_frisch(echt, jetzt)

    # 7c #135: ein offener Pull-Request zum Zweig hält jeden Ordner
    _kein_offener_pull_request(echt, slug)

    # 8 Ungesichertes prüfen (auch trocken: fetch, Geheimnisse, Ignoriertes)
    koepfe, zu_sichern, status_vorher = _ungesichertes(echt, ist_worktree)
    art: Art = "gesichert und gelöscht" if (koepfe or zu_sichern) else "gelöscht"
    if ticket is None:  # 8b #235: ohne Nummer keine Sicherung (sicherung/<N>) ⇒ nur Fertiges
        if koepfe or zu_sichern:
            raise _Bleibt(
                f"{OHNE_NUMMER}: Änderungen oder Commits nicht auf origin (ohne Nummer nicht sicherbar)",
                fuer_david=True,
            )
        _koepfe_auf_server(echt, ist_worktree)
        grund = f"{OHNE_NUMMER}; alles auf origin, Baum sauber, seit 24 h unverändert, kein Pull-Request offen"
    elif fremd:  # 8b #135: ohne gültiges Ticket nur, was nachweislich fertig ist
        grund = _fremd_fertig(echt, ticket, bool(koepfe or zu_sichern), jetzt)
    else:
        grund = _grund(art, ticket, platznot)
    if trocken:
        return Ergebnis(ordner, art, grund, stand.ticket, trocken=True), None
    if ticket is not None and (koepfe or zu_sichern):
        try:
            stand.sicherung = aufpasser.sichern(echt, ticket, jetzt, zusatz_koepfe=koepfe)
        except RuntimeError as fehler:
            raise _Bleibt(f"Sicherung gescheitert: {_kurz(fehler)}", fehler=True) from None
        if stand.sicherung is None:
            raise _Bleibt(
                "Sicherung lieferte nichts, obwohl Ungesichertes erkannt wurde",
                fehler=True,
            )
        _sicherung_auf_server(echt, ticket, stand.sicherung)

    # Gegenprobe direkt vor dem Löschen: nichts Neues, alles auf origin, kein Prozess
    _kein_prozess(echt, probe=True)
    if _status(echt) != status_vorher:
        raise _Bleibt("Ordner hat sich während der Sicherung geändert")
    if _ungepushte_koepfe(echt, ist_worktree):
        raise _Bleibt("nach der Sicherung noch Commits auf keinem Remote", fehler=True)

    # 9 Löschen (erst nach der Log-Zeile, siehe aufraeumen)
    erg = Ergebnis(ordner, art, grund, stand.ticket, stand.sicherung, trocken=False)
    return erg, (echt, ist_worktree)


def _git_ordner_pruefen(echt: Path) -> bool | None:
    """Regeln 2/3; Rückgabe: verknüpfter Worktree (True), Klon (False) oder gar kein Git
    (None, #1536: kein ``.git`` oder verwaister Worktree, dessen Git-Ordner fehlt)."""
    if _git_oder_none(echt, "rev-parse", "--is-inside-git-dir") == "true":
        raise _Bleibt("Git-Verwaltungsordner (Hauptrepo)")
    oben = _git_oder_none(echt, "rev-parse", "--show-toplevel")
    if oben is None:
        if _ohne_git(echt):
            return None
        raise _Bleibt("Git-Ordner nicht lesbar (git rev-parse scheitert)", fehler=True)
    if Path(oben).resolve() != echt:  # Unterordner eines anderen Repos: nie löschen
        raise _Bleibt(f"kein eigener Git-Ordner (gehört zu {oben})", fuer_david=True)
    ist_worktree = (echt / ".git").is_file()
    if not ist_worktree:
        if echt.name in _hauptrepo_namen():
            raise _Bleibt("Hauptrepo")
        if _hat_verknuepfte_worktrees(echt):
            raise _Bleibt("Hauptrepo mit verknüpften Worktrees")
    url = _git_oder_none(echt, "config", "--get", "remote.origin.url") or ""
    if _origin_slug(echt).lower() not in _eigene_repos():
        raise _Bleibt(f"fremdes Repo ({url or 'ohne origin'})", fuer_david=True)
    return ist_worktree


def _ohne_git(echt: Path) -> bool:
    """Kein ``.git`` im Ordner, oder eine ``.git``-Datei, deren ``gitdir`` fehlt (der
    Worktree eines gelöschten Hauptrepos). Ein ``.git``-Ordner, an dem git scheitert,
    ist kaputt oder fremd — der zählt nicht als „ohne Git“."""
    marke = echt / ".git"
    if not os.path.lexists(marke):
        return True
    if marke.is_symlink() or not marke.is_file():
        return False
    try:
        zeile = marke.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if not zeile.startswith("gitdir:"):
        return False
    ziel = Path(zeile[len("gitdir:") :].strip())
    if not ziel.is_absolute():
        ziel = echt / ziel
    return not os.path.lexists(ziel)


def _keine_spec_sitzung(ticket: int) -> None:
    """#1536: lebt die tmux-Sitzung ``spec-<N>``, arbeitet die Spec noch ⇒ ``_Bleibt``.
    Ohne tmux (PC) oder ohne tmux-Server gibt es keine Sitzung."""
    try:
        lauf = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_ZEITLIMIT_S,
            check=False,
        )
    except FileNotFoundError:
        return
    except (OSError, subprocess.TimeoutExpired) as fehler:
        raise _Bleibt(f"tmux nicht abfragbar: {_kurz(fehler)}", fehler=True) from None
    if lauf.returncode != 0:
        if "no server running" in lauf.stderr or "error connecting" in lauf.stderr:
            return
        raise _Bleibt(f"tmux nicht abfragbar: {lauf.stderr.strip()[:200]}", fehler=True)
    if f"spec-{ticket}" in lauf.stdout.split():
        raise _Bleibt(f"tmux-Sitzung spec-{ticket} läuft")


def _ticket_nummer(ordner: Path, echt: Path) -> int | None:
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    aus_name = _nummer(ordner.name, _name_muster())
    aus_zweig = _nummer(zweig, _ZWEIG_FORMEN)
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        raise _Bleibt(
            f"Ordnername (#{aus_name}) und Zweig {zweig} (#{aus_zweig}) widersprechen sich",
            fuer_david=True,
        )
    return aus_name if aus_name is not None else aus_zweig


def _nummer_zaehlt(slug: str) -> bool:
    """#135: Nummer aus Ordner/Zweig gilt nur, wenn ``origin`` (``slug``) selbst das Ticket-Repo ist.

    Sonst gehört sie zu einem anderen Repo (``skill-103`` = Skill-Issue, nicht App-Ticket #103);
    der Ordner darf dann nur weg, wenn er nachweislich fertig ist (:func:`_fremd_fertig`).
    Einzige Stelle für diese Zuordnung — Ordner-Präfixe dürfen beliebige Repos treffen.
    """
    ticket_repo = _ticket_repo_fuer(slug) if slug.strip() else None
    return ticket_repo is not None and ticket_repo.strip().lower() == slug.strip().lower()


def _kein_offener_pull_request(echt: Path, slug: str) -> None:
    """#135: offener Pull-Request zum ausgecheckten Zweig ⇒ ``_Bleibt``; nicht abfragbar ⇒ ``_Bleibt``.

    Losgelöster Kopf hat keinen Zweig, zu dem ein Pull-Request gehören kann."""
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD")
    if not zweig:
        return
    daten = gh.json_lauf(
        ["pr", "list", "-R", slug, "--head", zweig, "--state", "open", "--json", "number"],
        timeout=GH_ZEITLIMIT_S,
    )
    if not isinstance(daten, list):
        raise _Bleibt(f"Pull-Requests zu {zweig} nicht abfragbar", fehler=True)
    if daten:
        nr = daten[0].get("number") if isinstance(daten[0], dict) else "?"
        raise _Bleibt(f"Pull-Request #{nr} zu {zweig} offen")


def _fremd_fertig(echt: Path, ticket: int, ungesichert: bool, jetzt: float | None) -> str:
    """#135: Ordner mit Nummer eines anderen Repos darf nur weg, wenn nichts zu sichern ist,
    sein Kopf im Hauptzweig von origin liegt und seit 24 h nichts geändert wurde; sonst ``_Bleibt``.
    Rückgabe: Grund für Log und Ergebnis."""
    vorne = f"#{ticket} ist kein Ticket dieses Repos"
    if ungesichert:
        raise _Bleibt(f"{vorne}: Änderungen oder Commits nicht im Hauptzweig gesichert", fuer_david=True)
    haupt = _hauptzweig(echt, str(echt))
    offen = _git_oder_none(echt, "rev-list", "-n", "1", "HEAD", "--not", f"refs/remotes/origin/{haupt}")
    if offen is None:
        raise _Bleibt(f"{vorne}: origin/{haupt} nicht lesbar", fehler=True)
    if offen:
        raise _Bleibt(f"{vorne}: Kopf nicht in origin/{haupt}")
    jetzt_ts = jetzt if jetzt is not None else time.time()
    if jetzt_ts - _juengste_aenderung(echt) < MINDEST_ZU_SEKUNDEN:
        raise _Bleibt(f"{vorne}: frisch geändert (vor weniger als 24 h)")
    return f"{vorne}; Kopf in origin/{haupt}, Baum sauber, seit 24 h unverändert, kein Pull-Request offen"


def _ohne_nummer_nicht_frisch(echt: Path, jetzt: float | None) -> None:
    """#235: Ordner ohne Ticket-Nummer, in den vor weniger als 24 h geschrieben wurde, bleibt
    mit dem Grund ``keine Ticket-Nummer`` (#1536) — er ist vermutlich in Arbeit."""
    jetzt_ts = jetzt if jetzt is not None else time.time()
    if jetzt_ts - _juengste_aenderung(echt) < MINDEST_ZU_SEKUNDEN:
        raise _Bleibt(OHNE_NUMMER, fuer_david=True)


def _juengste_aenderung(echt: Path) -> float:
    """Jüngste Änderungszeit im Ordner (ohne ``.git``-Inhalt) und in Index/HEAD seines Git-Ordners.

    ponytail: läuft durch den ganzen Baum; bei riesigen Ordnern (node_modules) teuer —
    dann auf ``git status``-Pfade plus Git-Ordner begrenzen."""
    neu = 0.0
    pfade: list[str] = [str(echt)]
    git_ordner = _git_oder_none(echt, "rev-parse", "--absolute-git-dir")
    if git_ordner:
        pfade += [os.path.join(git_ordner, n) for n in ("index", "HEAD", os.path.join("logs", "HEAD"))]
    for wurzel, unter, dateien in os.walk(echt):
        unter[:] = [u for u in unter if u != ".git"]
        pfade += [os.path.join(wurzel, n) for n in (*unter, *dateien)]
    for pfad in pfade:
        try:
            neu = max(neu, os.lstat(pfad).st_mtime)
        except OSError:  # in der Zwischenzeit verschwunden: zählt nicht als Änderung
            continue
    return neu


def _ticket_lange_zu(ticket: int, jetzt: float | None, platznot: bool = False, slug: str | None = None) -> None:
    daten = _ticket_daten(ticket, slug)
    if not isinstance(daten, dict) or "state" not in daten:
        raise _Bleibt(f"Ticket #{ticket} nicht abfragbar", fehler=True)
    if not ticket_zustand.endgueltig_zu(daten):
        raise _Bleibt(f"Ticket #{ticket} offen")
    zu_seit = _zeitpunkt(daten.get("closedAt"))
    if zu_seit is None:
        raise _Bleibt(f"Ticket #{ticket}: Schließzeit unbekannt")
    if platznot:
        return  # Platznot: geschlossen reicht, die 24 h entfallen
    jetzt_ts = jetzt if jetzt is not None else time.time()
    if jetzt_ts - zu_seit < MINDEST_ZU_SEKUNDEN:
        raise _Bleibt(f"Ticket #{ticket} erst seit weniger als 24 h zu")


def _grund(art: Art, ticket: int, platznot: bool = False) -> str:
    if platznot:
        zu = f"Ticket #{ticket} zu, Platznot (unter {_prozent(PLATZNOT_ANTEIL)} frei)"
    else:
        zu = f"Ticket #{ticket} seit über 24 h zu"
    if art == "gelöscht":
        return f"{zu}, nichts Ungesichertes"
    return f"{zu}, Ungesichertes auf sicherung/{ticket}"


# --- Platte ---------------------------------------------------------------------


def _kandidaten() -> list[Path]:
    """Alle Ordner, die auf ein Wurzel-Muster passen, sortiert, dahinter die laut git
    eingetragenen Arbeitsordner außerhalb davon (#196) — ohne Doppelte, nie das Hauptrepo."""
    gefunden: dict[Path, None] = {}
    for muster in _wurzeln():
        for treffer in sorted(glob.glob(muster)):
            pfad = Path(os.path.abspath(treffer))
            if pfad.is_dir():
                gefunden.setdefault(pfad, None)
    schon = {_pfad_schluessel(p) for p in gefunden}
    for pfad in _eingetragene_worktrees():
        if (schluessel := _pfad_schluessel(pfad)) not in schon:
            schon.add(schluessel)
            gefunden.setdefault(pfad, None)
    return list(gefunden)


def _schliesszeit(ordner: Path) -> float | None:
    """Schließzeit des Tickets zum Ordner (für die Reihenfolge), ``None`` = offen/unbekannt."""
    try:
        echt = ordner.resolve()
        ticket = _ticket_nummer(ordner, echt)
        if ticket is None:
            return None
        daten = _ticket_daten(ticket, _origin_slug(echt))
    except (
        _Bleibt,
        OSError,
        RuntimeError,
    ):  # nur Reihenfolge — die Regeln entscheiden danach selbst
        return None
    if not isinstance(daten, dict) or not ticket_zustand.endgueltig_zu(daten):
        return None
    return _zeitpunkt(daten.get("closedAt"))


def _ticket_daten(ticket: int, slug: str | None = None) -> object:
    """``gh issue view`` (state, closedAt) mit Zeitlimit; ``None`` = nicht abfragbar.

    ``slug`` = origin des Ordners/Zweig-Repos: Ticket-Repo je Repo (:func:`_ticket_repo_fuer`,
    meldet selbst einmal); ohne ``slug`` (kein GitHub-origin) das eine Ticket-Repo der Maschine."""
    if slug is not None:
        # #234: eine Nummer zählt nur im Repo, das selbst ihr Ticket-Repo ist — im Ordner- wie
        # im Zweig-Pfad dieselbe Regel (:func:`_nummer_zaehlt`); sonst nicht abfragbar
        return _gh_ticket(ticket, _ticket_repo_fuer(slug)) if _nummer_zaehlt(slug) else None
    repo = _ticket_repo()
    if repo is None:
        log.warning("Hausmeister: kein eindeutiges Ticket-Repo in der Maschinen-Liste — #%s bleibt", ticket)
        return None
    return _gh_ticket(ticket, repo)


def _gh_ticket(ticket: int, repo: str | None) -> object:
    if repo is None:
        return None
    return gh.json_lauf(
        ["issue", "view", str(ticket), "-R", repo, "--json", "state,closedAt"],
        timeout=GH_ZEITLIMIT_S,
    )


def _ist_platznot(anteil: float | None) -> bool:
    return anteil is not None and anteil < PLATZNOT_ANTEIL


def _frei_anteil() -> float | None:
    """Anteil freier Platte (0..1) der Wurzeln, ``None`` = nicht messbar.

    Gemessen wird die Platte jeder existierenden Wurzel-Basis (am Laptop das
    Laufwerk von ``C:/dev``), der knappste Wert zählt; ohne Wurzel-Basis Home."""
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_PLATTE", "").strip()
    if roh:
        anteil = _naht_anteil(roh)
        if anteil is None:
            log.warning("Platte nicht messbar: TO_SPAWN_HAUSMEISTER_PLATTE=%r ungültig", roh)
        return anteil
    anteile: list[float] = []
    for basis in _platten_basen():
        try:
            stand = shutil.disk_usage(basis)
        except OSError as fehler:
            log.warning("Platte nicht messbar (%s): %s", basis, fehler)
            return None
        if not stand.total:
            log.warning("Platte nicht messbar (%s): Gesamtgröße 0", basis)
            return None
        anteile.append(stand.free / stand.total)
    return min(anteile)


def _platten_basen() -> list[Path]:
    """Fester Teil jedes Wurzel-Musters vor dem ersten Glob-Zeichen, nur existierende,
    ohne Doppelte; keiner existiert ⇒ ``[Path.home()]``."""
    basen: dict[Path, None] = {}
    for muster in _wurzeln():
        teile: list[str] = []
        for teil in Path(muster).parts:
            if glob.has_magic(teil):
                break
            teile.append(teil)
        if teile:
            basis = Path(*teile)
            if basis.is_dir():
                basen.setdefault(basis, None)
    return list(basen) or [Path.home()]


def _naht_anteil(roh: str) -> float | None:
    try:
        return float(roh)
    except ValueError:
        pass
    datei = Path(roh)
    try:
        werte = [z.strip() for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]
        if len(werte) > 1:
            datei.write_text("".join(f"{w}\n" for w in werte[1:]), encoding="utf-8")
        return float(werte[0]) if werte else None
    except (OSError, ValueError):
        return None


def _prozent(anteil: float) -> str:
    """0.15 → „15 %“, 0.034 → „3,4 %“."""
    wert = round(anteil * 100, 1)
    text = f"{int(wert)}" if wert == int(wert) else f"{wert:.1f}".replace(".", ",")
    return f"{text} %"


def _aufpasser_log(satz: str) -> None:
    """Eine Zeile ins Aufpasser-Log (``cron.out`` im Aufpasser-Zustandsordner)."""
    try:
        datei = aufpasser.zustand_standard() / aufpasser.CRON_LOG
        datei.parent.mkdir(parents=True, exist_ok=True)
        zeit = datetime.now().astimezone().isoformat(timespec="seconds")
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"{zeit} Hausmeister: {satz}\n")
    except OSError as fehler:
        log.warning("Aufpasser-Log nicht schreibbar: %s", fehler)


def _wurzeln() -> list[str]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_WURZELN")
    if roh:
        muster = roh.split(os.pathsep)
    else:
        maschine = config.maschine()
        muster = [m for m in maschine["hausmeister"]["wurzeln"] if isinstance(m, str) and _wurzel_erlaubt(m)]
        muster += [b for e in maschine["repos"] if (b := _basis_muster(e["worktree_basis"]))]
    return list(dict.fromkeys(os.path.expanduser(m) for m in muster if m.strip()))


#: Schon gemeldete abgelehnte Wurzeln (``_wurzeln`` läuft je Ordner, eine Log-Zeile reicht).
_ABGELEHNT_GEMELDET: set[str] = set()


def _ablehnen(art: str, wert: str) -> None:
    if wert.strip() and wert not in _ABGELEHNT_GEMELDET:
        _ABGELEHNT_GEMELDET.add(wert)
        log.warning("Hausmeister: %s %r ignoriert — nur echte Unterordner des Home", art, wert)


def _echt_unter_home(pfad: Path) -> bool:
    """Absolut und nach Auflösen (Symlinks) echt unter dem Home — nicht Home, nicht darüber."""
    try:
        echt, heim = pfad.resolve(), Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    return pfad.is_absolute() and heim in echt.parents


def _versteckt_unter_home(pfad: Path) -> bool:
    """Ein Teil unterhalb des Home beginnt mit ``.`` (``~/.ssh``, ``~/a/.cache``)."""
    heim = Path.home().parts
    rest = pfad.parts[len(heim) :] if pfad.parts[: len(heim)] == heim else pfad.parts
    return any(teil.startswith(".") for teil in rest)


def _wurzel_erlaubt(muster: str) -> bool:
    """Wurzel-Muster der Liste: Vorgabe (z. B. ``C:/dev/wt-*``) oder fester Teil vor dem
    ersten Glob-Zeichen echt unter dem Home (``~/*``, ``/tmp/*`` fallen weg) und ohne
    versteckten Ordner im ganzen Muster, auch nach dem ersten Glob-Zeichen (``~/.ssh/*``,
    ``~/x/wt-*/.ssh``: Schlüssel/Einstellungen, keine Worktrees; ``..`` fällt mit weg)."""
    if muster in WURZELN_STANDARD:
        return True
    pfad = Path(os.path.expanduser(muster.strip()))
    teile: list[str] = []
    for teil in pfad.parts:
        if glob.has_magic(teil):
            break
        teile.append(teil)
    if teile and _echt_unter_home(Path(*teile)) and not _versteckt_unter_home(pfad):
        return True
    _ablehnen("Wurzel", muster)
    return False


def _basis_muster(basis: str) -> str | None:
    """``worktree_basis`` als Wurzel-Muster ``<basis>/*`` (Glob-Zeichen escaped);
    ``None``, wenn sie kein echter Unterordner des Home ist."""
    pfad = Path(basis.strip()).expanduser()
    if not basis.strip() or not _echt_unter_home(pfad):
        _ablehnen("worktree_basis", basis)
        return None
    return os.path.join(glob.escape(str(pfad)), "*")


def _im_bereich(ordner: Path) -> bool:
    """Aufgelöster Pfad passt auf eine Wurzel und liegt in keinem versteckten Ordner — der
    echte Pfad zählt, nicht der Mustertext (``[.]ssh``, Symlink auf ``.ssh``, #112). Laut git
    eingetragene Arbeitsordner eines Hauptrepos zählen überall mit (#196, gleiche Quelle wie
    :func:`_kandidaten`)."""
    try:
        echt = ordner.resolve()
    except OSError:
        return False
    if _versteckt_unter_home(echt):
        return False
    if any(echt.match(m) for m in _wurzeln()):
        return True
    schluessel = _pfad_schluessel(echt)
    return any(_pfad_schluessel(p) == schluessel for p in _eingetragene_worktrees())


def _nummer(text: str, muster: tuple[re.Pattern[str], ...]) -> int | None:
    for m in muster:
        treffer = m.match(text)
        if treffer:
            return int(treffer.group(1))
    return None


def _origin_slug(echt: Path) -> str:
    """GitHub-Slug des ``origin`` von ``echt`` (``""`` = keiner ⇒ fremdes Repo).

    #235 M2: weicht das Push-Ziel ab (``pushurl``, ``pushInsteadOf``), landete eine Sicherung
    woanders als der Slug sagt ⇒ ``""``. #235 N1: ebenso, wenn ``origin`` mehr als eine
    Adresse hat oder die Repo-eigene Konfiguration (auch per ``include``) Adressen umschreibt
    (``url.<x>.insteadOf``) — fetch/push gingen dann woanders hin als der Slug sagt. Umleitungen
    der Maschine (``--global``/``--system``) gelten für jedes Repo gleich und bleiben erlaubt
    (#347 W4: auch auf einen Spiegel-Pfad außerhalb des Ordners), außer auf einen Transport-Helfer
    (``ext::…``, ``fd::…``): dort führt ein Befehl statt eines Servers die Sicherung aus.
    #254: Konfiguration nicht lesbar (z. B. git < 2.26 ohne ``--show-scope``) ⇒ ``""``.
    """
    roh = (_git_oder_none(echt, "config", "--get-all", "remote.origin.url") or "").splitlines()
    if len(roh) != 1:
        return ""
    konfig = _git_oder_none(echt, "config", "--show-scope", "--list")
    if konfig is None:
        return ""
    for zeile in konfig.splitlines():
        ort, _, eintrag = zeile.partition("\t")
        if ort in ("local", "worktree") and eintrag.startswith("url."):
            return ""
    lesen = _git_oder_none(echt, "remote", "get-url", "origin")
    if lesen is None or lesen != _git_oder_none(echt, "remote", "get-url", "--push", "origin"):
        return ""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", lesen):
        return ""
    # #252: wirksame Adresse nach allen Umleitungen (auch Maschine, includeIf, Umgebung)
    if _ziel_im_ordner(echt, lesen):
        return ""
    return _slug(roh[0])


def _ziel_im_ordner(echt: Path, url: str) -> bool:
    """#252: zeigt ``url`` (Pfad, relativ zu ``echt``, oder ``file://``) auf ``echt`` selbst oder
    darunter? Eine Sicherung dorthin ginge mit dem Ordner verloren. Netz-Adressen ⇒ ``False``."""
    pfad = url.strip().removeprefix("file://")
    if "://" in pfad:
        return False
    if re.match(r"^[^/\\]+:", pfad) and not re.match(r"^[A-Za-z]:[/\\]", pfad):
        return False  # scp-Form host:pfad
    ziel = Path(pfad)
    ordner = echt.resolve()
    ziel = (ziel if ziel.is_absolute() else ordner / ziel).resolve()
    return ziel == ordner or ordner in ziel.parents


def _slug(url: str) -> str:
    """``owner/name`` einer GitHub-URL; alles andere (fremder Host, Pfad, ``http://``) ⇒ ``""``."""
    treffer = _GITHUB_SLUG.match(url.strip())
    return treffer.group(1) if treffer else ""


def _zeitpunkt(text: object) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- Git ------------------------------------------------------------------------


def _git_oder_none(cwd: Path, *args: str) -> str | None:
    try:
        return aufpasser.git_lauf(cwd, *args).strip()
    except RuntimeError:
        return None


def _hat_verknuepfte_worktrees(repo: Path) -> bool:
    for zeile in aufpasser.git_lauf(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :])
            if pfad.is_dir() and pfad.resolve() != repo:
                return True
    return False


def _hauptrepo(worktree: Path) -> Path:
    gemeinsam = Path(aufpasser.git_lauf(worktree, "rev-parse", "--git-common-dir").strip())
    if not gemeinsam.is_absolute():
        gemeinsam = (worktree / gemeinsam).resolve()
    return gemeinsam.parent


def _neu_erzeugbar(pfad: str) -> bool:
    """Kompilat (auch ``.pyc.<zahl>``) unter ``__pycache__`` oder ``data/.secret_key``."""
    teile = pfad.split("/")
    kompilat = re.search(r"\.pyc(?:\.\d+)?$", pfad) is not None
    return pfad == "data/.secret_key" or (kompilat and "__pycache__" in teile[:-1])


def _glob(muster: tuple[str, ...]) -> list[str]:
    return [f":(glob){m}" for m in muster]


def _status(repo: Path, *pathspec: str) -> list[tuple[str, str]]:
    """``(XY, Pfad)`` aller geänderten, neuen und ignorierten Dateien (einzeln)."""
    roh = aufpasser.git_lauf(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored",
        "--",
        *pathspec,
    )
    teile = roh.split("\0")
    eintraege: list[tuple[str, str]] = []
    i = 0
    while i < len(teile):
        teil = teile[i]
        i += 1
        if not teil:
            continue
        xy = teil[:2]
        eintraege.append((xy, teil[3:]))
        if xy[0] in "RC":
            i += 1  # Herkunftspfad der Umbenennung überspringen
    return eintraege


def _gleiche_kopie(haupt: Path | None, repo: Path, rel: str) -> bool:
    if haupt is None:
        return False
    hier, dort = repo / rel, haupt / rel
    try:
        return hier.is_file() and dort.is_file() and hier.read_bytes() == dort.read_bytes()
    except OSError:
        return False


def _ungesichertes(repo: Path, ist_worktree: bool) -> tuple[list[str], bool, list[tuple[str, str]]]:
    """Regel 8: (ungepushte Köpfe, zu sichernde Dateien da?, Status-Schnappschuss).

    Alles, was die Sicherung nicht retten kann, wirft ``_Bleibt``.
    """
    try:
        aufpasser.git_lauf(repo, "fetch", "--prune", "-q", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"git fetch origin gescheitert: {_kurz(fehler)}", fehler=True) from None
    if not ist_worktree and _git_oder_none(repo, "rev-parse", "--verify", "-q", "refs/stash"):
        raise _Bleibt("Stash vorhanden (git stash list)", fuer_david=True)
    if _nur_im_reflog(repo, ist_worktree):
        raise _Bleibt("ungesicherte Commits im Verlauf (Reflog, auf keinem Zweig)", fuer_david=True)
    for zeile in aufpasser.git_lauf(repo, "ls-files", "-s").splitlines():
        if zeile.startswith("160000 "):
            raise _Bleibt(f"Submodul {zeile.split(chr(9), 1)[-1]}", fuer_david=True)

    alle = _status(repo)
    for _, pfad in alle:
        if pfad.endswith("/"):
            raise _Bleibt(f"eingebettetes Repo {pfad}", fuer_david=True)
    erzeugnis = {p for xy, p in _status(repo, *_glob(ERZEUGNISSE)) if xy == "!!"}
    geheim = {p for _, p in _status(repo, *_glob(aufpasser.SICHERUNG_AUSSCHLUSS))}
    # Nur sicher neu Erzeugbares ist nie ein Geheimnis (__pycache__/tempo_zugang.*.pyc).
    # Ein .env unter .review/, node_modules/, .to-spawn/ bleibt Geheimnis.
    geheim -= {p for p in erzeugnis if _neu_erzeugbar(p)}
    haupt = _hauptrepo(repo) if ist_worktree else None
    ungleich = [p for p in sorted(geheim) if not _gleiche_kopie(haupt, repo, p)]
    if ungleich:
        raise _Bleibt(f"Geheimnis-Datei {ungleich[0]} nicht gleich im Hauptrepo", fuer_david=True)
    # #1536: Datenbanken lädt die Sicherung nie hoch — weg dürfen nur Test-Kopien.
    datenbanken = {p for _, p in _status(repo, *_glob(aufpasser.NIE_HOCHLADEN))}
    fremd = sorted(p for p in datenbanken if not ist_test_db(repo, p))
    if fremd:
        raise _Bleibt(f"Datenbank {fremd[0]} ist keine Test-Kopie (nicht sicherbar)", fuer_david=True)
    for xy, pfad in alle:
        if xy == "!!" and pfad not in geheim | erzeugnis | datenbanken:
            raise _Bleibt(f"ignorierte Datei {pfad} ist kein bekanntes Erzeugnis")
    nicht_hoch = geheim | datenbanken
    zu_sichern = any(xy != "!!" and p not in nicht_hoch for xy, p in alle)

    koepfe = _ungepushte_koepfe(repo, ist_worktree)
    if koepfe:
        leck = aufpasser.git_lauf(
            repo,
            "log",
            "--format=",
            "--name-only",
            *koepfe,
            "--not",
            "--remotes=origin",
            "--",
            *_glob(aufpasser.SICHERUNG_AUSSCHLUSS),
        ).split()
        if leck:
            raise _Bleibt(f"Geheimnis-Datei {leck[0]} in ungepushtem Commit", fuer_david=True)
    return koepfe, zu_sichern, alle


def _lokale_koepfe(repo: Path, ist_worktree: bool) -> list[str]:
    """Refs, deren Commits auf origin liegen müssen (Klon: alle außer refs/remotes + HEAD; Worktree: HEAD)."""
    refs = ["HEAD"]
    if not ist_worktree:
        refs += [
            r
            for r in aufpasser.git_lauf(repo, "for-each-ref", "--format=%(refname)").split()
            if not r.startswith("refs/remotes/")
        ]
    return refs


#: So viele Reflog-Stände je ``rev-list``-Aufruf (Windows: Kommandozeile höchstens 32 KiB).
_REFLOG_HAPPEN = 200


def _nur_im_reflog(repo: Path, ist_worktree: bool) -> bool:
    """#235 R1: liegt ein Commit nur im Reflog, der mit dem Ordner verschwindet?

    Verschwinden: beim Klon alle Reflogs, beim Worktree der Reflog seines ``HEAD`` (die
    Zweig-Reflogs liegen im Hauptklon und bleiben). Kein Verlust ist, was von einem Zweig auf
    ``origin`` (nach dem ``fetch`` in :func:`_ungesichertes`) oder einem lokalen Ref erreichbar
    ist — lokale Refs prüfen die übrigen Regeln (Sicherung ``sicherung/<N>`` oder bleibt).
    Gewöhnliche alte Stände liegen so längst auf dem Server und halten den Ordner nicht.

    #254: „auf dem Server“ heißt ``ls-remote`` (nicht fälschbare Tracking-Refs). Die Stände
    kommen roh aus den Reflog-Dateien (``git log -g`` überspringt fehlende Objekte still);
    ein fehlendes Objekt, ``core.logAllRefUpdates=false`` (Verlauf fehlt) oder ein anderer
    Ref-Speicher als ``files`` (keine Reflog-Dateien) ⇒ ``_Bleibt``.
    """
    if _git_oder_none(repo, "config", "--bool", "--get", "core.logAllRefUpdates") == "false":
        raise _Bleibt("core.logAllRefUpdates=false: Verlauf (Reflog) nicht prüfbar", fuer_david=True)
    if (_git_oder_none(repo, "config", "--get", "extensions.refStorage") or "files").lower() != "files":
        raise _Bleibt("Ref-Speicher ohne Reflog-Dateien: Verlauf nicht prüfbar", fuer_david=True)
    pfad = Path(aufpasser.git_lauf(repo, "rev-parse", "--git-path", "logs/HEAD" if ist_worktree else "logs").strip())
    pfad = pfad if pfad.is_absolute() else repo / pfad
    dateien = [pfad] if pfad.is_file() else [p for p in pfad.rglob("*") if p.is_file()]
    staende: set[str] = set()
    for datei in dateien:
        for zeile in datei.read_bytes().splitlines():
            felder = zeile.split(b" ", 2)
            if len(felder) < 3 or not all(re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?", f) for f in felder[:2]):
                raise _Bleibt(f"unlesbare Reflog-Zeile in {datei.name}", fehler=True)
            staende |= {f.decode() for f in felder[:2] if f.strip(b"0")}
    sicher = ("--not", "--exclude=refs/remotes/*", "--all", *_server_koepfe(repo))
    liste = sorted(staende)
    for i in range(0, len(liste), _REFLOG_HAPPEN):
        happen = liste[i : i + _REFLOG_HAPPEN]
        try:  # ohne --ignore-missing: ein fehlendes Objekt ist ein Fehler
            aufpasser.git_lauf(repo, "rev-list", "--no-walk", *happen)
        except RuntimeError as fehler:
            raise _Bleibt(f"Reflog verweist auf fehlendes Objekt: {_kurz(fehler)}", fuer_david=True) from None
        if aufpasser.git_lauf(repo, "rev-list", "-n", "1", "--ignore-missing", *happen, *sicher).strip():
            return True
    return False


def _ungepushte_koepfe(repo: Path, ist_worktree: bool) -> list[str]:
    """Refs mit Commits nicht auf origin (laut lokaler Tracking-Refs)."""
    return [
        ref
        for ref in _lokale_koepfe(repo, ist_worktree)
        if aufpasser.git_lauf(repo, "rev-list", "-n", "1", ref, "--not", "--remotes=origin").strip()
    ]


def _koepfe_auf_server(repo: Path, ist_worktree: bool) -> None:
    """#235 M1a: jeder lokale Kopf muss von einem Zweig-Kopf auf dem Server (``ls-remote``)
    erreichbar sein — lokale Tracking-Refs lassen sich fälschen oder per Refspec verengen.
    Server-Köpfe, die lokal fehlen, zählen nicht (``--ignore-missing``). Sonst ``_Bleibt``."""
    auf_server = _server_koepfe(repo)
    for ref in _lokale_koepfe(repo, ist_worktree):
        if aufpasser.git_lauf(repo, "rev-list", "--ignore-missing", "-n", "1", ref, "--not", *auf_server).strip():
            raise _Bleibt(f"{OHNE_NUMMER}: {ref} nicht auf dem Server (ls-remote origin)", fuer_david=True)


def _server_koepfe(repo: Path) -> list[str]:
    """SHAs aller Zweig-Köpfe von ``origin`` laut Server (``ls-remote --heads``); nicht
    erreichbar oder unlesbare Zeile ⇒ ``_Bleibt``.

    ponytail: ein ls-remote je Aufrufer (ohne Nummer zwei je Ordner), alle Köpfe auf einer
    Kommandozeile; zwischenspeichern bzw. in Happen, wenn Zweig-Zahl oder Laufzeit stört."""
    try:
        roh = _git_ohne_frage(repo, "ls-remote", "--heads", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"Server nicht erreichbar (ls-remote): {_kurz(fehler)}", fehler=True) from None
    auf_server: list[str] = []
    for zeile in roh.splitlines():
        if not zeile.strip():
            continue
        treffer = _LS_REMOTE_ZEILE.match(zeile)
        if treffer is None:  # #235 N2: nur „<sha>\trefs/heads/…“ ist eine Server-Angabe
            raise _Bleibt(f"unlesbare ls-remote-Zeile {_kurz(zeile)!r}", fehler=True)
        auf_server.append(treffer.group(1))
    return auf_server


def _sicherung_auf_server(repo: Path, ticket: int, sha: str) -> None:
    """#247: steht ``sicherung/<ticket>`` am Server (``ls-remote``) genau auf ``sha``? Sonst
    ``_Bleibt`` — der Push kann gelungen sein und der Server den Zweig gleich wieder entfernt haben."""
    ref = f"refs/heads/sicherung/{ticket}"
    try:
        roh = _git_ohne_frage(repo, "ls-remote", "origin", ref).strip()
    except RuntimeError as fehler:
        raise _Bleibt(f"sicherung/{ticket} nicht gegenprüfbar (ls-remote): {_kurz(fehler)}", fehler=True) from None
    treffer = _LS_REMOTE_ZEILE.fullmatch(roh)
    if treffer is None or not roh.endswith(f"\t{ref}") or treffer.group(1) != sha:
        stand = treffer.group(1)[:7] if treffer else "fehlt"
        raise _Bleibt(f"sicherung/{ticket} am Server {stand} ≠ gesichert {sha[:7]}", fehler=True)


# --- Prozesse -------------------------------------------------------------------


def _ist_windows() -> bool:
    return _PLATTFORM == "win32"


def _kein_prozess(ordner: Path, *, probe: bool = False) -> None:
    """Regel 7: ``_Bleibt``, falls ein Prozess im Ordner arbeitet (cwd, exe, offene
    Datei) oder das nicht feststellbar ist. Linux über ``/proc``; Windows über die
    CIM-Prozessliste, mit ``probe=True`` (nur echter Lauf, direkt vor dem Löschen)
    zusätzlich die Umbenennungs-Probe (siehe :mod:`to_spawn.hausmeister_windows`)."""
    if _ist_windows():
        try:
            hausmeister_windows.kein_prozess(ordner)
            if probe:
                hausmeister_windows.umbenennen_probe(ordner)
        except hausmeister_windows.Bleibt as b:
            raise _Bleibt(b.grund, fuer_david=b.fuer_david, fehler=b.fehler) from None
        return
    proc = Path("/proc")
    try:
        pids = [int(p.name) for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        raise _Bleibt("Prozessliste nicht lesbar", fehler=True) from None

    def drin(ziel: str | Path) -> bool:
        pfad = Path(ziel)
        return pfad == ordner or pfad.is_relative_to(ordner)

    for pid in pids:
        basis = proc / str(pid)
        try:
            stat = (basis / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # Prozess schon weg
        if stat.rsplit(")", 1)[-1].split()[:1] == ["Z"]:
            continue  # Zombie: kein cwd, keine Dateien
        ziele: list[str] = []
        for teil in ("cwd", "exe"):
            ziel = _link_lesen(pid, basis / teil)
            if ziel is not None:
                ziele.append(ziel)
        try:
            fds = list((basis / "fd").iterdir())
        except OSError as fehler:
            fds = []
            _unlesbar_pruefen(pid, basis, fehler)
        for fd in fds:
            try:
                ziele.append(os.readlink(fd))
            except OSError:
                continue  # inzwischen geschlossen
        if any(drin(z) for z in ziele):
            raise _Bleibt(f"Prozess {pid} arbeitet im Ordner oder hat Dateien offen")


def _link_lesen(pid: int, link: Path) -> str | None:
    try:
        return os.readlink(link)
    except OSError as fehler:
        _unlesbar_pruefen(pid, link.parent, fehler)
        return None


def _unlesbar_pruefen(pid: int, basis: Path, fehler: OSError) -> None:
    """Unlesbares /proc-Feld: Prozess weg ⇒ egal; fremder oder nicht-dumpbarer
    Prozess (PermissionError) ⇒ übersprungen; jeder andere Fehler ⇒ bleibt."""
    # ponytail: PermissionError übersprungen (fremde Nutzer, systemd --user, setuid);
    # die arbeiten nicht in Ticket-Ordnern. Upgrade: Prüfung als root.
    if isinstance(fehler, (FileNotFoundError, ProcessLookupError, PermissionError)):
        return
    if basis.exists():
        raise _Bleibt(f"Prozess {pid}: /proc nicht lesbar ({fehler})")


# --- Vierte Tür: halb angelegter Ordner (#1374) ----------------------------------


def halb_angelegt_entfernen(hauptrepo: Path, worktree: Path, neuer_zweig: str | None = None) -> bool:
    """Entfernt einen eben angelegten Worktree, dessen Füllen gescheitert ist; wirft nie.

    Nur für den Anleger (``nest.worktree_anlegen``) direkt nach dem Fehlschlag: der
    Ordner wurde mit ``--no-checkout`` angelegt und trägt noch keine Arbeit, darum
    gelten die Lösch-Regeln von :func:`aufraeumen` (Ticket zu, 24 h, Sicherung) hier
    nicht. Bliebe er stehen, kehrte der nächste Anleger-Aufruf früh zurück und die
    Session arbeitete in einem leeren Ordner, in dem alles als gelöscht gilt.

    ``neuer_zweig``: Zweig, den der Anleger eben erst erzeugt hat; er geht mit weg.
    Ein schon vorhandener Zweig trägt Arbeit und wird nie übergeben. Commits eines
    vorhandenen Zweigs überleben ``worktree remove`` ohnehin im Hauptrepo.

    Rückgabe ``True`` = Ordner weg. ``False`` = er blieb; Grund im Hausmeister-Log
    (``fehler=True``). Anders als bei :func:`aufraeumen` löscht ein nicht schreibbares
    Log hier trotzdem: der leere Ordner wäre schädlicher als eine fehlende Log-Zeile.
    """
    ticket = _nummer(worktree.name, _name_muster())
    if not _als_worktree_eingetragen(hauptrepo, worktree):
        _log_schreiben(
            Ergebnis(
                worktree,
                "bleibt",
                "halb angelegt, aber kein Worktree dieses Hauptrepos: nicht angefasst",
                ticket,
                trocken=False,
                fehler=True,
            )
        )
        log.error("%s ist kein Worktree von %s, bleibt stehen", worktree, hauptrepo)
        return False
    try:
        # Bekanntes Hauptrepo statt Ableitung aus dem halben Ordner: ist er kein
        # Worktree, liefe git sonst die Eltern hoch und träfe ein fremdes Repo.
        aufpasser.git_lauf(hauptrepo, "worktree", "remove", "--force", str(worktree))
    except (RuntimeError, OSError) as fehler:
        _log_schreiben(
            Ergebnis(
                worktree,
                "bleibt",
                f"halb angelegt, Entfernen scheiterte: {fehler}",
                ticket,
                trocken=False,
                fehler=True,
            )
        )
        log.error("Halber Worktree %s ließ sich nicht entfernen: %s", worktree, fehler)
        return False
    try:
        aufpasser.git_lauf(hauptrepo, "worktree", "prune")
    except (RuntimeError, OSError) as fehler:
        log.warning("git worktree prune nach %s scheiterte: %s", worktree, fehler)
    # Zeile erst nach dem Entfernen: je Ordner genau eine Zeile „gelöscht“ oder „bleibt“.
    _log_schreiben(
        Ergebnis(
            worktree,
            "gelöscht",
            "halb angelegt: Füllen scheiterte (#1374)",
            ticket,
            trocken=False,
        )
    )
    if neuer_zweig:
        try:
            aufpasser.git_lauf(hauptrepo, "branch", "-D", neuer_zweig)
        except (RuntimeError, OSError) as fehler:
            _log_schreiben(
                Ergebnis(
                    worktree,
                    "bleibt",
                    f"halb angelegt, Ordner weg, Zweig {neuer_zweig} blieb: {fehler}",
                    ticket,
                    trocken=False,
                    fehler=True,
                )
            )
            log.error("Neuer Zweig %s blieb stehen: %s", neuer_zweig, fehler)
    return True


def _als_worktree_eingetragen(hauptrepo: Path, worktree: Path) -> bool:
    """True, wenn ``worktree`` in ``git worktree list`` von ``hauptrepo`` steht (kein Hauptordner)."""
    try:
        liste = aufpasser.git_lauf(hauptrepo, "worktree", "list", "--porcelain")
    except (RuntimeError, OSError) as fehler:
        log.warning("git worktree list in %s scheiterte: %s", hauptrepo, fehler)
        return False
    pfade = [z[len("worktree ") :] for z in liste.splitlines() if z.startswith("worktree ")]
    ziel = worktree.resolve()
    return any(Path(x).resolve() == ziel for x in pfade[1:])


# --- Löschen + Log --------------------------------------------------------------


def _loeschen(ordner: Path, ist_worktree: bool) -> None:
    if ist_worktree:
        hauptrepo = _hauptrepo(ordner)
        zweig = _git_oder_none(ordner, "symbolic-ref", "--short", "-q", "HEAD")
        aufpasser.git_lauf(hauptrepo, "worktree", "remove", "--force", str(ordner))
        aufpasser.git_lauf(hauptrepo, "worktree", "prune")
        if zweig and zweig.startswith("ticket/"):  # #1439: wirft nie, entscheidet selbst
            erg = zweig_aufraeumen(hauptrepo, zweig, loeschen=True, ort="lokal")
            if erg.fehler or (erg.art == "bleibt" and erg.fuer_david):
                log.warning("Hausmeister: %s entfernt, Zweig %s bleibt: %s", ordner, zweig, erg.grund)
    elif sys.version_info >= (3, 12):
        shutil.rmtree(ordner, onexc=hausmeister_windows.schreibschutz_weg)
    else:
        shutil.rmtree(ordner, onerror=hausmeister_windows.schreibschutz_weg)


def altbestand_pfad() -> Path:
    """Liste des Laptop-Altbestands (E16), neben dem Hausmeister-Log."""
    return _log_pfad().parent / "altbestand.json"


def altbestand_festhalten(ordner: Iterable[Path]) -> Path:
    """Hält Ordner als Altbestand fest: sie bleiben im echten Lauf, bis der Mensch die Liste
    freigibt (``"freigegeben": true`` in der Datei). Schreibt
    ``{"freigegeben": false, "erstellt": <iso>, "ordner": [<posix, aufgelöst>]}``.
    Gibt es die Datei schon, kommen neue Ordner dazu, keiner geht weg. Ist sie schon
    freigegeben oder kaputt, bleibt sie unverändert (Warnung im Log; eine kaputte Liste
    hält per Regel jeden Ordner fest). Rückgabe: Pfad der Datei. ``OSError`` geht an
    den Aufrufer."""
    pfad = altbestand_pfad()
    neu = [Path(o).expanduser().resolve().as_posix() for o in ordner]
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        daten = {
            "freigegeben": False,
            "erstellt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ordner": [],
        }
    except ValueError:
        log.warning("Hausmeister: Altbestand-Liste %s kaputt — nicht ergänzt", pfad)
        return pfad
    liste = daten.get("ordner") if isinstance(daten, dict) else None
    if not isinstance(liste, list):
        log.warning("Hausmeister: Altbestand-Liste %s kaputt — nicht ergänzt", pfad)
        return pfad
    if daten.get("freigegeben") is True:
        log.warning(
            "Hausmeister: Altbestand-Liste %s ist schon freigegeben — %d Ordner nicht ergänzt",
            pfad,
            len(neu),
        )
        return pfad
    bekannt = {os.path.normcase(str(o)) for o in liste}
    for o in neu:
        if os.path.normcase(o) not in bekannt:
            bekannt.add(os.path.normcase(o))
            liste.append(o)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zwischen = pfad.with_name(pfad.name + ".neu")
    zwischen.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(zwischen, pfad)  # nie eine halb geschriebene Liste
    return pfad


def _kein_altbestand(echt: Path) -> None:
    """Altbestand-Regel (nur echter Lauf): gelistet und nicht freigegeben ⇒ bleibt;
    Datei kaputt/unlesbar ⇒ jeder Ordner bleibt mit Fehler. Datei fehlt: am Server keine
    Wirkung; unter Windows hält der Lauf zuerst alle vorhandenen Ordner fest — der
    erste Lauf am Laptop löscht nie ohne das Ja des Menschen."""
    pfad = altbestand_pfad()
    if _ist_windows() and not os.path.lexists(pfad):
        try:
            altbestand_festhalten([*_kandidaten(), echt])
        except OSError as fehler:
            raise _Bleibt(f"Altbestand-Liste {pfad} nicht anlegbar: {fehler}", fehler=True) from None
        log.warning(
            "Hausmeister: Altbestand-Liste fehlte — alle Ordner festgehalten in %s",
            pfad,
        )
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):  # Datei gibt es nicht (Server)
        return
    except (OSError, ValueError) as fehler:
        raise _Bleibt(f"Altbestand-Liste {pfad} unlesbar: {fehler}", fehler=True) from None
    liste = daten.get("ordner") if isinstance(daten, dict) else None
    if not isinstance(liste, list) or not all(isinstance(o, str) for o in liste):
        raise _Bleibt(f"Altbestand-Liste {pfad} kaputt", fehler=True)
    if daten.get("freigegeben") is True:
        return
    schluessel = os.path.normcase(str(echt))
    if any(os.path.normcase(str(Path(o))) == schluessel for o in liste):
        raise _Bleibt(f"Altbestand, wartet auf das Ja des Menschen (Liste {pfad})")


def _kennzahl_schreiben(ergebnisse: list[Ergebnis], *, trocken: bool) -> None:
    """#1536: je Takt eine Zeile ``art="kennzahl"`` in :func:`_kennzahl_pfad` — „x von y gelöscht, z bleiben“
    und die Bleib-Gründe gezählt (Zahlen im Grund als ``N``, ohne Klammer-Zusatz)."""
    geloescht = sum(1 for e in ergebnisse if e.art != "bleibt")
    gruende: dict[str, int] = {}
    fehler_je: dict[str, list[str]] = {}
    for e in ergebnisse:
        if e.art == "bleibt":
            schluessel = re.sub(r"\d+", "N", e.grund.split(" (", 1)[0])
            gruende[schluessel] = gruende.get(schluessel, 0) + 1
            if e.fehler:
                fehler_je.setdefault(schluessel, []).append(f"{e.ordner}: {e.grund}")
    satz = f"{geloescht} von {len(ergebnisse)} gelöscht, {len(ergebnisse) - geloescht} bleiben"
    log.info("Hausmeister: Kennzahl %s%s", satz, " (trocken)" if trocken else "")
    # #235: eine Zeile je Bleib-Grund mit Anzahl; Fehler zusätzlich mit Ordner und Text
    for schluessel, anzahl in sorted(gruende.items(), key=lambda kv: -kv[1]):
        fehl = fehler_je.get(schluessel)
        log.info("Hausmeister: bleibt %d× %s%s", anzahl, schluessel, f" — Fehler: {' | '.join(fehl)}" if fehl else "")
    _log_zeile(
        {
            "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
            "art": "kennzahl",
            "grund": satz,
            "trocken": trocken,
            "gesamt": len(ergebnisse),
            "geloescht": geloescht,
            "bleiben": len(ergebnisse) - geloescht,
            "fehler": sum(1 for e in ergebnisse if e.fehler),
            "bleib_gruende": dict(sorted(gruende.items(), key=lambda kv: -kv[1])),
        },
        _kennzahl_pfad(),
    )


def _kennzahl_pfad() -> Path:
    return _log_pfad().with_name(KENNZAHL_DATEI)


def _log_pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_LOG")
    return Path(roh).expanduser() if roh else Path.home() / ".claude" / "to-spawn" / "hausmeister.jsonl"


def _log_schreiben(erg: Ergebnis) -> bool:
    """Eine JSON-Zeile anhängen; False = nicht schreibbar."""
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ordner": str(erg.ordner),
        "art": erg.art,
        "grund": erg.grund,
        "ticket": erg.ticket,
        "sicherung": erg.sicherung,
        "trocken": erg.trocken,
        "fuer_david": erg.fuer_david,
        "fehler": erg.fehler,
    }
    log.info("Hausmeister: %s — %s (%s)", erg.ordner, erg.art, erg.grund)
    return _log_zeile(zeile)


def _log_zeile(zeile: dict[str, object], datei: Path | None = None) -> bool:
    """Eine JSON-Zeile ins Hausmeister-Log (oder ``datei``); False = nicht schreibbar."""
    try:
        datei = datei or _log_pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Hausmeister-Log nicht schreibbar: %s", fehler)
        return False
    return True
