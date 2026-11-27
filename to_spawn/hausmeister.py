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

#1536/#163/#241 — Behalten ist die Ausnahme mit echtem Grund. Ordner ganz ohne Git (kein
``.git`` oder verwaister Worktree, dessen Hauptrepo/Eintrag fehlt) mit Ticket-Nummer im
Namen werden geräumt, wenn alles gilt: kein Prozess darin, keine laufende Spec-Sitzung,
kein offenes Ticket zur Nummer (alle Ticket-Repos der Maschine geprüft, fail-closed),
nichts jünger als :data:`OHNE_GIT_ALTER_SEKUNDEN`, kein Geheimnis-Verdacht, keine fremde
Datenbank, kein eingebettetes Repo und kein Lesefehler. Ohne Ticket-Nummer im Namen
werden solche Ordner (#241) zusätzlich nur dann geräumt, wenn ihr gesamter Nicht-Erzeugnis-
Inhalt bytegleich einem Commit auf ``origin`` eines bekannten eigenen Hauptrepos ist; sonst
bleiben sie (``fuer_mensch=True``).
#225 — Gesperrte Worktrees (``locked``) bleiben als Regel (kein Fehler); eingetragene
Worktrees ohne Ticket-Nummer außerhalb der Wurzeln (z. B. ``deploy-tmp``) bleiben still
stehen (kein ``fuer_mensch``).
Unterordner eines fremden Repos und unlesbare Git-Ordner bleiben. Je Takt schreibt :func:`aufraeumen_alles` eine Zeile
nach :data:`KENNZAHL_DATEI` neben dem Log („x von y gelöscht, z bleiben“, Bleib-Gründe
gezählt) — eigene Datei, damit Leser des Ordner-Logs nur Ordner-Zeilen sehen.

Ungesichert heißt: geänderte/neue Dateien oder Commits auf keinem Remote. Vorher
``git fetch --prune origin`` (veraltete Tracking-Refs täuschen „gepusht“ vor), dann
zählt der erreichbare Server-Stand (``ls-remote --heads origin``, #179/#242). Beim Klon (``.git`` ist Ordner) zählen alle lokalen
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
  gleichem Inhalt hat; sonst (und beim Klon immer) bleibt, ``fuer_mensch=True``.
  Geheimnisse in ungepushten Commits würde die Sicherung mit hochladen ⇒ bleibt, ``fuer_mensch=True``.
- Ignorierte Dateien: nur bekannte Erzeugnisse (:data:`ERZEUGNISSE`) dürfen weg,
  jede andere ignorierte Datei ⇒ bleibt.
- Datenbanken (``aufpasser.NIE_HOCHLADEN``) lädt die Sicherung nie hoch: weg dürfen nur
  kleine Test-Kopien (:func:`ist_test_db`), jede andere ⇒ bleibt, ``fuer_mensch=True``.
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
„bleibt“. :func:`liste_fuer_mensch` liest daraus die Ordner, über die der Mensch selbst
entscheiden muss.

Zweite Tür (#1369): :func:`aufraeumen_alles` wendet dieselben Regeln auf alle Ordner
unter den Wurzeln an. Im Aufpasser-Takt (:meth:`to_spawn.aufpasser.Aufpasser._lauf`,
Entscheidung #262) läuft die Deploy-Wache immer VOR :func:`aufraeumen_alles`, sodass ein
laufender Deploy/Prüflauf auch das Aufräumen sperrt; bei Direktaufrufen (CLI, Windows-Aufgabe,
Start-Check) greifen die eigenen Sperren je Ordner (Prozess im Ordner, offenes Ticket, 24 h).
Platznot (weniger als :data:`PLATZNOT_ANTEIL` der Platte frei):
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
ausgecheckt). Ohne Ticket-Nummer bleibt der Zweig und steht in :func:`liste_fuer_mensch`.
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
import hashlib
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import EllipsisType
from typing import Any, Literal

from to_spawn import (
    aufpasser,
    config,
    docker_reste,
    gh,
    hausmeister_windows,
    hindernis,
    prozessbaum,
    starter,
    temp_ordner,
    ticket_zustand,
    tmux_aufruf,
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


def _ticket_repo_aus(repos: Sequence[Mapping[str, Any]]) -> str | None:
    """Das eine Ticket-Repo aller Einträge; ``None`` = keins, mehrere oder unklar.

    Nie aus dem ``slug`` geraten: ein falsches Repo könnte ein fremdes geschlossenes #N
    melden. Leeres ``ticket_repo`` gilt nur, wenn der ``slug`` selbst das ausdrückliche
    Ticket-Repo eines anderen Eintrags ist (Vorgabe: DuoPlus, Skill-Ordner fragen dort).
    """
    explizit = {t for e in repos if (t := (e.get("ticket_repo") or "").strip())}
    if _ohne_ticket_repo(repos):
        return None
    return next(iter(explizit)) if len(explizit) == 1 else None


def _ohne_ticket_repo(repos: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Einträge ohne brauchbares Ticket-Repo: kein ``ticket_repo`` und der ``slug`` ist nicht
    das ausdrückliche Ticket-Repo eines Eintrags (z. B. Wegwerf-Klon, #112)."""
    bekannt = {t.lower() for e in repos if (t := (e.get("ticket_repo") or "").strip())}
    return [
        e
        for e in repos
        if not (e.get("ticket_repo") or "").strip() and (e.get("slug") or "").strip().lower() not in bekannt
    ]


def _hauptrepo_namen_aus(repos: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    namen: list[str] = []
    for e in repos:
        p = (e.get("pfad") or "").strip()
        if not p:
            continue
        # Für ``~/foo`` oder absolute Pfade reicht ``.name`` ohne Heimordner-Auflösung (#212);
        # nur ein nacktes ``~`` braucht den aufgelösten Ordnernamen.
        pfad = config.heim_ordner() if p.rstrip("/\\") == "~" else Path(p)
        namen.append(pfad.name)
    return tuple(namen)


def _remotes_aus(repos: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(f"https://github.com/{s}" for e in repos if (s := (e.get("slug") or "").strip()))


_VORGABE_REPOS: list[dict[str, Any]] = config.MASCHINE_DEFAULTS["repos"]
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


def _maschinen_repos() -> list[dict[str, Any]]:
    return config.maschine()["repos"]


def _ticket_repo() -> str | None:
    """Repo für ``gh issue view``; ``None`` = keins oder nicht eindeutig (⇒ Ticket nicht abfragbar).

    Nur noch Rückfall ohne GitHub-origin; je Ordner/Zweig gilt :func:`_ticket_repo_fuer` (#176).
    """
    return _ticket_repo_aus(_maschinen_repos())


def _alle_ticket_repos() -> list[str]:
    """Alle bekannten Ticket-Repos der Maschine (ohne Doppelte, Originalschreibweise)."""
    gesehen: dict[str, str] = {}
    for eintrag in _maschinen_repos():
        wert = (eintrag.get("ticket_repo") or "").strip()
        if wert and wert.lower() not in gesehen:
            gesehen[wert.lower()] = wert
        zuordnung, _ = config.zweig_ticket_repo_pruefen(eintrag.get("zweig_ticket_repo"))
        if zuordnung:
            for ziel in zuordnung.values():
                if ziel and ziel.lower() not in gesehen:
                    gesehen[ziel.lower()] = ziel
    return list(gesehen.values())


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


def _kaputten_eintrag_melden(eintrag: Mapping[str, Any]) -> None:
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


def _zweig_ticket_repo_fehler_melden(eintrag: Mapping[str, Any], grund: str) -> None:
    schluessel = f"zweig-ticket-repo:{eintrag.get('pfad')}:{eintrag.get('slug')}:{grund}"
    if schluessel not in _ABGELEHNT_GEMELDET:
        _ABGELEHNT_GEMELDET.add(schluessel)
        log.warning(
            "Hausmeister: zweig_ticket_repo für slug=%r pfad=%r in %s ungültig (%s) — "
            "Zweige und Ordner bleiben fail-closed",
            eintrag.get("slug"),
            eintrag.get("pfad"),
            config.maschine_datei(),
            grund,
        )


def _zweig_ticket_repo_regeln(slug: str) -> tuple[dict[str, str] | None, bool]:
    """Gültige ``zweig_ticket_repo``-Zuordnung für ``slug`` aus der Maschinen-Liste (#347).

    Rückgabe ``(regeln, ok)``:
    - ``ok=True``: gültige Zuordnung (leer = keine Sonderregeln).
    - ``ok=False`` (``regeln=None``): kaputte oder widersprüchliche Einträge ⇒ fail-closed.
    """
    gesucht = slug.strip().lower()
    if not gesucht:
        return {}, True
    passende = [e for e in _maschinen_repos() if (e.get("slug") or "").strip().lower() == gesucht]
    if not passende:
        return {}, True
    normalisiert: list[tuple[frozenset[tuple[str, str]], dict[str, str]]] = []
    for eintrag in passende:
        zuordnung, fehler = config.zweig_ticket_repo_pruefen(eintrag.get("zweig_ticket_repo"))
        if zuordnung is None:
            _zweig_ticket_repo_fehler_melden(eintrag, fehler)
            return None, False
        normalisiert.append((frozenset((k, v.lower()) for k, v in zuordnung.items()), zuordnung))
    if len({sig for sig, _ in normalisiert}) > 1:
        _zweig_ticket_repo_fehler_melden(passende[0], "widersprüchliche Einträge zum selben slug")
        return None, False
    return normalisiert[0][1], True


def _zweig_ticket_repo_treffer(zweig: str, regeln: Mapping[str, str]) -> str | None:
    """Längster passender Klartext-Präfix aus ``regeln`` → Ticket-Repo oder ``""``; ``None`` = kein Treffer."""
    treffer = [praefix for praefix in regeln if zweig.startswith(praefix)]
    if not treffer:
        return None
    return regeln[max(treffer, key=len)]


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
#: #163: Alters-Schwelle für verwaiste Ordner ohne eigenes Git (jüngste Änderung im Baum).
OHNE_GIT_ALTER_SEKUNDEN = MINDEST_ZU_SEKUNDEN
#: #163: Geheimnis-Verdacht für Ordner ohne Git (erweitert ``aufpasser.SICHERUNG_AUSSCHLUSS``).
OHNE_GIT_GEHEIMNISSE = (
    *aufpasser.SICHERUNG_AUSSCHLUSS,
    "**/credentials.json",
    "**/*.p12",
    "**/*.pfx",
    "**/id_rsa",
    "**/id_ed25519",
)
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
#: Ab diesem belegten Inode-Anteil der Temp-Wurzel warnt und räumt der Hausmeister (#355);
#: einzige Stelle der Schwelle (auch für ``tests/conftest.py``).
INODE_WARN_ANTEIL = 0.70
#: Ab diesem belegten Inode-Anteil der Temp-Wurzel startet keine neue Session (#355).
INODE_SPERRE_ANTEIL = 0.90
#: Hindernis-Arten des Start-Checks; ein gesunder Check schließt alle (#1471, #896).
#: ``_spur`` nimmt nur diese Konstanten (Test ``test_jede_gemeldete_art_wird_geschlossen``).
ART_PLATTE_VOLL = "platte_voll"
ART_PLATTE_UNMESSBAR = "platte_unmessbar"
ART_HAUSMEISTER_FEHLER = "hausmeister_fehler"
ART_TEMP_INODES_VOLL = "temp_inodes_voll"
ART_TEMP_INODES_KNAPP = "temp_inodes_knapp"
HINDERNIS_ARTEN = (
    ART_PLATTE_VOLL,
    ART_PLATTE_UNMESSBAR,
    ART_HAUSMEISTER_FEHLER,
    ART_TEMP_INODES_VOLL,
    ART_TEMP_INODES_KNAPP,
)
#: Marker-Datei eigener pytest-Temp-Ordner ``pt-*`` (#177, #355) — einzige Quelle auch für ``tests/conftest.py``.
PT_MARKER = ".pt-lauf"
#: Normale Schonfrist toter ``pt-*``-Reste in Sekunden (#177, #355).
PT_RESTE_ALTER_S = 3600
#: Verkürzte Schonfrist toter ``pt-*``-Reste bei Inode-Knappheit ab :data:`INODE_WARN_ANTEIL` (#355).
PT_RESTE_ALTER_KNAPP_S = 600
#: Läuft gerade :func:`_start_pruefen`? Dann schreibt :func:`_alle_ordner` keine doppelte Inode-Warnung.
_IM_STARTCHECK: ContextVar[bool] = ContextVar("hausmeister_im_startcheck", default=False)
#: Sperrdatei gegen parallele Aufräum-Läufe, im Ordner des Hausmeister-Logs.
SPERR_DATEI = "hausmeister.lock"
#: #781: Mindestabstand (s) zwischen zwei Hintergrund-Anstößen bei Platznot im Start-Check.
HINTERGRUND_ABSTAND_S = 600
_HINTERGRUND_STEMPEL = "start-hintergrund.stempel"
_CLI = Path(__file__).resolve().parent.parent / "to_spawn.py"
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
#: (samt ``-wal``/``-shm``). So legen App und Tests sie an (App-DB-Pfad u. a.).
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
#: (``l1/2026-11-21-stand``, ``fix/2026-11``, auch ``ticket/2026-13-x``) — im Zweig-Pfad bleibt ein
#: Zweig ohne Nummer stehen; ein Ordner mit Datums-Zweig läuft über den Weg „ohne Nummer“.
_KEIN_DATUM = r"(?!20\d\d-\d)"
_ZWEIG_MUSTER = re.compile(rf"^(?:(?:ticket|fix|wip|aufseher)/|fix-|abschluss-){_KEIN_DATUM}{_TICKET_NR}(?:-.*)?$")
#: #235: Etappen-Zweig ``l<Etappe>/<N>[buchstabe]-…`` (``l4/176-…``, ``l1/214a-…``); erst ab
#: 2 Ziffern — ``l3/3b-1b-…`` ist ein Etappen-Schritt, kein Issue, ``l1/p7f-…`` ebenso.
_ZWEIG_MUSTER_ETAPPE = re.compile(rf"^l\d+/{_KEIN_DATUM}([1-9]\d{{1,2}}|[1-4]\d{{3}})[a-z]?(?:-.*)?$")
#: Alle Zweig-Formen mit Ticket-Nummer — die einzige Stelle „Zweigname → Nummer“ (#248, auch
#: für das Zweig-Aufräumen, :func:`_zweig_ticket`); welches Repo die Nummer meint, bestimmen
#: ``zweig_ticket_repo`` (optional je Repo in der Maschinen-Liste, #347) und sonst ``origin`` —
#: Zweig + origin → Repo + Nummer nur über :func:`zweig_zu_ticket` (#234, #357).
_ZWEIG_FORMEN = (_ZWEIG_MUSTER, _ZWEIG_MUSTER_ETAPPE)
#: #235 N2: eine Zeile von ``git ls-remote --heads`` (SHA-1 oder SHA-256, Tab, Zweig).
_LS_REMOTE_ZEILE = re.compile(r"([0-9a-f]{40}(?:[0-9a-f]{24})?)\trefs/heads/\S+$")


@dataclass(frozen=True)
class Ergebnis:
    ordner: Path
    art: Art
    grund: str
    ticket: int | None
    sicherung: str | None = None
    fuer_mensch: bool = False
    trocken: bool = True
    fehler: bool = False  # True = „bleibt“ wegen eines Fehlers, nicht wegen einer Regel

    def __init__(
        self,
        ordner: Path,
        art: Art,
        grund: str,
        ticket: int | None,
        sicherung: str | None = None,
        fuer_mensch: bool = False,
        trocken: bool = True,
        fehler: bool = False,
        *,
        fuer_david: bool | None = None,  # Übergangs-Alias (#325)
    ) -> None:
        object.__setattr__(self, "ordner", ordner)
        object.__setattr__(self, "art", art)
        object.__setattr__(self, "grund", grund)
        object.__setattr__(self, "ticket", ticket)
        object.__setattr__(self, "sicherung", sicherung)
        object.__setattr__(self, "fuer_mensch", bool(fuer_mensch or fuer_david))  # Übergangs-Alias (#325)
        object.__setattr__(self, "trocken", trocken)
        object.__setattr__(self, "fehler", fehler)

    @property
    def fuer_david(self) -> bool:  # Übergangs-Alias (#325)
        """Übergangs-Alias für :attr:`fuer_mensch` (#325)."""
        return self.fuer_mensch


class _Bleibt(Exception):
    """Eine Regel (oder ein Fehler) hält den Ordner fest."""

    def __init__(
        self,
        grund: str,
        *,
        fuer_mensch: bool = False,
        fuer_david: bool = False,  # Übergangs-Alias (#325)
        fehler: bool = False,
    ) -> None:
        super().__init__(grund)
        self.grund = grund
        self.fuer_mensch = bool(fuer_mensch or fuer_david)  # Übergangs-Alias (#325)
        self.fehler = fehler

    @property
    def fuer_david(self) -> bool:  # Übergangs-Alias (#325)
        """Übergangs-Alias für :attr:`fuer_mensch` (#325)."""
        return self.fuer_mensch


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


def rundgang(*, loeschen: bool, jetzt: float | None = None, docker: bool = False) -> Rundgang:
    """Wie :func:`aufraeumen_alles`, sagt aber, ob überhaupt aufgeräumt wurde; wirft nie.

    Für Aufrufer, die „erledigt“ oder den Exit-Code des Gesamtlaufs festhalten
    (capo-Schlussschritt, CLI ``aufraeumen --alles``): belegte Sperre, gescheiterte
    Ordnersuche und jeder unerwartete Fehler ergeben ``gelaufen=False`` mit Klartext-Grund
    statt einer leeren Liste, die wie „nichts zu tun“ aussieht. Mit ``docker=True`` gibt
    derselbe Lauf unter gehaltener Sperre einmal täglich Docker-Reste frei (#1372).
    """
    return _rundgang_sicher(loeschen=loeschen, jetzt=jetzt, docker=docker)


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
    wt_token = _WORKTREE_CACHE.set({})
    origin_token = _ORIGIN_BAEUME_CACHE.set({})
    ticket_token = _TICKET_DATEN_CACHE.set({})
    try:
        try:
            kandidaten = _kandidaten()
            anteil = _frei_anteil()
            if _ist_platznot(anteil):
                # #781: Ticket-Daten aus _schliesszeit bleiben über _TICKET_DATEN_CACHE für _regeln erhalten.
                zeiten = {k: _schliesszeit(k) for k in kandidaten}
                kandidaten.sort(key=lambda k: (zeiten[k] is None, zeiten[k] or 0.0, str(k)))
        except Exception as fehler:  # Vertrag: wirft nie
            log.exception("Hausmeister: Aufräumen gescheitert (Ordnersuche/Reihenfolge)")
            _zweige_im_takt(loeschen, jetzt)
            return Rundgang(False, f"Ordnersuche gescheitert ({type(fehler).__name__}: {fehler})")
        ergebnisse: list[Ergebnis] = []
        with (
            prozessbaum.arbeitsordner_lauf_cache() if _ist_windows() else nullcontext(),
            hausmeister_windows.prozess_lauf_cache() if _ist_windows() else nullcontext(),
        ):
            for ordner in kandidaten:
                erg = _aufraeumen(ordner, loeschen=loeschen, jetzt=jetzt, platznot=_ist_platznot(anteil))
                ergebnisse.append(erg)
                if erg.art != "bleibt" and not erg.trocken:
                    anteil = _frei_anteil()
    finally:
        _TICKET_DATEN_CACHE.reset(ticket_token)
        _ORIGIN_BAEUME_CACHE.reset(origin_token)
        _WORKTREE_CACHE.reset(wt_token)
    _kennzahl_schreiben(ergebnisse, trocken=not loeschen)
    temp_ordner.aufraeumen(loeschen=loeschen, jetzt=jetzt)  # #136: Temp-Reste, wirft nie
    _pt_reste_im_takt(loeschen=loeschen, jetzt=jetzt)  # #355: tote markierte pt-*-Reste räumen (Linux)
    _temp_inodes_im_takt()  # #355: Datei-Zähler (Inodes) der Temp-Wurzel prüfen
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


def start_erlaubt(
    *, loeschen: bool = True, nur_messen: bool = False, synchron_nur_bei_sperre: bool = False
) -> tuple[bool, str]:
    """Start-Check: (darf eine neue Session starten?, Satz für Menschen); wirft nie.

    Bei Platznot erst :func:`aufraeumen_alles`, dann neu messen (ohne Platznot lohnt
    der Lauf mit seinen gh-Abfragen nicht — die Platte bliebe über der Startsperre).
    ``loeschen=False`` (Probelauf, Status) misst nur und löscht nie. Messung unmöglich
    oder unerwarteter Fehler → Start erlaubt, mit Spur in cron.out und Hindernis-Log.
    ``nur_messen=True`` (#617): auch bei Platznot kein Rundgang (der braucht gh/git je Ordner,
    Minuten) — nur Messung und Urteil; der Aufrufer räumt bei „voll“ erneut ohne den Schalter.
    ``synchron_nur_bei_sperre=True`` (#781): synchroner Rundgang nur unterhalb der Startsperre
    (bzw. bei vollen Temp-Inodes); bei bloßer Platznot darüber stößt Linux nur gedrosselt im
    Hintergrund an, sodass der Session-Start nicht minutenlang blockiert.
    """
    try:
        return _start_pruefen(loeschen, nur_messen, synchron_nur_bei_sperre)
    except Exception as fehler:  # Vertrag: wirft nie, im Zweifel starten
        log.exception("Hausmeister: Start-Check gescheitert")
        satz = f"Platte: Start-Check gescheitert ({fehler}) — Start erlaubt"
        _spur(ART_HAUSMEISTER_FEHLER, satz)
        return True, satz


def _ist_start_gesperrt(anteil: float | None, inodes: float | None) -> bool:
    """Greift die Startsperre (Platte unter :data:`STARTSPERRE_ANTEIL` oder Inodes ab :data:`INODE_SPERRE_ANTEIL`)?"""
    return (anteil is not None and anteil < STARTSPERRE_ANTEIL) or _ist_inode_voll(inodes)


def _start_sperrt() -> bool:
    """Nur messen: greift gerade die Startsperre (Platte oder Temp-Inodes)?

    Für :func:`to_spawn.speicher.auf_platte_warten` (#781): nur dann läuft ein synchroner
    Rundgang und die Vorab-Wartezeile ist angebracht. Messfehler → ``True``.
    """
    try:
        return _ist_start_gesperrt(_frei_anteil(), _temp_inode_anteil())
    except Exception:  # Messfehler: vorsichtshalber melden, Start-Check entscheidet
        log.exception("Hausmeister: Vorab-Messung gescheitert")
        return True


def _hintergrund_stempel() -> Path:
    return _log_pfad().parent / _HINTERGRUND_STEMPEL


def _hintergrund_anstossen() -> bool:
    """#781 (Linux-Teil): stößt bei Platznot über der Startsperre einen abgelösten
    Hausmeister-Rundgang im Hintergrund an, gedrosselt über :data:`HINTERGRUND_ABSTAND_S`.

    Gibt ``True`` bei Anstoß zurück, sonst ``False`` (Windows-Zweig unberührt, frischer
    Stempel, Test-Sperre oder Start-Fehler). Wirft nie.
    """
    if _ist_windows():
        return False
    stempel = _hintergrund_stempel()
    try:
        if 0 <= time.time() - stempel.stat().st_mtime < HINTERGRUND_ABSTAND_S:  # Zukunft = Uhr-Sprung
            return False
    except OSError:
        pass
    fehlerdatei = _log_pfad().parent / "hausmeister-start.err"
    try:
        fehlerdatei.parent.mkdir(parents=True, exist_ok=True)
        with fehlerdatei.open("ab") as stderr:
            starter.starte(  # einzige Start-Tür (#1406)
                [sys.executable, str(_CLI), "aufraeumen", "--alles", "--loeschen"],
                notnagel=None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )
    except (OSError, starter.StartGesperrt) as fehler:
        log.debug("Hausmeister: Hintergrund-Anstoß übersprungen (%s)", fehler)
        return False
    try:
        stempel.parent.mkdir(parents=True, exist_ok=True)
        stempel.touch()
    except OSError as fehler:
        log.warning("Hausmeister: Hintergrund-Stempel %s nicht schreibbar (%s)", stempel, fehler)
    return True


def _start_pruefen(loeschen: bool, nur_messen: bool = False, synchron_nur_bei_sperre: bool = False) -> tuple[bool, str]:
    anteil = _frei_anteil()
    inodes = _temp_inode_anteil()
    zusatz = ""
    knapp = _ist_platznot(anteil) or _ist_inode_knapp(inodes)
    gesperrt = _ist_start_gesperrt(anteil, inodes)
    if not nur_messen and (gesperrt if synchron_nur_bei_sperre else knapp):
        token = _IM_STARTCHECK.set(True)
        try:
            rund = rundgang(loeschen=loeschen)
        finally:
            _IM_STARTCHECK.reset(token)
        if not rund.gelaufen and not rund.wartet:
            zusatz = " (Aufräumen gescheitert, siehe Log)"
        anteil = _frei_anteil()
        inodes = _temp_inode_anteil()
    elif not nur_messen and synchron_nur_bei_sperre and knapp and loeschen:
        _hintergrund_anstossen()
    if _ist_inode_voll(inodes):
        assert inodes is not None
        nach = "trotz gescheitertem Aufräumen" if zusatz else "auch nach dem Aufräumen"
        satz = (
            f"Temp-Wurzel voll: {_prozent(inodes)} der Inodes belegt (Startsperre ab "
            f"{_prozent(INODE_SPERRE_ANTEIL)}), {nach} — keine neue Session"
        )
        _spur(ART_TEMP_INODES_VOLL, satz, inodes_belegt=round(inodes, 4))
        return False, satz
    if anteil is None:
        satz = f"Platte nicht messbar — kein Urteil, Start erlaubt{zusatz}"
        _spur(ART_PLATTE_UNMESSBAR, satz)
        return True, satz
    if anteil < STARTSPERRE_ANTEIL:
        nach = "trotz gescheitertem Aufräumen" if zusatz else "auch nach dem Aufräumen"
        satz = (
            f"Platte voll: nur {_prozent(anteil)} frei (Startsperre unter "
            f"{_prozent(STARTSPERRE_ANTEIL)}), {nach} — keine neue Session"
        )
        _spur(ART_PLATTE_VOLL, satz, frei_anteil=round(anteil, 4))
        return False, satz
    if _ist_inode_knapp(inodes):
        assert inodes is not None
        warn = f"Temp-Wurzel knapp: {_prozent(inodes)} der Inodes belegt (Warnschwelle {_prozent(INODE_WARN_ANTEIL)})"
        log.warning("Hausmeister: %s", warn)
        _spur(ART_TEMP_INODES_KNAPP, warn, inodes_belegt=round(inodes, 4))
        return True, f"Platte frei: {_prozent(anteil)} frei{zusatz} ({warn})"
    hindernis.erledigt(*HINDERNIS_ARTEN)  # #1471/#896: gesunder Check = Hindernisse vorbei
    return True, f"Platte frei: {_prozent(anteil)} frei{zusatz}"


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
    except config.KeinHeimOrdner as fehler:
        log.error("Hausmeister: %s (%s)", fehler, ordner)
        erg = _bleibt(ordner, trocken, stand, _Bleibt(f"Fehler: {_kurz(fehler)}", fehler=True))
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


def liste_fuer_mensch(log_datei: Path | None = None) -> list[Path | str]:
    """Ordner aller Log-Zeilen mit ``fuer_mensch=True``, jeder nur einmal (Reihenfolge des Logs).

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
        if not isinstance(daten, dict):
            continue
        fuer_mensch = daten.get("fuer_mensch") or daten.get("fuer_david")  # Übergangs-Alias (#325)
        if not fuer_mensch:
            continue
        if daten.get("ordner"):
            gesehen.setdefault(Path(daten["ordner"]), None)
        elif daten.get("zweig"):
            gesehen.setdefault(f"{daten.get('remote') or ''} {daten['zweig']}", None)
    return list(gesehen)


liste_fuer_david = liste_fuer_mensch  # Übergangs-Alias (#325)


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
    fuer_mensch: bool = False

    def __init__(
        self,
        remote: str,
        zweig: str,
        sha: str,
        angelegt: float | None,
        art: Literal["gelöscht", "gesichert+gelöscht", "bleibt"],
        grund: str,
        trocken: bool = True,
        fehler: bool = False,
        ort: Literal["remote", "lokal"] = "remote",
        fuer_mensch: bool = False,
        *,
        fuer_david: bool | None = None,  # Übergangs-Alias (#325)
    ) -> None:
        object.__setattr__(self, "remote", remote)
        object.__setattr__(self, "zweig", zweig)
        object.__setattr__(self, "sha", sha)
        object.__setattr__(self, "angelegt", angelegt)
        object.__setattr__(self, "art", art)
        object.__setattr__(self, "grund", grund)
        object.__setattr__(self, "trocken", trocken)
        object.__setattr__(self, "fehler", fehler)
        object.__setattr__(self, "ort", ort)
        object.__setattr__(self, "fuer_mensch", bool(fuer_mensch or fuer_david))  # Übergangs-Alias (#325)

    @property
    def fuer_david(self) -> bool:  # Übergangs-Alias (#325)
        """Übergangs-Alias für :attr:`fuer_mensch` (#325)."""
        return self.fuer_mensch


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
            "fuer_mensch": erg.fuer_mensch,
            "fuer_david": erg.fuer_mensch,  # Übergangs-Alias (#325)
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
    ziel = zweig_zu_ticket(zweig, slug)  # #357: Repo + Nummer nur von dort
    daten = _ticket_daten(nr, ziel)
    if daten is None and ziel is None:  # #234: Grund statt „nicht abfragbar“
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
        return ergebnis("bleibt", "keine Ticket-Nummer im Zweig — der Mensch entscheidet", fuer_mensch=True)
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
        leck = _geheimnis_pfade(o.cwd, sha, "--not", o.haupt_ref, f"--glob={o.sicherungen}")
        if leck:
            return ergebnis("bleibt", f"Geheimnis-Datei {leck[0]} in ungesichertem Commit", sha, fuer_mensch=True)
        try:  # #169: dieselbe Sperre wie der Zweig-Upload, Übergaben nie auf sicherung/<N>
            gesperrt = uebergabe_sichern.gesperrte_pfade(o.cwd, sha, "--not", o.haupt_ref, f"--glob={o.sicherungen}")
        except OSError as fehler:
            return ergebnis("bleibt", f"Prüfung gesperrter Pfade gescheitert: {_kurz(fehler)}", sha, fehler=True)
        if gesperrt:
            log.error("Sicherung von %s verweigert: %s nie öffentlich", zweig, ", ".join(gesperrt))
            grund = f"Sicherung verweigert: {', '.join(gesperrt)} nie öffentlich"
            return ergebnis("bleibt", grund, sha, fehler=True, fuer_mensch=True)
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


def _ist_git_repo(pfad: Path) -> bool:
    """Normales Git-Repo (``.git``-Ordner) oder bare Repo (``HEAD``, ``objects``, ``refs``)."""
    return (pfad / ".git").is_dir() or (
        (pfad / "HEAD").is_file() and (pfad / "objects").is_dir() and (pfad / "refs").is_dir()
    )


def _hauptrepos() -> list[Path]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_HAUPTREPOS")
    if roh is not None:
        return [Path(p).expanduser() for p in roh.split()]
    pfade = (pfad for e in _maschinen_repos() if (pfad := config.repo_klon_pfad(e)) is not None)
    return [p for p in dict.fromkeys(pfade) if _ist_git_repo(p)]


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


@dataclass(frozen=True)
class _WorktreeEintrag:
    """Ein Eintrag aus ``git worktree list --porcelain`` — einzige Parse-Stelle (#225)."""

    pfad: str
    zweig: str | None = None
    gesperrt: bool = False
    sperr_grund: str = ""
    bare: bool = False


#: #225: Worktree-Liste je Hauptrepo innerhalb eines Rundgangs einmal lesen (``None`` = kein Cache).
_WORKTREE_CACHE: ContextVar[dict[str, tuple[_WorktreeEintrag, ...] | None] | None] = ContextVar(
    "hausmeister_worktree_cache", default=None
)
#: #241: Origin-Bäume je Hauptrepo innerhalb eines Rundgangs einmal lesen (``None`` = kein Cache).
_ORIGIN_BAEUME_CACHE: ContextVar[dict[str, tuple[str, dict[str, str], list[str]]] | None] = ContextVar(
    "hausmeister_origin_baeume_cache", default=None
)
#: #781: Ticket-Daten je ``(repo_klein, ticket)`` innerhalb eines Rundgangs einmal abfragen (``None`` = kein Cache).
_TICKET_DATEN_CACHE: ContextVar[dict[tuple[str, int], object] | None] = ContextVar(
    "hausmeister_ticket_daten_cache", default=None
)


def _worktree_eintraege(repo: Path) -> list[_WorktreeEintrag]:
    """Alle Einträge von ``git worktree list --porcelain``; der erste Eintrag ist immer der
    Haupt-Arbeitsbaum bzw. das bare-Hauptrepo."""
    eintraege: list[_WorktreeEintrag] = []
    for zeile in aufpasser.git_lauf(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            eintraege.append(_WorktreeEintrag(zeile[len("worktree ") :]))
        elif not eintraege:
            continue
        elif zeile.startswith("branch refs/heads/"):
            eintraege[-1] = replace(eintraege[-1], zweig=zeile[len("branch refs/heads/") :])
        elif zeile == "locked" or zeile.startswith("locked "):
            eintraege[-1] = replace(
                eintraege[-1],
                gesperrt=True,
                sperr_grund=zeile[len("locked") :].strip(),
            )
        elif zeile == "bare":
            eintraege[-1] = replace(eintraege[-1], bare=True)
    return eintraege


def _worktree_eintraege_gecached(repo: Path) -> tuple[_WorktreeEintrag, ...] | None:
    """Liest :func:`_worktree_eintraege` im Rundgang höchstens einmal je Hauptrepo (#225);
    ``None`` bei Lesefehler (einmal geloggt)."""
    cache = _WORKTREE_CACHE.get()
    schluessel = _pfad_schluessel(repo)
    if cache is not None and schluessel in cache:
        return cache[schluessel]
    try:
        eintraege: tuple[_WorktreeEintrag, ...] | None = tuple(_worktree_eintraege(repo))
    except (RuntimeError, OSError) as fehler:
        log.warning("Hausmeister: worktree-Liste von %s unlesbar: %s", repo, _kurz(fehler))
        eintraege = None
    if cache is not None:
        cache[schluessel] = eintraege
    return eintraege


def _ausgecheckte(repo: Path) -> dict[str, str]:
    """Zweig → Arbeitsordner aller in ``repo`` ausgecheckten Zweige (auch das Hauptrepo, immer frisch)."""
    return {e.zweig: e.pfad for e in _worktree_eintraege(repo) if e.zweig}


def _eingetragene_worktrees() -> list[Path]:
    """#196/#225: alle laut git eingetragenen Arbeitsordner der Hauptrepos, wo immer sie liegen
    (auch außerhalb der Wurzeln, z. B. ``C:\\c\\dev\\…`` nach einem Pfad-Fehler) — ohne den
    Haupt-Arbeitsbaum bzw. das bare-Hauptrepo, nur vorhandene Ordner. Ein kaputtes Repo fällt
    mit Log-Zeile weg; im Rundgang wird jedes Hauptrepo nur einmal gelesen."""
    gefunden: list[Path] = []
    for repo in _hauptrepos():
        eintraege = _worktree_eintraege_gecached(repo)
        if not eintraege:
            continue
        for eintrag in eintraege[1:]:
            pfad = Path(os.path.abspath(eintrag.pfad))
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
        fuer_mensch=b.fuer_mensch,
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

    # 2/3 Git-Ordner? Hauptrepo? Eigenes Repo? (None = gar kein Git, #1536/#163)
    ist_worktree = _git_ordner_pruefen(echt)
    if ist_worktree is None:
        return _ohne_git_regeln(ordner, echt, trocken, jetzt, stand)

    # 4 Ticket-Nummer aus Name und Zweig (None = keine, #1536)
    stand.ticket = ticket = _ticket_nummer(ordner, echt)
    # 4a #225: eingetragene Worktrees ohne Ticket-Nummer außerhalb der Wurzeln (z. B. deploy-tmp)
    # bleiben still stehen — nie löschen, nie in liste_fuer_mensch.
    if ticket is None and not _in_wurzeln(echt):
        raise _Bleibt(f"{OHNE_NUMMER} (außerhalb der Wurzeln)")
    # 4b #135/#347: Nummer zählt im Ziel-Repo aus zweig_ticket_repo bzw. origin; unklar ⇒ Fehler
    slug = _origin_slug(echt)
    ziel: tuple[str, int] | None = None
    if ticket is not None:
        ziel, unklar = _ordner_ticket_ziel(echt, ticket, slug)
        if unklar:
            raise _Bleibt("Ticket-Repo nicht bestimmbar (Maschinen-Liste)", fuer_mensch=True, fehler=True)
    fremd = ticket is not None and ziel is None
    if fremd:
        stand.ticket = None

    # 5/6 Ticket zu, seit mindestens 24 h; keine laufende Spec-Sitzung
    if ticket is not None and not fremd:
        _ticket_lange_zu(ticket, jetzt, platznot, ziel)
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
    koepfe, zu_sichern, status_vorher, sicherung_vorher = _ungesichertes(echt, ist_worktree, ticket)
    art: Art = "gesichert und gelöscht" if (koepfe or zu_sichern) else "gelöscht"
    if ticket is None:  # 8b #235: ohne Nummer keine Sicherung (sicherung/<N>) ⇒ nur Fertiges
        if zu_sichern:
            raise _Bleibt(
                f"{OHNE_NUMMER}: Änderungen oder Commits nicht auf origin (ohne Nummer nicht sicherbar)",
                fuer_mensch=True,
            )
        if koepfe:
            raise _Bleibt(
                f"{OHNE_NUMMER}: {koepfe[0]} nicht auf dem Server (ls-remote origin, ohne Nummer nicht sicherbar)",
                fuer_mensch=True,
            )
        grund = f"{OHNE_NUMMER}; alles auf origin, Baum sauber, seit 24 h unverändert, kein Pull-Request offen"
    elif fremd:  # 8b #135: ohne gültiges Ticket nur, was nachweislich fertig ist
        grund = _fremd_fertig(echt, ticket, bool(koepfe or zu_sichern), jetzt)
    else:
        grund = _grund(art, ticket, platznot)
    if trocken:
        return Ergebnis(ordner, art, grund, stand.ticket, trocken=True), None
    if ticket is not None and (koepfe or zu_sichern):
        zusatz = [*koepfe, *([sicherung_vorher] if sicherung_vorher else [])]
        try:
            stand.sicherung = aufpasser.sichern(echt, ticket, jetzt, zusatz_koepfe=zusatz)
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
    (None, #1536/#241: kein ``.git`` oder verwaister Worktree ohne Eintrag im Hauptrepo)."""
    if _git_oder_none(echt, "rev-parse", "--is-inside-git-dir") == "true":
        raise _Bleibt("Git-Verwaltungsordner (Hauptrepo)")
    oben = _git_oder_none(echt, "rev-parse", "--show-toplevel")
    if oben is None:
        if _ohne_git(echt):
            return None
        raise _Bleibt("Git-Ordner nicht lesbar (git rev-parse scheitert)", fehler=True)
    if Path(oben).resolve() != echt:  # Unterordner eines anderen Repos: nie löschen
        raise _Bleibt(f"kein eigener Git-Ordner (gehört zu {oben})", fuer_mensch=True)
    ist_worktree = (echt / ".git").is_file()
    if not ist_worktree:
        if echt.name in _hauptrepo_namen():
            raise _Bleibt("Hauptrepo")
        if _hat_verknuepfte_worktrees(echt):
            raise _Bleibt("Hauptrepo mit verknüpften Worktrees")
    url = _git_oder_none(echt, "config", "--get", "remote.origin.url") or ""
    if _origin_slug(echt).lower() not in _eigene_repos():
        raise _Bleibt(f"fremdes Repo ({url or 'ohne origin'})", fuer_mensch=True)
    if ist_worktree:
        return _worktree_eintrag_pruefen(echt)
    return False


def _worktree_eintrag_pruefen(echt: Path) -> bool | None:
    """#225/#241: prüft einen Ordner mit ``.git``-Datei in der Worktree-Liste seines Hauptrepos.

    - Gesperrt (``locked``) ⇒ bleibt als Regel (``fehler=False``).
    - Nicht im Hauptrepo eingetragen (kopiert/verschoben vor ``worktree prune``) ⇒ ``None``
      (Worktree-Waise, weiter in :func:`_ohne_git_regeln`).
    - Haupt-Arbeitsbaum (``--separate-git-dir``) ⇒ ``False`` (wie Klon, Hauptrepo-Schutz greift).
    """
    try:
        haupt = _hauptrepo(echt)
    except (RuntimeError, OSError) as fehler:
        raise _Bleibt(f"Hauptrepo des Worktrees nicht lesbar: {_kurz(fehler)}", fehler=True) from None
    eintraege = _worktree_eintraege_gecached(haupt)
    if eintraege is None:
        raise _Bleibt("worktree-Liste des Hauptrepos nicht lesbar", fehler=True)
    schluessel = _pfad_schluessel(echt)
    for idx, eintrag in enumerate(eintraege):
        if _pfad_schluessel(Path(os.path.abspath(eintrag.pfad))) != schluessel:
            continue
        if idx == 0:
            if echt.name in _hauptrepo_namen():
                raise _Bleibt("Hauptrepo")
            if any(Path(os.path.abspath(e.pfad)).is_dir() for e in eintraege[1:]):
                raise _Bleibt("Hauptrepo mit verknüpften Worktrees")
            return False
        if eintrag.gesperrt:
            zusatz = f" — {eintrag.sperr_grund}" if eintrag.sperr_grund else ""
            raise _Bleibt(f"gesperrt (locked){zusatz}")
        return True
    return None


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


def _glob_zu_regex(muster: str) -> re.Pattern[str]:
    """Übersetzt ein Git-Glob-Muster (``**``, ``*``, ``?``) in einen POSIX-Pfad-Regex."""
    teile: list[str] = []
    i = 0
    n = len(muster)
    while i < n:
        if muster.startswith("**/", i):
            teile.append("(?:.*/)?")
            i += 3
        elif muster.startswith("/**", i) and i + 3 == n:
            teile.append("/.*")
            i += 3
        elif muster.startswith("**", i):
            teile.append(".*")
            i += 2
        elif muster[i] == "*":
            teile.append("[^/]*")
            i += 1
        elif muster[i] == "?":
            teile.append("[^/]")
            i += 1
        else:
            teile.append(re.escape(muster[i]))
            i += 1
    return re.compile("^" + "".join(teile) + "$", re.IGNORECASE)


_OHNE_GIT_GEHEIMNIS_MUSTER = tuple(_glob_zu_regex(m) for m in OHNE_GIT_GEHEIMNISSE)
_OHNE_GIT_DB_MUSTER = tuple(_glob_zu_regex(m) for m in aufpasser.NIE_HOCHLADEN)
_OHNE_GIT_ERZEUGNIS_MUSTER = tuple(_glob_zu_regex(m) for m in ERZEUGNISSE)


def _git_blob_sha(pfad: Path, groesse: int) -> bytes:
    """Git-Blob-SHA-1 einer regulären Datei (gestreamt); bricht bei Größenänderung ab."""
    h = hashlib.sha1(f"blob {groesse}\0".encode())
    gelesen = 0
    with pfad.open("rb") as f:
        while block := f.read(65536):
            gelesen += len(block)
            h.update(block)
    if gelesen != groesse:
        raise OSError(f"Dateigröße während des Lesens geändert ({pfad.name})")
    return h.digest()


def _ist_wegwerf_begleiter(echt: Path, rel: str) -> bool:
    """Bekannte Erzeugnisse (:data:`ERZEUGNISSE`) oder kleine Test-DBs (:func:`ist_test_db`),
    die neben einem origin-Stand liegen dürfen (#241)."""
    return any(m.match(rel) for m in _OHNE_GIT_ERZEUGNIS_MUSTER) or ist_test_db(echt, rel)


def _git_baum_sha(echt: Path, *, ohne_erzeugnisse: bool) -> str | None:
    """#241: berechnet den exakten Git-Tree-SHA-1 eines Ordners ohne Git (fail-closed bei OSError).

    Eine ``.git``-Datei direkt in ``echt`` (Worktree-Waise) wird übersprungen. Mit
    ``ohne_erzeugnisse=True`` fallen bekannte Erzeugnisse und kleine Test-DBs heraus.
    Leerer Ordner oder unbekannte Dateitypen ⇒ ``None``.
    """

    def baum(d: Path) -> bytes | None:
        eintraege: list[tuple[str, bytes]] = []
        with os.scandir(d) as it:
            items = list(it)
        for e in items:
            if d == echt and e.name == ".git" and e.is_file(follow_symlinks=False):
                continue
            info = e.stat(follow_symlinks=False)
            art = info.st_mode
            rel = Path(e.path).relative_to(echt).as_posix()
            if stat.S_ISDIR(art):
                sub = baum(Path(e.path))
                if sub is not None:
                    eintraege.append((e.name + "/", b"40000 " + e.name.encode("utf-8") + b"\0" + sub))
            elif stat.S_ISLNK(art):
                if ohne_erzeugnisse and _ist_wegwerf_begleiter(echt, rel):
                    continue
                ziel = os.readlink(e.path).encode("utf-8")
                blob = hashlib.sha1(f"blob {len(ziel)}\0".encode() + ziel).digest()
                eintraege.append((e.name, b"120000 " + e.name.encode("utf-8") + b"\0" + blob))
            elif stat.S_ISREG(art):
                if ohne_erzeugnisse and _ist_wegwerf_begleiter(echt, rel):
                    continue
                blob = _git_blob_sha(Path(e.path), info.st_size)
                modus = b"100755" if (art & 0o111) else b"100644"
                eintraege.append((e.name, modus + b" " + e.name.encode("utf-8") + b"\0" + blob))
            else:
                return None
        if not eintraege:
            return None
        eintraege.sort(key=lambda x: x[0].encode("utf-8"))
        inhalt = b"".join(raw for _, raw in eintraege)
        return hashlib.sha1(f"tree {len(inhalt)}\0".encode() + inhalt).digest()

    try:
        roh = baum(echt)
    except OSError as fehler:
        raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
    return roh.hex() if roh is not None else None


def _datei_blobs(echt: Path, *, ohne_erzeugnisse: bool) -> dict[str, str] | None:
    """#241 (Windows-Rückfall ohne POSIX-Ausführbit): ``{rel_posix: blob_sha_hex}`` aller Dateien."""
    blobs: dict[str, str] = {}
    stapel = [echt]
    try:
        while stapel:
            d = stapel.pop()
            with os.scandir(d) as it:
                items = list(it)
            for e in items:
                if d == echt and e.name == ".git" and e.is_file(follow_symlinks=False):
                    continue
                info = e.stat(follow_symlinks=False)
                art = info.st_mode
                rel = Path(e.path).relative_to(echt).as_posix()
                if stat.S_ISDIR(art):
                    stapel.append(Path(e.path))
                elif stat.S_ISREG(art):
                    if ohne_erzeugnisse and _ist_wegwerf_begleiter(echt, rel):
                        continue
                    blobs[rel] = _git_blob_sha(Path(e.path), info.st_size).hex()
                elif stat.S_ISLNK(art):
                    if ohne_erzeugnisse and _ist_wegwerf_begleiter(echt, rel):
                        continue
                    ziel = os.readlink(e.path).encode("utf-8")
                    blobs[rel] = hashlib.sha1(f"blob {len(ziel)}\0".encode() + ziel).hexdigest()
                else:
                    return None
    except OSError as fehler:
        raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
    return blobs or None


def _origin_baeume(repo: Path) -> tuple[str, dict[str, str], list[str]]:
    """#241: ``(slug, {tree_sha: commit_sha}, server_shas)`` aller von ``ls-remote --heads origin``
    erreichbaren Commits eines eigenen Hauptrepos; im Rundgang einmal je Hauptrepo gelesen."""
    cache = _ORIGIN_BAEUME_CACHE.get()
    schluessel = _pfad_schluessel(repo)
    if cache is not None and schluessel in cache:
        return cache[schluessel]
    slug = _origin_slug(repo)
    if slug.lower() not in _eigene_repos():
        ergebnis: tuple[str, dict[str, str], list[str]] = ("", {}, [])
        if cache is not None:
            cache[schluessel] = ergebnis
        return ergebnis
    server_shas = list(dict.fromkeys(_server_koepfe(repo)))
    if server_shas and any(
        _git_oder_none(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}") is None for sha in server_shas
    ):
        try:
            _git_ohne_frage(repo, "fetch", "-q", "--prune", "origin")
        except RuntimeError as fehler:
            raise _Bleibt(f"git fetch origin gescheitert: {_kurz(fehler)}", fehler=True) from None
    baeume: dict[str, str] = {}
    for i in range(0, len(server_shas), _REFLOG_HAPPEN):
        happen = server_shas[i : i + _REFLOG_HAPPEN]
        try:
            roh = aufpasser.git_lauf(repo, "rev-list", "--ignore-missing", "--format=%T %H", *happen)
        except RuntimeError as fehler:
            raise _Bleibt(f"origin-Commits nicht lesbar: {_kurz(fehler)}", fehler=True) from None
        for zeile in roh.splitlines():
            if not zeile or zeile.startswith("commit "):
                continue
            baum_sha, sep, commit_sha = zeile.partition(" ")
            if sep and baum_sha not in baeume:
                baeume[baum_sha] = commit_sha
    ergebnis = (slug, baeume, server_shas)
    if cache is not None:
        cache[schluessel] = ergebnis
    return ergebnis


def _ls_tree_blobs(repo: Path, commit: str) -> dict[str, str]:
    """``{rel_posix: blob_sha}`` eines Commits über ``git ls-tree -r -z``."""
    roh = aufpasser.git_lauf(repo, "ls-tree", "-r", "-z", commit)
    blobs: dict[str, str] = {}
    for eintrag in roh.split("\0"):
        if not eintrag:
            continue
        meta, sep, pfad = eintrag.partition("\t")
        teile = meta.split()
        if sep and len(teile) >= 3 and teile[1] == "blob":
            blobs[pfad] = teile[2]
    return blobs


def _inhalt_auf_origin(echt: Path) -> tuple[str, str, str | None] | None:
    """#241: prüft, ob der Inhalt von ``echt`` bytegleich einem Commit auf ``origin`` eines
    bekannten eigenen Hauptrepos ist. Rückgabe ``(slug, commit_sha, baum_sha_oder_None)``
    oder ``None``; wirft ``_Bleibt(..., fehler=True)`` fail-closed bei Server-Lesefehler."""
    sha_roh = _git_baum_sha(echt, ohne_erzeugnisse=False)
    sha_ohne = _git_baum_sha(echt, ohne_erzeugnisse=True)
    gesucht = tuple(dict.fromkeys(s for s in (sha_roh, sha_ohne) if s))
    if not gesucht:
        return None
    blobs_kandidaten: list[dict[str, str]] | None = None
    fehler_bleibt: _Bleibt | None = None
    for repo in _hauptrepos():
        try:
            slug, baeume, server_shas = _origin_baeume(repo)
        except _Bleibt as b:
            if b.fehler and fehler_bleibt is None:
                fehler_bleibt = b
            continue
        if not slug:
            continue
        for baum_sha in gesucht:
            if commit := baeume.get(baum_sha):
                return slug, commit, baum_sha
        if _ist_windows() and server_shas:
            if blobs_kandidaten is None:
                b1 = _datei_blobs(echt, ohne_erzeugnisse=False)
                b2 = _datei_blobs(echt, ohne_erzeugnisse=True)
                blobs_kandidaten = [b for b in (b1, b2) if b is not None]
            for sha in server_shas:
                try:
                    ziel_blobs = _ls_tree_blobs(repo, sha)
                except RuntimeError:
                    continue
                if any(b == ziel_blobs for b in blobs_kandidaten):
                    return slug, sha, None
    if fehler_bleibt is not None:
        raise fehler_bleibt
    return None


def _ist_ticket_nicht_vorhanden(fehlertext: str) -> bool:
    """True nur, wenn ``gh issue view`` eindeutig meldet, dass die Nummer im Repo fehlt."""
    klein = fehlertext.lower()
    return any(text in klein for text in ("could not resolve to an issue", "issue not found", "http 404"))


def _kein_offenes_ticket_ohne_git(nr: int) -> None:
    """#163: prüft ``nr`` in allen Ticket-Repos der Maschine; offen oder Lesefehler ⇒ ``_Bleibt``."""
    repos = _alle_ticket_repos()
    if not repos:
        raise _Bleibt(
            f"Ticket #{nr} nicht abfragbar (kein Ticket-Repo in der Maschinen-Liste)",
            fehler=True,
        )
    for repo in repos:
        code, ausgabe = gh.lauf(
            ["issue", "view", str(nr), "-R", repo, "--json", "state,closedAt"],
            timeout=GH_ZEITLIMIT_S,
            fehlertext=True,
        )
        if code != 0:
            if _ist_ticket_nicht_vorhanden(ausgabe):
                continue
            raise _Bleibt(f"Ticket #{nr} nicht abfragbar ({repo})", fehler=True)
        try:
            daten = json.loads(ausgabe)
        except ValueError:
            raise _Bleibt(f"Ticket #{nr} nicht abfragbar ({repo})", fehler=True) from None
        if not isinstance(daten, dict) or "state" not in daten:
            raise _Bleibt(f"Ticket #{nr} nicht abfragbar ({repo})", fehler=True)
        if not ticket_zustand.endgueltig_zu(daten):
            raise _Bleibt(f"Ticket #{nr} offen ({repo})")


def _ohne_git_baum_pruefen(echt: Path, jetzt: float) -> None:
    """#163: prüft einen Ordner ohne eigenes Git komplett (fail-closed bei Lesefehler).

    Symlinks werden nie verfolgt. Hält den Ordner bei jungen Dateien/Ordnern
    (< :data:`OHNE_GIT_ALTER_SEKUNDEN`), Sockets/FIFOs, eingebetteten Git-Repos,
    Geheimnis-Verdacht (:data:`OHNE_GIT_GEHEIMNISSE` ohne :func:`_neu_erzeugbar`),
    unbekannten/zu großen Datenbanken sowie jedem ``OSError``.
    """
    try:
        wurzel_stat = os.lstat(echt)
    except OSError as fehler:
        raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
    if jetzt - wurzel_stat.st_mtime < OHNE_GIT_ALTER_SEKUNDEN:
        raise _Bleibt("frisch geändert (ohne Git)")
    stapel = [echt]
    while stapel:
        d = stapel.pop()
        try:
            with os.scandir(d) as it:
                eintraege = list(it)
        except OSError as fehler:
            raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
        if d != echt:
            namen = {e.name for e in eintraege}
            if ".git" in namen or {"HEAD", "objects", "refs"} <= namen:
                rel_d = d.relative_to(echt).as_posix()
                raise _Bleibt(f"eingebettetes Repo {rel_d}", fuer_mensch=True)
        for e in eintraege:
            try:
                info = e.stat(follow_symlinks=False)
            except OSError as fehler:
                raise _Bleibt(f"Eintrag nicht lesbar ({e.name}): {_kurz(fehler)}", fehler=True) from None
            rel = Path(e.path).relative_to(echt).as_posix()
            art = info.st_mode
            if stat.S_ISSOCK(art) or stat.S_ISFIFO(art):
                raise _Bleibt(f"Socket/FIFO im Ordner ({rel}) — ein Prozess lebt evtl.")
            if jetzt - info.st_mtime < OHNE_GIT_ALTER_SEKUNDEN:
                raise _Bleibt(f"frisch geändert ({rel})")
            if any(m.match(rel) for m in _OHNE_GIT_GEHEIMNIS_MUSTER) and not _neu_erzeugbar(rel):
                raise _Bleibt(f"Geheimnis-Verdacht {rel} (ohne Git nicht sicherbar)", fuer_mensch=True)
            if any(m.match(rel) for m in _OHNE_GIT_DB_MUSTER) and not ist_test_db(echt, rel):
                raise _Bleibt(f"Datenbank {rel} ist keine Test-Kopie (nicht sicherbar)", fuer_mensch=True)
            if stat.S_ISDIR(art):
                stapel.append(Path(e.path))


def _ohne_git_regeln(
    ordner: Path, echt: Path, trocken: bool, jetzt: float | None, stand: _Stand
) -> tuple[Ergebnis, tuple[Path, bool] | None]:
    """#163/#241: räumt einen verwaisten Ordner ohne eigenes Git nach allen Schutz-Regeln."""
    nr = _nummer(ordner.name, _name_muster())
    jetzt_ts = jetzt if jetzt is not None else time.time()
    # Lokale Prüfungen immer zuerst (fail-closed bei Lesefehler; Prozesse, Alter, Geheimnisse,
    # fremde DBs und eingebettete Repos halten den Ordner vor jeder Netzabfrage).
    _kein_prozess(echt)
    _ohne_git_baum_pruefen(echt, jetzt_ts)
    if nr is None:
        treffer = _inhalt_auf_origin(echt)
        if treffer is None:
            raise _Bleibt(
                "kein eigener Git-Ordner und keine Ticket-Nummer (nicht sicherbar)",
                fuer_mensch=True,
            )
        slug, commit, baum_sha = treffer
        grund = (
            f"verwaister Ordner ohne Git (Inhalt gleich Commit {commit[:7]} auf origin von {slug}, "
            "alt, kein Prozess, kein Geheimnis-Verdacht)"
        )
        if trocken:
            return Ergebnis(ordner, "gelöscht", grund, None, trocken=True), None
        _kein_prozess(echt, probe=True)
        _ohne_git_baum_pruefen(echt, jetzt_ts)
        if baum_sha is not None:
            aktuell = {
                _git_baum_sha(echt, ohne_erzeugnisse=False),
                _git_baum_sha(echt, ohne_erzeugnisse=True),
            }
            if baum_sha not in aktuell:
                raise _Bleibt("Ordner hat sich während der Prüfung geändert")
        elif _inhalt_auf_origin(echt) is None:
            raise _Bleibt("Ordner hat sich während der Prüfung geändert")
        return Ergebnis(ordner, "gelöscht", grund, None, trocken=False), (echt, False)
    stand.ticket = nr
    _keine_spec_sitzung(nr)
    _kein_offenes_ticket_ohne_git(nr)
    grund = f"verwaister Ordner ohne Git (#{nr}: kein offenes Ticket, alt, kein Prozess, kein Geheimnis-Verdacht)"
    if trocken:
        return Ergebnis(ordner, "gelöscht", grund, nr, trocken=True), None
    _kein_prozess(echt, probe=True)
    _ohne_git_baum_pruefen(echt, jetzt_ts)
    return Ergebnis(ordner, "gelöscht", grund, nr, trocken=False), (echt, False)


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
            **prozessbaum.fensterlos(),
        )
    except FileNotFoundError:
        return
    except (OSError, subprocess.TimeoutExpired) as fehler:
        raise _Bleibt(f"tmux nicht abfragbar: {_kurz(fehler)}", fehler=True) from None
    if lauf.returncode != 0:
        if "no server running" in lauf.stderr or "error connecting" in lauf.stderr:
            return
        raise _Bleibt(f"tmux nicht abfragbar: {lauf.stderr.strip()[:200]}", fehler=True)
    for name in lauf.stdout.split():
        if tmux_aufruf.spec_aus_sitzung(name) == str(ticket):
            raise _Bleibt(f"tmux-Sitzung {name} läuft")


def _ticket_nummer(ordner: Path, echt: Path) -> int | None:
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    aus_name = _nummer(ordner.name, _name_muster())
    aus_zweig = _nummer(zweig, _ZWEIG_FORMEN)
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        raise _Bleibt(
            f"Ordnername (#{aus_name}) und Zweig {zweig} (#{aus_zweig}) widersprechen sich",
            fuer_mensch=True,
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


def zweig_zu_ticket(zweig: str, slug: str) -> tuple[str, int] | None:
    """Zweigname + ``origin`` (``slug``) → (Ticket-Repo, Nummer); ``None`` = kein abfragbares Ticket.

    Einzige Stelle für diese Zuordnung (#357, #347): versteckt die Zweig-Formen samt Datums-Sperre
    (``_ZWEIG_FORMEN``), optionale Zweig-Regeln aus der Maschinen-Liste (``zweig_ticket_repo``,
    längster passender Klartext-Präfix gewinnt; leer = kein Ticket-Repo; ungültig/widersprüchlich =
    fail-closed ``None``) und sonst die Regel „origin entscheidet“ (:func:`_nummer_zaehlt`). Wer ein
    Ticket zu einem Zweig abfragt, reicht das Ergebnis unverändert an :func:`_ticket_daten` weiter —
    so kann kein Aufrufer Nummer und Repo falsch verbinden.
    """
    nr = _nummer(zweig, _ZWEIG_FORMEN)
    if nr is None:
        return None
    regeln, ok = _zweig_ticket_repo_regeln(slug)
    if not ok or regeln is None:
        return None
    treffer = _zweig_ticket_repo_treffer(zweig, regeln)
    if treffer is not None:
        return (treffer, nr) if treffer else None
    return _ticket_ziel(nr, slug)


def _ordner_ticket_ziel(echt: Path, ticket: int, slug: str) -> tuple[tuple[str, int] | None, bool]:
    """Bestimmt ``(ziel, unklar)`` für einen Ordner mit Nummer ``ticket`` und ``origin`` ``slug`` (#347).

    Trägt der ausgecheckte Zweig dieselbe Ticket-Nummer, gelten ``zweig_ticket_repo``-Regeln
    über :func:`_zweig_ticket_repo_treffer`; sonst :func:`_ticket_ziel`. ``unklar=True`` heißt:
    eigenes Repo ohne bestimmbares ``ticket_repo`` oder ungültiges/widersprüchliches
    ``zweig_ticket_repo`` ⇒ Ordner bleibt mit Fehler (nie über :func:`_fremd_fertig`).
    """
    regeln, ok = _zweig_ticket_repo_regeln(slug)
    if not ok or regeln is None:
        return None, True
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    if _nummer(zweig, _ZWEIG_FORMEN) == ticket:
        treffer = _zweig_ticket_repo_treffer(zweig, regeln)
        if treffer is not None:
            return ((treffer, ticket) if treffer else None), False
    if slug.lower() in _eigene_repos() and _ticket_repo_fuer(slug) is None:
        return None, True
    return _ticket_ziel(ticket, slug), False


def _ticket_ziel(nr: int, slug: str) -> tuple[str, int] | None:
    """Nummer ``nr`` (aus Zweig oder Ordnername) + ``origin`` → (Ticket-Repo, Nummer) oder ``None``.

    Der Ordner-Pfad nutzt das über :func:`_ordner_ticket_ziel`; für Zweige gilt :func:`zweig_zu_ticket`."""
    repo = _ticket_repo_fuer(slug) if _nummer_zaehlt(slug) else None
    return None if repo is None else (repo, nr)


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
        raise _Bleibt(f"{vorne}: Änderungen oder Commits nicht im Hauptzweig gesichert", fuer_mensch=True)
    haupt = _hauptzweig(echt, str(echt))
    haupt_sha = _server_zweig_sha(echt, haupt)
    offen = _git_oder_none(echt, "rev-list", "--ignore-missing", "-n", "1", "HEAD", "--not", haupt_sha)
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
        raise _Bleibt(OHNE_NUMMER, fuer_mensch=True)


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


def _ticket_lange_zu(
    ticket: int, jetzt: float | None, platznot: bool = False, ziel: tuple[str, int] | None | EllipsisType = ...
) -> None:
    """``ziel`` wie bei :func:`_ticket_daten` (Ergebnis von :func:`_ticket_ziel`)."""
    daten = _ticket_daten(ticket, ziel)
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
        ziel, unklar = _ordner_ticket_ziel(echt, ticket, _origin_slug(echt))
        if unklar:
            return None
        daten = _ticket_daten(ticket, ziel)
    except (
        _Bleibt,
        OSError,
        RuntimeError,
    ):  # nur Reihenfolge — die Regeln entscheiden danach selbst
        return None
    if not isinstance(daten, dict) or not ticket_zustand.endgueltig_zu(daten):
        return None
    return _zeitpunkt(daten.get("closedAt"))


def _ticket_daten(ticket: int, ziel: tuple[str, int] | None | EllipsisType = ...) -> object:
    """``gh issue view`` (state, closedAt) mit Zeitlimit; ``None`` = nicht abfragbar.

    ``ziel`` = Ergebnis von :func:`zweig_zu_ticket` bzw. :func:`_ticket_ziel`, unverändert: gefragt
    wird genau dieses Repo mit dieser Nummer, ``None`` = nicht abfragbar (#234, #357). Ohne
    ``ziel`` (kein GitHub-origin) das eine Ticket-Repo der Maschine für ``ticket``. Innerhalb von
    :func:`_alle_ordner` liefert :data:`_TICKET_DATEN_CACHE` je ``(repo, nr)`` höchstens einen
    ``gh``-Aufruf (#781).
    """
    if ziel is None:
        return None
    if not isinstance(ziel, EllipsisType):
        repo, nr = ziel
        return _gh_ticket_gecached(nr, repo)
    repo = _ticket_repo()
    if repo is None:
        log.warning("Hausmeister: kein eindeutiges Ticket-Repo in der Maschinen-Liste — #%s bleibt", ticket)
        return None
    return _gh_ticket_gecached(ticket, repo)


def _gh_ticket_gecached(ticket: int, repo: str | None) -> object:
    if repo is None:
        return None
    cache = _TICKET_DATEN_CACHE.get()
    schluessel = (repo.lower(), ticket)
    if cache is not None and schluessel in cache:
        return cache[schluessel]
    daten = _gh_ticket(ticket, repo)
    if cache is not None:
        cache[schluessel] = daten
    return daten


def _gh_ticket(ticket: int, repo: str | None) -> object:
    if repo is None:
        return None
    return gh.json_lauf(
        ["issue", "view", str(ticket), "-R", repo, "--json", "state,closedAt"],
        timeout=GH_ZEITLIMIT_S,
    )


def _ist_platznot(anteil: float | None) -> bool:
    return anteil is not None and anteil < PLATZNOT_ANTEIL


def _ist_inode_knapp(belegt: float | None) -> bool:
    return belegt is not None and belegt >= INODE_WARN_ANTEIL


def _ist_inode_voll(belegt: float | None) -> bool:
    return belegt is not None and belegt >= INODE_SPERRE_ANTEIL


def inode_anteil(pfad: Path) -> float | None:
    """Belegter Inode-Anteil (0..1) des Dateisystems unter ``pfad`` (#355); ``None`` = unbekannt.

    Einzige Mess-Stelle (auch für ``tests/conftest.py``): ohne ``os.statvfs`` (Windows), bei
    ``OSError`` oder ohne feste Inode-Zahl (``f_files <= 0``, z. B. btrfs) kommt ``None``.
    """
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        return None
    try:
        werte = statvfs(pfad)
    except OSError:
        return None
    if werte.f_files <= 0:
        return None
    return 1 - werte.f_ffree / werte.f_files


def _temp_inode_basen() -> list[Path]:
    """Existierende Temp-Wurzeln (System-Temp + konfigurierter Temp-Ordner), ohne Doppelte."""
    basen: dict[str, Path] = {}
    kandidaten = [Path(tempfile.gettempdir())]
    try:
        kandidaten.append(temp_ordner._pfad_ohne_anlegen())
    except (OSError, config.KeinHeimOrdner):
        pass
    for pfad in kandidaten:
        if pfad.is_dir():
            basen.setdefault(_pfad_schluessel(pfad), pfad)
    return list(basen.values())


def _temp_inode_anteil() -> float | None:
    """Höchster belegter Inode-Anteil (0..1) der Temp-Wurzeln (#355); ``None`` = unbekannt.

    Test-Naht ``TO_SPAWN_HAUSMEISTER_INODES``: Anteil (0..1) oder Datei mit einem Anteil je Zeile.
    """
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_INODES", "").strip()
    if roh:
        anteil = _naht_anteil(roh)
        if anteil is None:
            log.warning("Temp-Inodes nicht messbar: TO_SPAWN_HAUSMEISTER_INODES=%r ungültig", roh)
        return anteil
    werte = [w for basis in _temp_inode_basen() if (w := inode_anteil(basis)) is not None]
    return max(werte) if werte else None


def _pt_marker_retten(ordner: Path, pid: int, mtime: float | None = None) -> None:
    """Steht ein ``pt-*``-Ordner nach ``rmtree`` noch ohne Marker, Marker neu schreiben (#355)."""
    marker = ordner / PT_MARKER
    if not ordner.is_dir() or marker.exists():
        return
    try:
        marker.write_text(f"{pid}\n", encoding="utf-8")
        if mtime is not None:
            os.utime(marker, (mtime, mtime))
    except OSError as fehler:
        log.warning("Hausmeister: pt-Marker in %s nicht wiederherstellbar: %s", ordner, fehler)


def _temp_pt_basen() -> list[Path]:
    """Temp-Wurzeln für das Hausmeister-Räumen toter ``pt-*``-Reste (#355).

    Bei gesetzter Test-Tür ``TO_SPAWN_TEMP_ORDNER`` nur dieser Ordner (nie fremde Läufe im
    System-Temp während Tests); sonst alle existierenden Temp-Wurzeln (:func:`_temp_inode_basen`).
    """
    if os.environ.get("TO_SPAWN_TEMP_ORDNER", "").strip():
        try:
            ziel = temp_ordner._pfad_ohne_anlegen()
            return [ziel] if ziel.is_dir() else []
        except (OSError, config.KeinHeimOrdner):
            return []
    return _temp_inode_basen()


def pt_reste_raeumen(
    temp: Path,
    *,
    loeschen: bool = True,
    jetzt: float | None = None,
    inode_fn: Callable[[Path], float | None] | None = None,
    mit_log: bool = False,
) -> list[Path]:
    """Räumt tote, markierte ``pt-*``-Ordner (Marker :data:`PT_MARKER`) unter ``temp`` (#355, Linux).

    Ab :data:`INODE_WARN_ANTEIL` greift die kurze Schonfrist :data:`PT_RESTE_ALTER_KNAPP_S` (10 min),
    sonst :data:`PT_RESTE_ALTER_S` (1 h). Unmarkierte Ordner, Symlinks, ungültige PIDs und laufende
    Prozesse bleiben immer unberührt. Windows-Zweig unberührt (dort räumt ``tests/conftest.py``).
    """
    if _ist_windows():
        return []
    stichtag = time.time() if jetzt is None else jetzt
    anteil = (inode_fn or inode_anteil)(temp)
    knapp = _ist_inode_knapp(anteil)
    grenze = stichtag - (PT_RESTE_ALTER_KNAPP_S if knapp else PT_RESTE_ALTER_S)
    try:
        kandidaten = sorted(p for p in temp.iterdir() if p.name.startswith("pt-"))
    except OSError as fehler:
        log.warning("Hausmeister: pt-Reste unter %s nicht lesbar: %s", temp, fehler)
        return []
    geraeumt: list[Path] = []
    for ordner in kandidaten:
        marker = ordner / PT_MARKER
        try:
            if ordner.is_symlink():
                continue
            marker_mtime = marker.stat().st_mtime
            if marker_mtime > grenze:
                continue
            pid = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if not 0 < pid < 2**31 or prozessbaum.lebt(pid):
            continue
        if not loeschen:
            geraeumt.append(ordner)
            continue
        grund = (
            f"toter pt-Rest (PID {pid}, Inodes knapp)"
            if knapp
            else f"toter pt-Rest (PID {pid}, abgelaufene Schonfrist)"
        )
        if mit_log and not _log_schreiben(Ergebnis(ordner, "gelöscht", grund, None, trocken=False)):
            continue
        try:
            shutil.rmtree(ordner)
        except OSError as fehler:
            log.warning("Hausmeister: pt-Rest %s nicht löschbar: %s", ordner, fehler)
            if mit_log:
                _log_schreiben(
                    Ergebnis(ordner, "bleibt", f"Löschen gescheitert: {fehler}", None, trocken=False, fehler=True)
                )
        finally:
            _pt_marker_retten(ordner, pid, marker_mtime)
        if not ordner.exists():
            geraeumt.append(ordner)
    return geraeumt


def _pt_reste_im_takt(*, loeschen: bool, jetzt: float | None) -> None:
    """#355 (Linux): räumt im Hausmeister-Takt tote markierte ``pt-*``-Reste; wirft nie."""
    if _ist_windows():
        return
    hat_naht = bool(os.environ.get("TO_SPAWN_HAUSMEISTER_INODES", "").strip())
    messung: Callable[[Path], float | None] = (lambda _p: _temp_inode_anteil()) if hat_naht else inode_anteil
    for basis in _temp_pt_basen():
        try:
            pt_reste_raeumen(basis, loeschen=loeschen, jetzt=jetzt, inode_fn=messung, mit_log=True)
        except Exception:  # Vertrag: wirft nie
            log.exception("Hausmeister: pt-Reste unter %s gescheitert", basis)


def _temp_inodes_im_takt() -> None:
    """#355: warnt im regulären Takt, sobald der Inode-Zähler einer Temp-Wurzel knapp wird."""
    if _IM_STARTCHECK.get():
        return
    inodes = _temp_inode_anteil()
    if not _ist_inode_knapp(inodes):
        return
    assert inodes is not None
    ist_voll = _ist_inode_voll(inodes)
    schwelle = INODE_SPERRE_ANTEIL if ist_voll else INODE_WARN_ANTEIL
    satz = f"Temp-Wurzel: {_prozent(inodes)} der Inodes belegt (Schwelle {_prozent(schwelle)})"
    log.warning("Hausmeister: %s", satz)
    if ist_voll:
        _spur(ART_TEMP_INODES_VOLL, satz, inodes_belegt=round(inodes, 4))
    else:
        _spur(ART_TEMP_INODES_KNAPP, satz, inodes_belegt=round(inodes, 4))


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
    return list(basen) or [config.heim_ordner()]


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
        echt, heim = pfad.resolve(), config.heim_ordner().resolve()
    except (OSError, RuntimeError):
        return False
    return pfad.is_absolute() and heim in echt.parents


def _versteckt_unter_home(pfad: Path) -> bool:
    """Ein Teil unterhalb des Home beginnt mit ``.`` (``~/.ssh``, ``~/a/.cache``)."""
    heim = config.heim_ordner().parts
    gleich = [os.path.normcase(t) for t in pfad.parts[: len(heim)]] == [os.path.normcase(t) for t in heim]
    rest = pfad.parts[len(heim) :] if gleich else pfad.parts
    return any(teil.startswith(".") for teil in rest)


def _wurzel_erlaubt(muster: str) -> bool:
    """Wurzel-Muster der Liste: Vorgabe (z. B. ``C:/dev/wt-*``) oder fester Teil vor dem
    ersten Glob-Zeichen echt unter dem Home (``~/*``, ``/tmp/*`` fallen weg) und ohne
    versteckten Ordner im ganzen Muster, auch nach dem ersten Glob-Zeichen (``~/.ssh/*``,
    ``~/x/wt-*/.ssh``: Schlüssel/Einstellungen, keine Worktrees; ``..`` fällt mit weg).

    Hinweis zum Wirkungsbereich (#196/#225): :func:`_im_bereich` nimmt zusätzlich jeden
    laut git eingetragenen Worktree der bekannten Hauptrepos überall auf der Platte mit
    (auch in Temp-Ordnern, ``C:/dev/wt-<name>``, Prüf-/Deploy-Worktrees). Dort schützen nicht
    die Wurzel-Grenzen, sondern allein die Wegwerf-Prüfung, die Sperr-Regel (``locked``),
    das stille Stehenlassen ohne Ticket-Nummer außerhalb der Wurzeln und die Sicherung."""
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


def _in_wurzeln(echt: Path) -> bool:
    """True, wenn der aufgelöste Pfad ``echt`` auf ein konfiguriertes Wurzel-Muster passt."""
    return any(echt.match(m) for m in _wurzeln())


def _im_bereich(ordner: Path) -> bool:
    """Aufgelöster Pfad passt auf eine Wurzel und liegt in keinem versteckten Ordner — der
    echte Pfad zählt, nicht der Mustertext (``[.]ssh``, Symlink auf ``.ssh``, #112).

    Erweiterter Bereich (#196/#225): Zusätzlich zählt jeder laut git eingetragene Worktree
    eines bekannten Hauptrepos überall (auch in Temp-Ordnern, ``C:/dev/wt-<name>``, Prüf-/
    Deploy-Worktrees; gleiche Quelle wie :func:`_kandidaten`). Schutz bieten die Wegwerf-Prüfung
    samt Sicherung (``sicherung/<N>``), die Sperr-Regel (``locked``) sowie das stille
    Stehenlassen von Worktrees ohne Ticket-Nummer außerhalb der Wurzeln."""
    try:
        echt = ordner.resolve()
    except OSError:
        return False
    if _versteckt_unter_home(echt):
        return False
    if _in_wurzeln(echt):
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
    if _ist_transport_helfer(lesen):
        return ""
    # #252: wirksame Adresse nach allen Umleitungen (auch Maschine, includeIf, Umgebung)
    if _ziel_im_ordner(echt, lesen):
        return ""
    return _slug(roh[0])


#: Eingebaute Git-URL-Schemas; jedes andere ``<schema>://`` ruft einen ``git-remote-<x>``-Helfer.
_GIT_SCHEMAS = frozenset({"http", "https", "ssh", "git", "file"})


def _ist_transport_helfer(url: str) -> bool:
    """``ext::…``, ``fd::…`` oder fremdes ``<schema>://`` (startet ``git-remote-<x>``)."""
    text = url.strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", text):
        return True
    treffer = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", text)
    return treffer is not None and treffer.group(1).lower() not in _GIT_SCHEMAS


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
    return gh.github_slug(url)


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
    schluessel = _pfad_schluessel(repo)
    for eintrag in _worktree_eintraege(repo):
        pfad = Path(os.path.abspath(eintrag.pfad))
        if pfad.is_dir() and _pfad_schluessel(pfad) != schluessel:
            return True
    return False


def _hauptrepo(worktree: Path) -> Path:
    gemeinsam = Path(aufpasser.git_lauf(worktree, "rev-parse", "--git-common-dir").strip())
    if not gemeinsam.is_absolute():
        gemeinsam = (worktree / gemeinsam).resolve()
    else:
        gemeinsam = gemeinsam.resolve()
    if _git_oder_none(gemeinsam, "rev-parse", "--is-bare-repository") == "true":
        return gemeinsam
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
    if haupt is None or not (haupt / ".git").is_dir():
        return False
    hier, dort = repo / rel, haupt / rel
    try:
        return hier.is_file() and dort.is_file() and hier.read_bytes() == dort.read_bytes()
    except OSError:
        return False


def _ungesichertes(
    repo: Path, ist_worktree: bool, ticket: int | None = None
) -> tuple[list[str], bool, list[tuple[str, str]], str | None]:
    """Regel 8: (ungepushte Köpfe, zu sichernde Dateien da?, Status-Schnappschuss, Server-``sicherung/<N>``).

    Alles, was die Sicherung nicht retten kann, wirft ``_Bleibt``.
    """
    try:
        aufpasser.git_lauf(repo, "fetch", "--prune", "-q", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"git fetch origin gescheitert: {_kurz(fehler)}", fehler=True) from None
    if not ist_worktree and _git_oder_none(repo, "rev-parse", "--verify", "-q", "refs/stash"):
        raise _Bleibt("Stash vorhanden (git stash list)", fuer_mensch=True)
    server_zweige = _server_zweige(repo)
    sicherung_vorher = _sicherung_stand_holen(repo, ticket, server_zweige) if ticket is not None else None
    auf_server = list(server_zweige.values())
    if _nur_im_reflog(repo, ist_worktree, auf_server):
        raise _Bleibt("ungesicherte Commits im Verlauf (Reflog, auf keinem Zweig)", fuer_mensch=True)
    for zeile in aufpasser.git_lauf(repo, "ls-files", "-s").splitlines():
        if zeile.startswith("160000 "):
            raise _Bleibt(f"Submodul {zeile.split(chr(9), 1)[-1]}", fuer_mensch=True)

    alle = _status(repo)
    for _, pfad in alle:
        if pfad.endswith("/"):
            raise _Bleibt(f"eingebettetes Repo {pfad}", fuer_mensch=True)
    erzeugnis = {p for xy, p in _status(repo, *_glob(ERZEUGNISSE)) if xy == "!!"}
    geheim = {p for _, p in _status(repo, *_glob(aufpasser.SICHERUNG_AUSSCHLUSS))}
    # Nur sicher neu Erzeugbares ist nie ein Geheimnis (__pycache__/tempo_zugang.*.pyc).
    # Ein .env unter .review/, node_modules/, .to-spawn/ bleibt Geheimnis.
    geheim -= {p for p in erzeugnis if _neu_erzeugbar(p)}
    haupt = _hauptrepo(repo) if ist_worktree else None
    ungleich = [p for p in sorted(geheim) if not _gleiche_kopie(haupt, repo, p)]
    if ungleich:
        raise _Bleibt(f"Geheimnis-Datei {ungleich[0]} nicht gleich im Hauptrepo", fuer_mensch=True)
    # #1536: Datenbanken lädt die Sicherung nie hoch — weg dürfen nur Test-Kopien.
    datenbanken = {p for _, p in _status(repo, *_glob(aufpasser.NIE_HOCHLADEN))}
    fremd = sorted(p for p in datenbanken if not ist_test_db(repo, p))
    if fremd:
        raise _Bleibt(f"Datenbank {fremd[0]} ist keine Test-Kopie (nicht sicherbar)", fuer_mensch=True)
    for xy, pfad in alle:
        if xy == "!!" and pfad not in geheim | erzeugnis | datenbanken:
            raise _Bleibt(f"ignorierte Datei {pfad} ist kein bekanntes Erzeugnis")
    nicht_hoch = geheim | datenbanken
    zu_sichern = any(xy != "!!" and p not in nicht_hoch for xy, p in alle)

    koepfe = _ungepushte_koepfe(repo, ist_worktree, auf_server)
    if koepfe:
        gegen = ["--not", *auf_server] if auf_server else []
        leck = _geheimnis_pfade(repo, "--ignore-missing", *koepfe, *gegen)
        if leck:
            raise _Bleibt(f"Geheimnis-Datei {leck[0]} in ungepushtem Commit", fuer_mensch=True)
    return koepfe, zu_sichern, alle, sicherung_vorher


def _geheimnis_pfade(cwd: Path, *rev_args: str) -> list[str]:
    """Geheimnis-Pfade (``SICHERUNG_AUSSCHLUSS``) in Commits von ``rev_args`` per ``-z`` (#215).

    Trennt NUL-getrennt über :func:`to_spawn.uebergabe_sichern.pfade_aus_nul`, damit Pfade
    mit Leerzeichen, Zeilenumbruch oder Nicht-ASCII-Zeichen nie gequotet oder zerlegt werden.
    """
    aus = aufpasser.git_lauf(
        cwd,
        "log",
        "-z",
        "--format=",
        "--name-only",
        "--diff-merges=separate",
        *rev_args,
        "--",
        *_glob(aufpasser.SICHERUNG_AUSSCHLUSS),
    )
    return uebergabe_sichern.pfade_aus_nul(aus)


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
#: Obergrenze der Server-Commits für die Baum-/Patch-Prüfung (#253, Schutz vor Alt-Historie).
_SERVER_PRUEF_LIMIT = 1000
#: Deterministische Diff-Optionen ohne externe Diff-Treiber/Textconv und mit vollem Blob-Hash (#253).
_DIFF_OPTIONEN = ("--no-ext-diff", "--no-textconv", "--no-renames", "--full-index")


def _git_bytes(cwd: Path, *args: str, eingabe: bytes | None = None) -> bytes:
    """Führt ``git`` binärsicher mit optionaler Standardeingabe und ``fensterlos()`` aus."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    try:
        ergebnis = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            input=eingabe,
            env=env,
            capture_output=True,
            check=False,
            timeout=300,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args[:3])}: Zeitgrenze 300 s") from None
    except FileNotFoundError as fehler:
        raise RuntimeError(f"git {' '.join(args[:3])}: {fehler}") from None
    if ergebnis.returncode != 0:
        meldung = ergebnis.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"git {' '.join(args[:3])}: {meldung}")
    return ergebnis.stdout


def _commit_infos(repo: Path, shas: Iterable[str]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Liefert ``sha → (baum_sha, eltern_shas)`` über ``--stdin`` (Windows-Befehlszeile bleibt kurz)."""
    eingabe = "".join(f"{s}\n" for s in shas).encode("ascii")
    if not eingabe:
        return {}
    roh = _git_bytes(repo, "log", "--no-walk", "--format=%H%x09%T%x09%P", "--stdin", eingabe=eingabe)
    infos: dict[str, tuple[str, tuple[str, ...]]] = {}
    for zeile in roh.decode("ascii", errors="replace").splitlines():
        teile = zeile.split("\t")
        if len(teile) == 3:
            infos[teile[0]] = (teile[1], tuple(teile[2].split()))
    return infos


def _server_kandidaten(repo: Path, server: list[str], grenzen: set[str]) -> tuple[list[str], set[str]]:
    """Server-Commits abseits von ``grenzen`` (höchstens :data:`_SERVER_PRUEF_LIMIT`): ``(SHAs, Baum-SHAs)``."""
    zeilen = [f"{s}\n" for s in server] + [f"^{g}\n" for g in sorted(grenzen)]
    roh = _git_bytes(
        repo,
        "rev-list",
        "--ignore-missing",
        f"--max-count={_SERVER_PRUEF_LIMIT}",
        "--format=%H%x09%T",
        "--stdin",
        eingabe="".join(zeilen).encode("ascii"),
    )
    shas: list[str] = []
    baeume: set[str] = set()
    for zeile in roh.decode("ascii", errors="replace").splitlines():
        if "\t" in zeile:
            sha, _, baum = zeile.partition("\t")
            shas.append(sha)
            baeume.add(baum)
    return shas, baeume


def _patch_ids_von_commits(repo: Path, shas: Iterable[str]) -> dict[str, str]:
    """``commit_sha → patch_id`` (``git patch-id --stable``) für alle Nicht-Merge-Commits mit Diff."""
    eingabe = "".join(f"{s}\n" for s in shas).encode("ascii")
    if not eingabe:
        return {}
    diffs = _git_bytes(
        repo,
        "log",
        "--no-walk",
        "--no-merges",
        "--format=commit %H",
        "-p",
        *_DIFF_OPTIONEN,
        "--stdin",
        eingabe=eingabe,
    )
    if not diffs:
        return {}
    roh = _git_bytes(repo, "patch-id", "--stable", eingabe=diffs)
    ids: dict[str, str] = {}
    for zeile in roh.decode("ascii", errors="replace").splitlines():
        teile = zeile.split()
        if len(teile) >= 2:
            ids[teile[1]] = teile[0]
    return ids


def _diff_patch_id(repo: Path, basis: str, ziel: str) -> str | None:
    """Stabile Patch-ID für den Gesamt-Diff ``basis..ziel`` (Squash-Merge); ``None`` bei leerem Diff."""
    diff = _git_bytes(repo, "diff", *_DIFF_OPTIONEN, basis, ziel)
    if not diff:
        return None
    roh = _git_bytes(repo, "patch-id", "--stable", eingabe=diff).decode("ascii", errors="replace").strip()
    teile = roh.split()
    return teile[0] if teile else None


def _verwaist_auf_server(repo: Path, verwaist: set[str], server: list[str]) -> bool:
    """#253: Liegen alle verwaisten Reflog-Commits als Baum oder Patch bereits im Server-Stand?

    Erkennt ``amend``, ``rebase``, ``pull --rebase``, Squash-Merge (auch mehrerer Commits
    auf bewegtem Hauptzweig) und Force-Push nach ``amend``. Leere Commits, Reverts zurück
    auf die Basis und wirklich verlorene Änderungen bleiben fail-closed ungedeckt.
    """
    if not server:
        return False
    infos = _commit_infos(repo, sorted(verwaist))
    if not verwaist <= infos.keys():
        return False
    hat_kind = {p for _, ps in infos.values() for p in ps if p in verwaist}
    spitzen = sorted(c for c in verwaist if c not in hat_kind)
    alle_grenzen = {p for _, ps in infos.values() for p in ps if p not in verwaist}
    grenz_infos = _commit_infos(repo, sorted(alle_grenzen)) if alle_grenzen else {}
    baum_von: dict[str, str] = {c: t for c, (t, _) in infos.items()}
    baum_von.update({c: t for c, (t, _) in grenz_infos.items()})

    def vorfahren_im_graph(start: str) -> set[str]:
        gesehen: set[str] = set()
        stapel = [start]
        while stapel:
            aktuell = stapel.pop()
            if aktuell in gesehen or aktuell not in verwaist:
                continue
            gesehen.add(aktuell)
            stapel.extend(p for p in infos[aktuell][1] if p in verwaist and p not in gesehen)
        return gesehen

    def baum_trifft(commit: str, server_baeume: set[str], grenz_baeume: set[str]) -> bool:
        t = baum_von[commit]
        if t not in server_baeume or t in grenz_baeume:
            return False
        # Leerer Commit (Baum == Baum eines eigenen Elternteils) zählt nie als Treffer (#254).
        return all(t != baum_von.get(p) for p in infos[commit][1])

    abgedeckt: set[str] = set()
    for spitze in spitzen:
        gruppe = vorfahren_im_graph(spitze)
        if gruppe <= abgedeckt:
            continue
        grenzen = {p for c in gruppe for p in infos[c][1] if p not in verwaist}
        grenz_baeume = {baum_von[g] for g in grenzen if g in baum_von}
        kandidaten, server_baeume = _server_kandidaten(repo, server, grenzen)
        if not kandidaten:
            return False
        if baum_trifft(spitze, server_baeume, grenz_baeume):
            abgedeckt |= gruppe
            continue
        server_patches = set(_patch_ids_von_commits(repo, kandidaten).values())
        # Squash-Merge (ein oder mehrere Commits, auch auf bewegtem Hauptzweig):
        # Gesamt-Diff ab maximaler Grenze gegen die Server-Patches prüfen.
        if grenzen:
            if len(grenzen) == 1:
                max_grenzen = sorted(grenzen)
            elif len(grenzen) <= _REFLOG_HAPPEN:
                max_grenzen = aufpasser.git_lauf(repo, "merge-base", "--independent", *sorted(grenzen)).split()
            else:
                max_grenzen = []
            if len(max_grenzen) == 1:
                gesamt = _diff_patch_id(repo, max_grenzen[0], spitze)
                if gesamt and gesamt in server_patches:
                    abgedeckt |= gruppe
                    continue
        # Einzel-Commits (Rebase, pull --rebase, umgeordnete Commits): Baum oder Patch je Commit.
        offen = [c for c in sorted(gruppe) if c not in abgedeckt]
        for c in list(offen):
            if baum_trifft(c, server_baeume, grenz_baeume):
                abgedeckt |= vorfahren_im_graph(c)
        offen = [c for c in offen if c not in abgedeckt]
        if offen:
            einzel = _patch_ids_von_commits(repo, offen)
            for c in offen:
                if (pid := einzel.get(c)) and pid in server_patches:
                    abgedeckt.add(c)
        if not gruppe <= abgedeckt:
            return False
    return abgedeckt == verwaist


def _existiert_streng(pfad: Path) -> bool:
    """``True`` wenn ``pfad`` existiert, ``False`` nur bei ``FileNotFoundError`` (``PermissionError`` steigt auf)."""
    try:
        os.lstat(pfad)
    except FileNotFoundError:
        return False
    return True


def _reflog_dateien(git_dir: Path, ist_worktree: bool) -> list[Path]:
    """Alle Reflog-Dateien unter ``logs/`` (und beim Klon ``worktrees/*/logs/``), fail-closed (#347).

    Anders als ``Path.rglob``/``glob`` (überspringen unter Python 3.13 unlesbare Ordner still)
    lässt ``os.scandir`` jeden ``OSError`` durch — ein gesperrter Unterordner hält den Ordner.
    """
    wurzeln: list[Path] = []
    logs_dir = git_dir / "logs"
    if _existiert_streng(logs_dir):
        wurzeln.append(logs_dir)
    wt_dir = git_dir / "worktrees"
    if not ist_worktree and _existiert_streng(wt_dir):
        with os.scandir(wt_dir) as it:
            eintraege = sorted(it, key=lambda e: e.name)
        for eintrag in eintraege:
            if eintrag.is_dir(follow_symlinks=False):
                wt_logs = Path(eintrag.path) / "logs"
                if _existiert_streng(wt_logs):
                    wurzeln.append(wt_logs)
    dateien: list[Path] = []
    stapel = list(wurzeln)
    while stapel:
        ordner = stapel.pop()
        with os.scandir(ordner) as it:
            for eintrag in it:
                if eintrag.is_dir(follow_symlinks=False):
                    stapel.append(Path(eintrag.path))
                elif eintrag.is_file(follow_symlinks=True):
                    dateien.append(Path(eintrag.path))
    return dateien


def _nur_im_reflog(repo: Path, ist_worktree: bool, auf_server: list[str] | None = None) -> bool:
    """#235 R1/#253: liegt ein Commit nur im Reflog, der mit dem Ordner verschwindet?

    Verschwinden: beim Klon alle Reflogs (auch die verknüpfter Worktrees), beim Worktree die
    seines eigenen Git-Verzeichnisses (die Zweig-Reflogs liegen im Hauptklon und bleiben). Kein Verlust ist, was von einem Zweig auf
    ``origin`` (nach dem ``fetch`` in :func:`_ungesichertes`) oder einem lokalen Ref erreichbar
    ist — lokale Refs prüfen die übrigen Regeln (Sicherung ``sicherung/<N>`` oder bleibt).
    #253: Stände, deren Baum oder Patch bereits im Server-Stand liegt (``amend``, ``rebase``,
    ``pull --rebase``, Squash-Merge, Force-Push nach ``amend``), gelten ebenfalls als gesichert.

    #254: „auf dem Server“ heißt ``ls-remote`` (nicht fälschbare Tracking-Refs). Die Stände
    kommen roh aus den Reflog-Dateien (``git log -g`` überspringt fehlende Objekte still);
    ein fehlendes Objekt, ``core.logAllRefUpdates=false`` (Verlauf fehlt) oder ein anderer
    Ref-Speicher als ``files`` (keine Reflog-Dateien) ⇒ ``_Bleibt``.
    """
    if _git_oder_none(repo, "config", "--bool", "--get", "core.logAllRefUpdates") == "false":
        raise _Bleibt("core.logAllRefUpdates=false: Verlauf (Reflog) nicht prüfbar", fuer_mensch=True)
    if (_git_oder_none(repo, "config", "--get", "extensions.refStorage") or "files").lower() != "files":
        raise _Bleibt("Ref-Speicher ohne Reflog-Dateien: Verlauf nicht prüfbar", fuer_mensch=True)
    # Was mit dem Ordner stirbt: beim Worktree sein Git-Verzeichnis (``HEAD``, ``refs/worktree/*``,
    # ``refs/bisect/*``), beim Klon das ganze ``.git`` samt ``worktrees/*/logs`` verknüpfter Worktrees.
    git_dir = Path(aufpasser.git_lauf(repo, "rev-parse", "--absolute-git-dir").strip())
    staende: set[str] = set()
    try:
        dateien = _reflog_dateien(git_dir, ist_worktree)
        inhalte = [(d, d.read_bytes()) for d in dateien]
    except OSError as fehler:
        raise _Bleibt(f"Reflog nicht lesbar: {fehler}", fehler=True) from None
    for datei, inhalt in inhalte:
        for zeile in inhalt.splitlines():
            felder = zeile.split(b" ", 2)
            if len(felder) < 3 or not all(re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?", f) for f in felder[:2]):
                raise _Bleibt(f"unlesbare Reflog-Zeile in {datei.name}", fehler=True)
            staende |= {f.decode() for f in felder[:2] if f.strip(b"0")}
    server = _server_koepfe(repo) if auf_server is None else auf_server
    sicher = ("--not", "--exclude=refs/remotes/*", "--all", *server)
    liste = sorted(staende)
    verwaist: set[str] = set()
    for i in range(0, len(liste), _REFLOG_HAPPEN):
        happen = liste[i : i + _REFLOG_HAPPEN]
        try:  # ohne --ignore-missing: ein fehlendes Objekt ist ein Fehler
            aufpasser.git_lauf(repo, "rev-list", "--no-walk", *happen)
        except RuntimeError as fehler:
            raise _Bleibt(f"Reflog verweist auf fehlendes Objekt: {_kurz(fehler)}", fuer_mensch=True) from None
        aus = aufpasser.git_lauf(repo, "rev-list", "--ignore-missing", *happen, *sicher)
        verwaist.update(aus.split())
    if not verwaist:
        return False
    return not _verwaist_auf_server(repo, verwaist, server)


def _ungepushte_koepfe(repo: Path, ist_worktree: bool, auf_server: list[str] | None = None) -> list[str]:
    """#179: Refs mit Commits auf keinem erreichbaren Remote-Kopf (``ls-remote origin``)."""
    server = _server_koepfe(repo) if auf_server is None else auf_server
    return [
        ref
        for ref in _lokale_koepfe(repo, ist_worktree)
        if aufpasser.git_lauf(repo, "rev-list", "--ignore-missing", "-n", "1", ref, "--not", *server).strip()
    ]


def _koepfe_auf_server(repo: Path, ist_worktree: bool) -> None:
    """#235 M1a/#179: jeder lokale Kopf muss von einem Zweig-Kopf auf dem Server (``ls-remote``)
    erreichbar sein — lokale Tracking-Refs lassen sich fälschen oder per Refspec verengen.
    Server-Köpfe, die lokal fehlen, zählen nicht (``--ignore-missing``). Sonst ``_Bleibt``."""
    fehlend = _ungepushte_koepfe(repo, ist_worktree)
    if fehlend:
        raise _Bleibt(f"{OHNE_NUMMER}: {fehlend[0]} nicht auf dem Server (ls-remote origin)", fuer_mensch=True)


def _server_zweig_sha(repo: Path, zweig: str) -> str:
    """#179: SHA von ``refs/heads/<zweig>`` direkt vom erreichbaren Remote ``origin`` (fail-closed)."""
    ref = f"refs/heads/{zweig}"
    try:
        roh = _git_ohne_frage(repo, "ls-remote", "origin", ref).strip()
    except RuntimeError as fehler:
        raise _Bleibt(f"Server nicht erreichbar (ls-remote {zweig}): {_kurz(fehler)}", fehler=True) from None
    treffer = _LS_REMOTE_ZEILE.fullmatch(roh)
    if treffer is None or not roh.endswith(f"\t{ref}"):
        raise _Bleibt(f"origin/{zweig} am Server nicht lesbar (ls-remote)", fehler=True)
    return treffer.group(1)


def _server_zweige(repo: Path) -> dict[str, str]:
    """``refs/heads/… → SHA`` aller Zweig-Köpfe von ``origin`` laut Server (``ls-remote --heads``);
    nicht erreichbar oder unlesbare Zeile ⇒ ``_Bleibt``."""
    try:
        roh = _git_ohne_frage(repo, "ls-remote", "--heads", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"Server nicht erreichbar (ls-remote): {_kurz(fehler)}", fehler=True) from None
    zweige: dict[str, str] = {}
    for zeile in roh.splitlines():
        if not zeile.strip():
            continue
        treffer = _LS_REMOTE_ZEILE.match(zeile)
        if treffer is None:  # #235 N2: nur „<sha>\trefs/heads/…“ ist eine Server-Angabe
            raise _Bleibt(f"unlesbare ls-remote-Zeile {_kurz(zeile)!r}", fehler=True)
        sha, _, ref = zeile.partition("\t")
        zweige[ref.strip()] = sha
    return zweige


def _sicherung_stand_holen(repo: Path, ticket: int, server_zweige: dict[str, str]) -> str | None:
    """#242: holt ``refs/heads/sicherung/<ticket>``, falls am Server vorhanden aber lokal fehlend.

    Einzelzweig-Klone ohne ``refs/remotes/origin/sicherung/*`` bekommen so die Objekte
    des bestehenden Sicherungs-Zweigs, damit Prüfung und Folge-Sicherung nahtlos anketten.
    """
    ref = f"refs/heads/sicherung/{ticket}"
    sha = server_zweige.get(ref)
    if sha is None:
        return None
    if _git_oder_none(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None:
        return sha
    try:
        _git_ohne_frage(repo, "fetch", "-q", "origin", ref)
    except RuntimeError as fehler:
        raise _Bleibt(f"sicherung/{ticket} nicht holbar: {_kurz(fehler)}", fehler=True) from None
    if _git_oder_none(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None:
        return sha
    geholt = _git_oder_none(repo, "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}")
    if geholt:
        server_zweige[ref] = geholt
        return geholt
    raise _Bleibt(f"sicherung/{ticket} nach fetch lokal nicht lesbar", fehler=True)


def _server_koepfe(repo: Path) -> list[str]:
    """SHAs aller Zweig-Köpfe von ``origin`` laut Server (``ls-remote --heads``); nicht
    erreichbar oder unlesbare Zeile ⇒ ``_Bleibt``.

    ponytail: ein ls-remote je Aufrufer (ohne Nummer zwei je Ordner), alle Köpfe auf einer
    Kommandozeile; zwischenspeichern bzw. in Happen, wenn Zweig-Zahl oder Laufzeit stört."""
    return list(_server_zweige(repo).values())


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


#: Lauf-Zwischenspeicher für Linux-``/proc``-Pfade (#615; gesetzt über :func:`_linux_prozess_lauf_cache`).
#: ``None`` = kein Cache aktiv (jeder Aufruf liest frisch); nie über Läufe hinweg.
_LINUX_PROZESSE_CACHE: ContextVar[dict[str, Any] | None] = ContextVar(
    "hausmeister_linux_prozesse_cache",
    default=None,
)


@contextmanager
def _linux_prozess_lauf_cache(*, aktiv: bool = True, praefixe: tuple[str, ...] | None = None) -> Iterator[None]:
    """Aktiviert oder pausiert den Lauf-Zwischenspeicher für Linux-``/proc``-Scans (#615).

    Bei vielen Zielordnern unter derselben Wurzel (z. B. :func:`helfer_altbestand_aufraeumen`)
    wird ``/proc`` einmalig pro Lauf gescannt statt je Ordner erneut. Mit ``praefixe`` werden
    nur Pfade unterhalb der relevanten Wurzeln gemerkt.
    """
    norm = tuple(dict.fromkeys(p.rstrip("/") or "/" for p in praefixe)) if praefixe else None
    token = _LINUX_PROZESSE_CACHE.set({"praefixe": norm} if aktiv else None)
    try:
        yield
    finally:
        _LINUX_PROZESSE_CACHE.reset(token)


def _pfad_unter_praefix(ziel: str, praefix: str) -> bool:
    """True, wenn ``ziel`` gleich ``praefix`` ist oder direkt darunter liegt."""
    if praefix == "/":
        return ziel.startswith("/")
    return ziel == praefix or ziel.startswith(praefix + "/")


def _linux_proc_pfade(praefixe: tuple[str, ...] | None = None) -> list[tuple[int, str]]:
    """Liest einmalig ``/proc`` (cwd, exe, fd) und liefert ``(pid, ziel)`` für passende Präfixe."""
    try:
        with os.scandir("/proc") as it:
            pids = [int(e.name) for e in it if e.name.isdigit()]
    except OSError:
        raise _Bleibt("Prozessliste nicht lesbar", fehler=True) from None

    treffer: list[tuple[int, str]] = []
    for pid in pids:
        basis_str = f"/proc/{pid}"
        basis = Path(basis_str)
        try:
            with open(f"{basis_str}/stat", encoding="utf-8", errors="replace") as f:
                stat_text = f.read()
        except OSError:
            continue  # Prozess schon weg
        if stat_text.rsplit(")", 1)[-1].split()[:1] == ["Z"]:
            continue  # Zombie: kein cwd, keine Dateien
        ziele: list[str] = []
        for teil in ("cwd", "exe"):
            ziel = _link_lesen(pid, basis / teil)
            if ziel is not None:
                ziele.append(ziel)
        try:
            with os.scandir(f"{basis_str}/fd") as fd_it:
                for fd in fd_it:
                    try:
                        ziele.append(os.readlink(fd.path))
                    except OSError:
                        continue  # inzwischen geschlossen
        except OSError as fehler:
            _unlesbar_pruefen(pid, basis, fehler)
        for z in ziele:
            if praefixe is None or any(_pfad_unter_praefix(z, p) for p in praefixe):
                treffer.append((pid, z))
    return treffer


def _kein_prozess(ordner: Path, *, probe: bool = False) -> None:
    """Regel 7: ``_Bleibt``, falls ein Prozess im Ordner arbeitet (cwd, exe, offene
    Datei) oder das nicht feststellbar ist. Linux über ``/proc``; Windows über die
    CIM-Prozessliste, mit ``probe=True`` (nur echter Lauf, direkt vor dem Löschen)
    zusätzlich die Umbenennungs-Probe (siehe :mod:`to_spawn.hausmeister_windows`)."""
    if _ist_windows():
        try:
            with (
                prozessbaum.arbeitsordner_lauf_cache(aktiv=False) if probe else nullcontext(),
                hausmeister_windows.prozess_lauf_cache(aktiv=False) if probe else nullcontext(),
            ):
                hausmeister_windows.kein_prozess(ordner)
            if probe:
                hausmeister_windows.umbenennen_probe(ordner)
        except hausmeister_windows.Bleibt as b:
            raise _Bleibt(b.grund, fuer_mensch=b.fuer_mensch, fehler=b.fehler) from None
        return
    ordner_s = str(ordner).rstrip("/") or "/"
    cache = _LINUX_PROZESSE_CACHE.get()
    if cache is not None and not probe:
        if "wert" not in cache:
            try:
                cache["wert"] = _linux_proc_pfade(cache.get("praefixe"))
            except _Bleibt as fehler:
                cache["wert"] = fehler
                raise
        wert = cache["wert"]
        if isinstance(wert, _Bleibt):
            raise wert
        for pid, ziel in wert:
            if _pfad_unter_praefix(ziel, ordner_s):
                raise _Bleibt(f"Prozess {pid} arbeitet im Ordner oder hat Dateien offen")
        return
    treffer = _linux_proc_pfade((ordner_s,))
    if treffer:
        raise _Bleibt(f"Prozess {treffer[0][0]} arbeitet im Ordner oder hat Dateien offen")


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
        eintraege = _worktree_eintraege(hauptrepo)
    except (RuntimeError, OSError) as fehler:
        log.warning("git worktree list in %s scheiterte: %s", hauptrepo, fehler)
        return False
    ziel = _pfad_schluessel(worktree)
    return any(_pfad_schluessel(Path(os.path.abspath(e.pfad))) == ziel for e in eintraege[1:])


# --- Löschen + Log --------------------------------------------------------------


def _loeschen(ordner: Path, ist_worktree: bool) -> None:
    if ist_worktree:
        hauptrepo = _hauptrepo(ordner)
        zweig = _git_oder_none(ordner, "symbolic-ref", "--short", "-q", "HEAD")
        aufpasser.git_lauf(hauptrepo, "worktree", "remove", "--force", str(ordner))
        aufpasser.git_lauf(hauptrepo, "worktree", "prune")
        if zweig and zweig.startswith("ticket/"):  # #1439: wirft nie, entscheidet selbst
            erg = zweig_aufraeumen(hauptrepo, zweig, loeschen=True, ort="lokal")
            if erg.fehler or (erg.art == "bleibt" and erg.fuer_mensch):
                log.warning("Hausmeister: %s entfernt, Zweig %s bleibt: %s", ordner, zweig, erg.grund)
    elif sys.version_info >= (3, 12):
        shutil.rmtree(ordner, onexc=hausmeister_windows.schreibschutz_weg)
    else:
        shutil.rmtree(ordner, onerror=hausmeister_windows.schreibschutz_weg)


def helfer_ordner_loeschen(ort: Path, name: str) -> bool:
    """Genau den Helfer-Temp-Ordner ``ort/name`` löschen (#551); ``False``, wenn er fehlt.

    Namensregeln prüft :mod:`to_spawn.helfer_temp`; hier nur das Sicherheitsnetz:
    ``ValueError`` (nichts gelöscht), wenn ``ort`` oder das Ziel ein Symlink ist, das Ziel kein
    Ordner ist oder aufgelöst nicht direkt unter ``ort`` liegt (``..``, ``/``, leerer Name).
    """
    ziel = ort / name
    if ort.is_symlink() or ziel.is_symlink():
        raise ValueError(f"Helfer-Temp: Symlink in {ziel} abgelehnt")
    if not ziel.exists():
        return False
    if not ziel.is_dir() or ziel.resolve().parent != ort.resolve():
        raise ValueError(f"Helfer-Temp: {ziel} liegt nicht direkt unter {ort}")
    _loeschen(ziel, ist_worktree=False)
    log.info("Hausmeister: Helfer-Temp %s gelöscht", ziel)
    return True


#: #615: Festes Namensmuster der alten Helfer-Temp-Ordner direkt im Home (Rest aus #551).
HELFER_ALTBESTAND_MUSTER = re.compile(r"^tmp-l[1-9]\d*-[a-z0-9][a-z0-9_-]*$")
#: #615: Vorgabe-Mindestalter in Tagen für :func:`helfer_altbestand_aufraeumen`.
HELFER_ALTBESTAND_TAGE = 1.0
#: #615: Vorgabe-Zeitgrenze in Sekunden für :func:`helfer_altbestand_aufraeumen`.
HELFER_ALTBESTAND_ZEITGRENZE_S = 60.0


class _ZeitgrenzeErreicht(Exception):
    """Zeitgrenze eines Aufräum-Laufs erreicht (#615)."""


def _baum_alter_pruefen(
    ordner: Path,
    *,
    jetzt: float,
    mindestalter_s: float,
    frist: float | None = None,
    uhr: Callable[[], float] = time.monotonic,
) -> None:
    """Prüft ohne Symlink-Verfolgung, dass weder ``ordner`` noch ein Eintrag darin zu jung ist."""
    try:
        wurzel_info = os.lstat(ordner)
    except OSError as fehler:
        raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
    if jetzt - wurzel_info.st_mtime < mindestalter_s:
        raise _Bleibt("frisch (jünger als Mindestalter)")
    stapel: list[str] = [str(ordner)]
    while stapel:
        if frist is not None and uhr() >= frist:
            raise _ZeitgrenzeErreicht()
        aktuell = stapel.pop()
        try:
            with os.scandir(aktuell) as it:
                eintraege = list(it)
        except OSError as fehler:
            raise _Bleibt(f"Ordner nicht lesbar: {_kurz(fehler)}", fehler=True) from None
        for idx, eintrag in enumerate(eintraege):
            if frist is not None and idx and idx % 256 == 0 and uhr() >= frist:
                raise _ZeitgrenzeErreicht()
            try:
                info = eintrag.stat(follow_symlinks=False)
            except OSError as fehler:
                raise _Bleibt(f"Eintrag nicht lesbar ({eintrag.name}): {_kurz(fehler)}", fehler=True) from None
            if jetzt - info.st_mtime < mindestalter_s:
                rel = Path(eintrag.path).relative_to(ordner).as_posix()
                raise _Bleibt(f"frisch ({rel})")
            if stat.S_ISDIR(info.st_mode):
                stapel.append(eintrag.path)


def helfer_altbestand_aufraeumen(
    *,
    loeschen: bool = False,
    aelter_als_tage: float = HELFER_ALTBESTAND_TAGE,
    jetzt: float | None = None,
    heim: Path | None = None,
    zeitgrenze_s: float | None = HELFER_ALTBESTAND_ZEITGRENZE_S,
    uhr: Callable[[], float] = time.monotonic,
) -> list[Ergebnis]:
    """Räumt alte ``~/tmp-l<N>-…``-Helfer-Ordner direkt im Home sicher weg (#615).

    Erlaubter Hausmeister-Weg statt Shell-Löschen mit Platzhalter: prüft festes
    Namensmuster (:data:`HELFER_ALTBESTAND_MUSTER`), echten Ordner (kein Symlink,
    eigener Besitzer, direkt im Home), kein ``.git``, Mindestalter im gesamten Baum,
    keinen laufenden Prozess, TOCTOU-Identität ``(st_dev, st_ino)`` und schreibt die
    Log-Zeile vor dem Löschen („ohne Log kein Löschen“). Vorgabe ist der Trockenlauf.
    """
    if isinstance(aelter_als_tage, bool) or not math.isfinite(aelter_als_tage) or aelter_als_tage < 1:
        raise ValueError(f"Helfer-Altbestand: --aelter-als-tage {aelter_als_tage!r} abgelehnt (endliche Zahl >= 1)")
    if zeitgrenze_s is not None and (
        isinstance(zeitgrenze_s, bool) or not math.isfinite(zeitgrenze_s) or zeitgrenze_s <= 0
    ):
        raise ValueError(f"Helfer-Altbestand: --zeitgrenze {zeitgrenze_s!r} abgelehnt (endliche Zahl > 0)")
    basis = heim if heim is not None else config.heim_ordner()
    if basis.is_symlink() or not basis.is_dir():
        log.warning("Hausmeister: Helfer-Altbestand-Basis %s ist kein echter Ordner", basis)
        return []
    basis_echt = basis.resolve()
    jetzt_ts = time.time() if jetzt is None else jetzt
    mindestalter_s = aelter_als_tage * 86400
    frist = (uhr() + zeitgrenze_s) if zeitgrenze_s is not None else None
    uid = os.getuid() if hasattr(os, "getuid") else None
    geschuetzt = set(_geschuetzte_ordner())
    trocken = not loeschen
    ergebnisse: list[Ergebnis] = []

    try:
        with os.scandir(basis) as it:
            eintraege = sorted(it, key=lambda e: e.name)
    except OSError as fehler:
        log.warning("Hausmeister: Helfer-Altbestand in %s nicht lesbar: %s", basis, fehler)
        return [Ergebnis(basis, "bleibt", f"Home nicht lesbar: {_kurz(fehler)}", None, trocken=trocken, fehler=True)]

    kandidaten = [e for e in eintraege if HELFER_ALTBESTAND_MUSTER.match(e.name)]
    gesamt = len(kandidaten)
    bearbeitet = 0

    def _zeitgrenze_melden() -> None:
        offen = max(gesamt - bearbeitet, 0)
        assert zeitgrenze_s is not None
        meldung = (
            f"Zeitgrenze ({zeitgrenze_s:g} s) erreicht — {bearbeitet} von {gesamt} Ordner(n) geprüft, "
            f"{offen} noch offen; Befehl einfach erneut starten"
        )
        log.warning("Hausmeister: Helfer-Altbestand: %s", meldung)
        erg_zeit = Ergebnis(basis, "bleibt", meldung, None, trocken=trocken, fehler=True)
        _log_schreiben(erg_zeit)
        ergebnisse.append(erg_zeit)

    praefixe = tuple(dict.fromkeys((str(basis), str(basis_echt))))
    cache_ctx = _linux_prozess_lauf_cache(praefixe=praefixe) if not _ist_windows() else nullcontext()
    with cache_ctx:
        for eintrag in kandidaten:
            if frist is not None and uhr() >= frist:
                _zeitgrenze_melden()
                break
            ordner = Path(eintrag.path)
            try:
                info = os.lstat(ordner)
            except OSError as fehler:
                erg = Ergebnis(ordner, "bleibt", f"nicht prüfbar: {_kurz(fehler)}", None, trocken=trocken, fehler=True)
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue
            if stat.S_ISLNK(info.st_mode):
                erg = Ergebnis(ordner, "bleibt", "Helfer-Altbestand: Symlink", None, trocken=trocken)
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue
            if not stat.S_ISDIR(info.st_mode):
                bearbeitet += 1
                continue
            try:
                if ordner.name in geschuetzt:
                    raise _Bleibt("geschützter Ordner")
                if uid is not None and info.st_uid != uid:
                    raise _Bleibt("fremder Besitzer")
                if ordner.resolve().parent != basis_echt:
                    raise _Bleibt("liegt aufgelöst nicht direkt im Home")
                if os.path.lexists(ordner / ".git"):
                    raise _Bleibt("enthält .git — über regulären Hausmeister prüfen")
                _baum_alter_pruefen(ordner, jetzt=jetzt_ts, mindestalter_s=mindestalter_s, frist=frist, uhr=uhr)
                _kein_prozess(ordner)
            except _ZeitgrenzeErreicht:
                _zeitgrenze_melden()
                break
            except _Bleibt as b:
                erg = Ergebnis(ordner, "bleibt", b.grund, None, trocken=trocken, fehler=b.fehler)
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue

            grund = f"Helfer-Altbestand (Muster tmp-l<N>-…, älter als {aelter_als_tage:g} Tag(e), kein Prozess)"
            erg = Ergebnis(ordner, "gelöscht", grund, None, trocken=trocken)
            if trocken:
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue

            try:
                info_neu = os.lstat(ordner)
                if stat.S_ISLNK(info_neu.st_mode) or not stat.S_ISDIR(info_neu.st_mode):
                    raise _Bleibt("Helfer-Altbestand: kein echter Ordner mehr (Symlink?)")
                if (info_neu.st_dev, info_neu.st_ino) != (info.st_dev, info.st_ino):
                    raise _Bleibt("Helfer-Altbestand: Ordner während der Prüfung ersetzt")
                if ordner.resolve().parent != basis_echt:
                    raise _Bleibt("liegt aufgelöst nicht direkt im Home")
                if _ist_windows():
                    _kein_prozess(ordner, probe=True)
            except _Bleibt as b:
                erg = Ergebnis(ordner, "bleibt", b.grund, None, trocken=False, fehler=b.fehler)
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue
            except OSError as fehler:
                erg = Ergebnis(
                    ordner, "bleibt", f"Gegenprobe gescheitert: {_kurz(fehler)}", None, trocken=False, fehler=True
                )
                _log_schreiben(erg)
                ergebnisse.append(erg)
                bearbeitet += 1
                continue

            if not _log_schreiben(erg):
                erg = replace(
                    erg,
                    art="bleibt",
                    grund="Hausmeister-Log nicht schreibbar — ohne Log kein Löschen",
                    fehler=True,
                )
                ergebnisse.append(erg)
                bearbeitet += 1
                continue
            try:
                temp_ordner_loeschen(ordner, erwartet=(info.st_dev, info.st_ino))
            except (RuntimeError, OSError) as fehler:
                erg = replace(erg, art="bleibt", grund=f"Löschen gescheitert: {_kurz(fehler)}", fehler=True)
                _log_schreiben(erg)
            ergebnisse.append(erg)
            bearbeitet += 1
    return ergebnisse


def temp_ordner_loeschen(ordner: Path, *, erwartet: tuple[int, int] | None = None) -> None:
    """Entfernt einen geprüften Temp-Rest (#136, #177): verknüpfte Worktrees sauber über Git,
    sonst über :func:`_loeschen` (mit Windows-Schreibschutz-Behandlung).

    Die Prüfregeln liegen in :mod:`to_spawn.temp_ordner`; die eigentliche Löschung bleibt
    allein hier, damit es im gesamten Skill genau eine Lösch-Stelle gibt. Direkt vor dem
    Löschen wird per ``os.lstat`` erneut gegen Symlink-Tausch und (mit ``erwartet``) gegen
    ``(st_dev, st_ino)``-Wechsel geprüft (TOCTOU-Schutz).
    """
    info = os.lstat(ordner)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"Temp-Rest {ordner} ist kein echter Ordner mehr (Symlink?)")
    if erwartet is not None and (info.st_dev, info.st_ino) != erwartet:
        raise OSError(f"Temp-Rest {ordner} wurde während der Prüfung ersetzt")
    git_marke = ordner / ".git"
    if git_marke.is_file() and not git_marke.is_symlink():
        try:
            haupt = _hauptrepo(ordner)
        except RuntimeError:
            haupt = None
        if haupt is not None and _als_worktree_eingetragen(haupt, ordner):
            aufpasser.git_lauf(haupt, "worktree", "remove", "--force", str(ordner))
            aufpasser.git_lauf(haupt, "worktree", "prune")
            return
    _loeschen(ordner, False)


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


def bleib_gruende(ergebnisse: Sequence[Ergebnis]) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Zählt die Bleib-Gründe absteigend (Zahlen als ``N``, ohne Klammer-Zusatz).

    Einzige Zähl- und Normier-Stelle für :func:`_kennzahl_schreiben` und
    :meth:`to_spawn.aufpasser.Aufpasser._hausmeister_lauf` (#235, #261).
    """
    gruende: dict[str, int] = {}
    fehler_je: dict[str, list[str]] = {}
    for e in ergebnisse:
        if e.art == "bleibt":
            schluessel = re.sub(r"\d+", "N", e.grund.split(" (", 1)[0])
            gruende[schluessel] = gruende.get(schluessel, 0) + 1
            if e.fehler:
                fehler_je.setdefault(schluessel, []).append(f"{e.ordner}: {e.grund}")
    geordnet = dict(sorted(gruende.items(), key=lambda kv: (-kv[1], kv[0])))
    return geordnet, fehler_je


def bleib_zeilen(ergebnisse: Sequence[Ergebnis]) -> list[str]:
    """Formatierte Log-Zeilen je Bleib-Grund (Anzahl + Grund, bei Fehlern Ordner und Text)."""
    gruende, fehler_je = bleib_gruende(ergebnisse)
    zeilen: list[str] = []
    for schluessel, anzahl in gruende.items():
        fehl = fehler_je.get(schluessel)
        zusatz = f" — Fehler: {' | '.join(fehl)}" if fehl else ""
        zeilen.append(f"Hausmeister: bleibt {anzahl}× {schluessel}{zusatz}")
    return zeilen


def _kennzahl_schreiben(ergebnisse: list[Ergebnis], *, trocken: bool) -> None:
    """#1536: je Takt eine Zeile ``art="kennzahl"`` in :func:`_kennzahl_pfad` — „x von y gelöscht, z bleiben“
    und die Bleib-Gründe gezählt (Zahlen im Grund als ``N``, ohne Klammer-Zusatz)."""
    geloescht = sum(1 for e in ergebnisse if e.art != "bleibt")
    gruende, _ = bleib_gruende(ergebnisse)
    satz = f"{geloescht} von {len(ergebnisse)} gelöscht, {len(ergebnisse) - geloescht} bleiben"
    log.info("Hausmeister: Kennzahl %s%s", satz, " (trocken)" if trocken else "")
    # #235: eine Zeile je Bleib-Grund mit Anzahl; Fehler zusätzlich mit Ordner und Text
    for zeile in bleib_zeilen(ergebnisse):
        log.info("%s", zeile)
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
            "bleib_gruende": gruende,
        },
        _kennzahl_pfad(),
    )


def _kennzahl_pfad() -> Path:
    return _log_pfad().with_name(KENNZAHL_DATEI)


def _log_pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_LOG")
    return config.expand_heim(roh) if roh else config.heim_ordner() / ".claude" / "to-spawn" / "hausmeister.jsonl"


def log_art(erg: Ergebnis) -> str:
    """Lesbare Aktions-Bezeichnung für Log und Ausgabe (#615): im Trockenlauf „würde …“."""
    if erg.trocken and erg.art != "bleibt":
        return f"würde {erg.art}"
    return erg.art


def _log_schreiben(erg: Ergebnis) -> bool:
    """Eine JSON-Zeile anhängen; False = nicht schreibbar."""
    art_text = log_art(erg)
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ordner": str(erg.ordner),
        "art": art_text,
        "grund": erg.grund,
        "ticket": erg.ticket,
        "sicherung": erg.sicherung,
        "trocken": erg.trocken,
        "fuer_mensch": erg.fuer_mensch,
        "fuer_david": erg.fuer_mensch,  # Übergangs-Alias (#325)
        "fehler": erg.fehler,
    }
    log.info("Hausmeister: %s — %s (%s)", erg.ordner, art_text, erg.grund)
    return _log_zeile(zeile)


def _log_zeile(zeile: dict[str, object], datei: Path | None = None) -> bool:
    """Eine JSON-Zeile ins Hausmeister-Log (oder ``datei``); False = nicht schreibbar."""
    try:
        datei = datei or _log_pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except (OSError, config.KeinHeimOrdner) as fehler:
        log.warning("Hausmeister-Log nicht schreibbar: %s", fehler)
        return False
    return True
