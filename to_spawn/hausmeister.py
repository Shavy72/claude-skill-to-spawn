"""Hausmeister (#1368): räumt alte Arbeitsordner (Worktrees, Klone) sicher weg.

Eine Tür: :func:`aufraeumen` entscheidet für EINEN Ordner nach allen Lösch-Regeln
und löscht nur mit ``loeschen=True``. Alle Regeln leben hier — Aufrufer (CLI, Cron)
kennen keine davon. Grundsatz: im Zweifel bleibt der Ordner. Die Tür wirft nie;
jeder Fehler ergibt ``art="bleibt"`` mit dem Fehler als Grund und ``fehler=True``
(„bleibt“ aus einer Regel hat ``fehler=False``; die CLI endet nur bei Fehlern ≠ 0).

Reihenfolge (erste zutreffende Regel gewinnt), Vertrag ``docs/verify-hard/1368/API.md``:
Aufräum-Bereich (aufgelöster Pfad, Symlink bleibt) → Hauptrepo → eigenes Repo →
Ticket-Nummer → Ticket zu → 24 h → kein Prozess im Ordner (cwd, exe, offene
Dateien) → Prüfung des Ungesicherten → Sicherung → Gegenprobe → Log → löschen.

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
  Geheimnisse in ungepushten Commits würde die Sicherung hochladen ⇒ bleibt, David.
- Ignorierte Dateien: nur bekannte Erzeugnisse (:data:`ERZEUGNISSE`) dürfen weg,
  jede andere ignorierte Datei ⇒ bleibt.
- Eingebettete Repos und Submodule (Sicherung sähe nur den Gitlink) ⇒ bleibt.

Prozesse kommen aus ``/proc``; unter Windows (Laptop, ``C:/dev/wt-*``, #1373) aus
``Get-CimInstance Win32_Process`` plus einer Umbenennungs-Probe nur im echten Lauf
(Windows-Wissen in :mod:`to_spawn.hausmeister_windows`, Tür bleibt hier). Plattenplatz
misst die Platte der Wurzeln, nicht Home. Altbestand (#1373, E16): Ordner in
:func:`altbestand_pfad` bleiben im echten Lauf, bis David die Liste freigibt; der
Trockenlauf zeigt die echte Regel. Fehlt die Liste unter Windows, hält der erste echte
Lauf zuerst alle vorhandenen Ordner fest (am Server: keine Wirkung).

Jede Entscheidung schreibt eine JSON-Zeile nach ``TO_SPAWN_HAUSMEISTER_LOG``
(Standard ``~/.claude/to-spawn/hausmeister.jsonl``), beim Löschen VOR dem Löschen;
Log nicht schreibbar ⇒ bleibt. Scheitert danach das Löschen, folgt eine zweite Zeile
„bleibt“. :func:`liste_fuer_david` liest daraus die Ordner, über die David selbst
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
für David, ohne etwas zu ändern.

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
import sys
import time
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

try:
    import fcntl
except ImportError:  # Windows: Sperre über hausmeister_windows (#1373)
    fcntl = None  # type: ignore[assignment]

from to_spawn import aufpasser, docker_reste, gh, hausmeister_windows, hindernis

log = logging.getLogger("to_spawn.hausmeister")

Art = Literal["gelöscht", "gesichert und gelöscht", "bleibt"]

#: Issues (Tickets) liegen immer hier, auch für Skill-Repo-Ordner.
TICKET_REPO = "Shavy72/duoplus-management"
EIGENE_REPOS = ("shavy72/duoplus-management", "shavy72/claude-skill-to-spawn")
#: Namen der Haupt-Arbeitskopien — nie aufräumen.
HAUPTREPO_NAMEN = ("duoplus-management", "claude-skill-to-spawn")
WURZELN_STANDARD = ("~/wt/*", "~/wt-*", "~/duoplus-[0-9]*", "C:/dev/wt-*")
MINDEST_ZU_SEKUNDEN = 24 * 3600
#: Unter diesem Anteil freier Platte gilt Platznot: fertige Ordner gehen vor Ablauf der 24 h.
PLATZNOT_ANTEIL = 0.15
#: #1491: Zweige löscht der Hausmeister nur, wenn diese Variable genau „1“ ist (:func:`zweige_loeschen_an`).
ZWEIGE_SCHALTER = "TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN"
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
)

_NAME_MUSTER = (
    re.compile(r"^wt-(\d+)(?:-.*)?$"),
    re.compile(r"^skill-(\d+)$"),
    re.compile(r"^duoplus-(\d+)$"),
    re.compile(r"^t(\d+)$"),
)
_ZWEIG_MUSTER = re.compile(r"^(?:ticket|fix|wip)/(\d+)(?:-.*)?$")
_GITHUB_SLUG = re.compile(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


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

    def __init__(
        self, grund: str, *, fuer_david: bool = False, fehler: bool = False
    ) -> None:
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


def aufraeumen(
    ordner: Path, *, loeschen: bool = False, jetzt: float | None = None
) -> Ergebnis:
    """Entscheidet (und löscht mit ``loeschen=True``) einen Arbeitsordner; wirft nie."""
    return _aufraeumen(ordner, loeschen=loeschen, jetzt=jetzt, platznot=False)


def aufraeumen_alles(
    *, loeschen: bool = False, jetzt: float | None = None
) -> list[Ergebnis]:
    """Alle Ordner unter den Wurzeln nach den Regeln von :func:`aufraeumen`; wirft nie.

    Bei Platznot zuerst die Ordner mit der ältesten Ticket-Schließung, ohne 24 h
    Wartezeit, bis wieder genug frei ist (siehe Modul-Kommentar). Läuft schon ein
    anderer Lauf (Sperre), oder scheitert die Ordnersuche: ``[]`` — wer das
    unterscheiden muss, nimmt :func:`rundgang`.
    Mit ``loeschen=True`` gibt derselbe Lauf einmal täglich Docker-Reste frei
    (:mod:`to_spawn.docker_reste`, #1372); Probelauf und Start-Check nie.
    """
    return list(
        _rundgang_sicher(loeschen=loeschen, jetzt=jetzt, docker=loeschen).ergebnisse
    )


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
        return Rundgang(
            False, f"unerwarteter Fehler ({type(fehler).__name__}: {fehler})"
        )


def _rundgang(*, loeschen: bool, jetzt: float | None, docker: bool) -> Rundgang:
    try:
        sperre = _sperre_nehmen()
    except OSError as fehler:
        log.warning("Hausmeister: Sperre nicht nutzbar (%s) — laufe ohne", fehler)
        sperre = None
    if sperre is False:
        log.info("Hausmeister: anderer Lauf läuft schon — dieser räumt nicht auf")
        return Rundgang(
            False, "ein anderer Lauf räumt gerade auf (Sperre belegt)", wartet=True
        )
    try:
        ergebnis = _alle_ordner(loeschen=loeschen, jetzt=jetzt)
        if docker and sperre:  # nur unter gehaltener Sperre: nie zwei Docker-Läufe
            docker_reste.freigeben(_log_pfad().parent, jetzt=jetzt)
        return ergebnis
    finally:
        if sperre:
            sperre.close()  # gibt die flock-Sperre frei


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
        return Rundgang(
            False, f"Ordnersuche gescheitert ({type(fehler).__name__}: {fehler})"
        )
    ergebnisse: list[Ergebnis] = []
    for ordner in kandidaten:
        erg = _aufraeumen(
            ordner, loeschen=loeschen, jetzt=jetzt, platznot=_ist_platznot(anteil)
        )
        ergebnisse.append(erg)
        if erg.art != "bleibt" and not erg.trocken:
            anteil = _frei_anteil()
    _zweige_im_takt(loeschen, jetzt)
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
        log.info("Hausmeister: Zweig-Löschen aus (%s=1 fehlt) — alle Zweige bleiben", ZWEIGE_SCHALTER)
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


def _sperre_nehmen() -> IO[str] | None | bool:
    """Sperrdatei (nicht blockierend): offene Datei = Sperre gehalten, ``False`` =
    anderer Lauf hält sie, ``None`` = Plattform ohne Sperre. Windows: msvcrt-Sperre
    auf derselben Datei (:func:`hausmeister_windows.sperre_nehmen`)."""
    datei = _log_pfad().parent / SPERR_DATEI
    if _ist_windows() or fcntl is None:
        return hausmeister_windows.sperre_nehmen(datei)
    datei.parent.mkdir(parents=True, exist_ok=True)
    f = datei.open("a", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return False
    except OSError:
        f.close()
        raise
    return f


def _aufraeumen(
    ordner: Path, *, loeschen: bool, jetzt: float | None, platznot: bool
) -> Ergebnis:
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
        erg = replace(
            erg, art="bleibt", grund=f"Löschen gescheitert: {_kurz(fehler)}", fehler=True
        )
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
_SICHERUNG_REMOTES = tuple(
    f"https://github.com/{slug}"
    for slug in ("Shavy72/duoplus-management", "Shavy72/claude-skill-to-spawn")
)
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


def sicherungen_aufraeumen(
    *, loeschen: bool = False, jetzt: float | None = None
) -> list[ZweigErgebnis]:
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
        return list(_SICHERUNG_REMOTES)
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
        log.warning(
            "Hausmeister: Takt der Sicherungs-Zweige nicht schreibbar: %s", fehler
        )


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


def _sicherungen_eines_remotes(
    remote: str, loeschen: bool, jetzt: float
) -> list[ZweigErgebnis]:
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
    roh = aufpasser.git_lauf(
        cache, "for-each-ref", "--format=%(refname) %(objectname)", "refs/sicherung/"
    )
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


def _sicherung_entscheiden(
    cache: Path, remote: str, nr: str, sha: str, loeschen: bool, jetzt: float
) -> ZweigErgebnis:
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
        f"vor {tage:.0f} Tagen angelegt, zuletzt vor {zuletzt:.0f} Tagen gesichert "
        f"(Grenze {SICHERUNG_TAGE} Tage)",
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
        erg = replace(
            erg, art="bleibt", grund=f"Löschen gescheitert: {_kurz(fehler)}", fehler=True
        )
        _zweig_log(erg)
    return erg


def _zweig_loeschen(cache: Path, zweig: str, sha: str) -> None:
    """Löscht ``zweig`` auf dem Remote nur, wenn er noch auf ``sha`` steht (Lease)."""
    ref = f"refs/heads/{zweig}"
    _git_ohne_frage(
        cache, "push", "-q", f"--force-with-lease={ref}:{sha}", "origin", f":{ref}"
    )


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
    daten = _ticket_daten(nr)
    if not isinstance(daten, dict) or "state" not in daten:
        raise RuntimeError(f"Ticket #{nr} nicht abfragbar")
    if str(daten["state"]).upper() != "CLOSED":
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
        return ergebnis("bleibt", "keine Ticket-Nummer im Zweig — David entscheidet", fuer_david=True)
    o = _zweig_ort(repo, ort)
    quelle = o.remote
    if ort == "lokal" and (wo := _ausgecheckt_in(o.cwd, zweig)):
        return ergebnis("bleibt", f"in Arbeitsordner {wo} ausgecheckt")
    # Ab hier nur noch der geprüfte Stand: Sicherung, Gegenprobe und Lease beziehen sich
    # auf ``sha``; steht der Zweig beim Löschen anders, bleibt er (_Nachgeschoben).
    fertig, fehlende, grund, sha = zweig_fertig(repo, zweig, uebernommen_tip=tip, ort=ort, holen=holen)
    if not fertig or not sha:
        return ergebnis("bleibt", grund, sha)
    art = "gesichert+gelöscht" if fehlende else "gelöscht"
    if fehlende and ort == "lokal":  # Remote-Commits liegen schon auf GitHub, lokale nicht
        leck = aufpasser.git_lauf(
            o.cwd, "log", "--format=", "--name-only", sha, "--not", o.haupt_ref,
            f"--glob={o.sicherungen}", "--", *_glob(aufpasser.SICHERUNG_AUSSCHLUSS),
        ).split()  # fmt: skip
        if leck:
            return ergebnis("bleibt", f"Geheimnis-Datei {leck[0]} in ungesichertem Commit", sha, fuer_david=True)
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
    # ponytail: nur ~/<Name> (Bau-Server); Laptop-Pfade per Naht, falls dort nötig.
    return [Path.home() / n for n in HAUPTREPO_NAMEN if (Path.home() / n / ".git").is_dir()]


def _zweig_ticket(zweig: str) -> int | None:
    treffer = _ZWEIG_MUSTER.match(zweig)
    return int(treffer.group(1)) if treffer and zweig.startswith("ticket/") else None


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


def _ausgecheckte(repo: Path) -> dict[str, str]:
    """Zweig → Arbeitsordner aller in ``repo`` ausgecheckten Zweige (auch das Hauptrepo)."""
    belegt: dict[str, str] = {}
    ordner = ""
    for zeile in aufpasser.git_lauf(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            ordner = zeile[len("worktree ") :]
        elif zeile.startswith("branch refs/heads/"):
            belegt[zeile[len("branch refs/heads/") :]] = ordner
    return belegt


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
    if not trocken:  # Trockenlauf zeigt die echte Regel (Altbestand-Liste für David)
        _kein_altbestand(echt)

    # 2/3 Git-Ordner? Hauptrepo? Eigenes Repo?
    ist_worktree = _git_ordner_pruefen(echt)

    # 4 Ticket-Nummer aus Name und Zweig
    stand.ticket = ticket = _ticket_nummer(ordner, echt)

    # 5/6 Ticket zu, seit mindestens 24 h
    _ticket_lange_zu(ticket, jetzt, platznot)

    # 7 Prozess im Ordner
    _kein_prozess(echt)

    # 8 Ungesichertes prüfen (auch trocken: fetch, Geheimnisse, Ignoriertes)
    koepfe, zu_sichern, status_vorher = _ungesichertes(echt, ist_worktree)
    art: Art = "gesichert und gelöscht" if (koepfe or zu_sichern) else "gelöscht"
    if trocken:
        return Ergebnis(
            ordner, art, _grund(art, ticket, platznot), ticket, trocken=True
        ), None
    if koepfe or zu_sichern:
        try:
            stand.sicherung = aufpasser.sichern(
                echt, ticket, jetzt, zusatz_koepfe=koepfe
            )
        except RuntimeError as fehler:
            raise _Bleibt(f"Sicherung gescheitert: {_kurz(fehler)}", fehler=True) from None
        if stand.sicherung is None:
            raise _Bleibt(
                "Sicherung lieferte nichts, obwohl Ungesichertes erkannt wurde",
                fehler=True,
            )

    # Gegenprobe direkt vor dem Löschen: nichts Neues, alles auf origin, kein Prozess
    _kein_prozess(echt, probe=True)
    if _status(echt) != status_vorher:
        raise _Bleibt("Ordner hat sich während der Sicherung geändert")
    if _ungepushte_koepfe(echt, ist_worktree):
        raise _Bleibt("nach der Sicherung noch Commits auf keinem Remote", fehler=True)

    # 9 Löschen (erst nach der Log-Zeile, siehe aufraeumen)
    erg = Ergebnis(
        ordner,
        art,
        _grund(art, ticket, platznot),
        ticket,
        stand.sicherung,
        trocken=False,
    )
    return erg, (echt, ist_worktree)


def _git_ordner_pruefen(echt: Path) -> bool:
    """Regeln 2/3; Rückgabe: verknüpfter Worktree (True) oder Klon (False)."""
    if _git_oder_none(echt, "rev-parse", "--is-inside-git-dir") == "true":
        raise _Bleibt("Git-Verwaltungsordner (Hauptrepo)")
    oben = _git_oder_none(echt, "rev-parse", "--show-toplevel")
    if oben is None or Path(oben).resolve() != echt:
        raise _Bleibt("kein eigener Git-Ordner", fuer_david=True)
    ist_worktree = (echt / ".git").is_file()
    if not ist_worktree:
        if echt.name in HAUPTREPO_NAMEN:
            raise _Bleibt("Hauptrepo")
        if _hat_verknuepfte_worktrees(echt):
            raise _Bleibt("Hauptrepo mit verknüpften Worktrees")
    url = _git_oder_none(echt, "config", "--get", "remote.origin.url") or ""
    if _slug(url).lower() not in EIGENE_REPOS:
        raise _Bleibt(f"fremdes Repo ({url or 'ohne origin'})", fuer_david=True)
    return ist_worktree


def _ticket_nummer(ordner: Path, echt: Path) -> int:
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    aus_name = _nummer(ordner.name, _NAME_MUSTER)
    aus_zweig = _nummer(zweig, (_ZWEIG_MUSTER,))
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        raise _Bleibt(
            f"Ordnername (#{aus_name}) und Zweig {zweig} (#{aus_zweig}) widersprechen sich",
            fuer_david=True,
        )
    ticket = aus_name if aus_name is not None else aus_zweig
    if ticket is None:
        raise _Bleibt("keine Ticket-Nummer in Ordnername oder Zweig", fuer_david=True)
    return ticket


def _ticket_lange_zu(ticket: int, jetzt: float | None, platznot: bool = False) -> None:
    daten = _ticket_daten(ticket)
    if not isinstance(daten, dict) or "state" not in daten:
        raise _Bleibt(f"Ticket #{ticket} nicht abfragbar", fehler=True)
    if str(daten["state"]).upper() != "CLOSED":
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
    zu = (
        f"Ticket #{ticket} zu, Platznot (unter {_prozent(PLATZNOT_ANTEIL)} frei)"
        if platznot
        else f"Ticket #{ticket} seit über 24 h zu"
    )
    if art == "gelöscht":
        return f"{zu}, nichts Ungesichertes"
    return f"{zu}, Ungesichertes auf sicherung/{ticket}"


# --- Platte ---------------------------------------------------------------------


def _kandidaten() -> list[Path]:
    """Alle Ordner, die auf ein Wurzel-Muster passen (ohne Doppelte), sortiert."""
    gefunden: dict[Path, None] = {}
    for muster in _wurzeln():
        for treffer in sorted(glob.glob(muster)):
            pfad = Path(os.path.abspath(treffer))
            if pfad.is_dir():
                gefunden.setdefault(pfad, None)
    return list(gefunden)


def _schliesszeit(ordner: Path) -> float | None:
    """Schließzeit des Tickets zum Ordner (für die Reihenfolge), ``None`` = offen/unbekannt."""
    try:
        ticket = _ticket_nummer(ordner, ordner.resolve())
        daten = _ticket_daten(ticket)
    except (
        _Bleibt,
        OSError,
        RuntimeError,
    ):  # nur Reihenfolge — die Regeln entscheiden danach selbst
        return None
    if not isinstance(daten, dict) or str(daten.get("state", "")).upper() != "CLOSED":
        return None
    return _zeitpunkt(daten.get("closedAt"))


def _ticket_daten(ticket: int) -> object:
    """``gh issue view`` (state, closedAt) mit Zeitlimit; ``None`` = nicht abfragbar."""
    return gh.json_lauf(
        ["issue", "view", str(ticket), "-R", TICKET_REPO, "--json", "state,closedAt"],
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
            log.warning(
                "Platte nicht messbar: TO_SPAWN_HAUSMEISTER_PLATTE=%r ungültig", roh
            )
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
        werte = [
            z.strip()
            for z in datei.read_text(encoding="utf-8").splitlines()
            if z.strip()
        ]
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
    muster = roh.split(os.pathsep) if roh else list(WURZELN_STANDARD)
    return [os.path.expanduser(m) for m in muster if m.strip()]


def _im_bereich(ordner: Path) -> bool:
    try:
        echt = ordner.resolve()
    except OSError:
        return False
    return any(echt.match(m) for m in _wurzeln())


def _nummer(text: str, muster: tuple[re.Pattern[str], ...]) -> int | None:
    for m in muster:
        treffer = m.match(text)
        if treffer:
            return int(treffer.group(1))
    return None


def _slug(url: str) -> str:
    treffer = _GITHUB_SLUG.search(url.strip())
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
    for zeile in aufpasser.git_lauf(
        repo, "worktree", "list", "--porcelain"
    ).splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :])
            if pfad.is_dir() and pfad.resolve() != repo:
                return True
    return False


def _hauptrepo(worktree: Path) -> Path:
    gemeinsam = Path(
        aufpasser.git_lauf(worktree, "rev-parse", "--git-common-dir").strip()
    )
    if not gemeinsam.is_absolute():
        gemeinsam = (worktree / gemeinsam).resolve()
    return gemeinsam.parent


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
        return (
            hier.is_file() and dort.is_file() and hier.read_bytes() == dort.read_bytes()
        )
    except OSError:
        return False


def _ungesichertes(
    repo: Path, ist_worktree: bool
) -> tuple[list[str], bool, list[tuple[str, str]]]:
    """Regel 8: (ungepushte Köpfe, zu sichernde Dateien da?, Status-Schnappschuss).

    Alles, was die Sicherung nicht retten kann, wirft ``_Bleibt``.
    """
    try:
        aufpasser.git_lauf(repo, "fetch", "--prune", "-q", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"git fetch origin gescheitert: {_kurz(fehler)}", fehler=True) from None
    if not ist_worktree and _git_oder_none(
        repo, "rev-parse", "--verify", "-q", "refs/stash"
    ):
        raise _Bleibt("Stash vorhanden (git stash list)", fuer_david=True)
    for zeile in aufpasser.git_lauf(repo, "ls-files", "-s").splitlines():
        if zeile.startswith("160000 "):
            raise _Bleibt(f"Submodul {zeile.split(chr(9), 1)[-1]}", fuer_david=True)

    alle = _status(repo)
    for _, pfad in alle:
        if pfad.endswith("/"):
            raise _Bleibt(f"eingebettetes Repo {pfad}", fuer_david=True)
    geheim = {p for _, p in _status(repo, *_glob(aufpasser.SICHERUNG_AUSSCHLUSS))}
    haupt = _hauptrepo(repo) if ist_worktree else None
    for pfad in sorted(geheim):
        if not _gleiche_kopie(haupt, repo, pfad):
            raise _Bleibt(
                f"Geheimnis-Datei {pfad} nicht gleich im Hauptrepo", fuer_david=True
            )
    erzeugnis = {p for xy, p in _status(repo, *_glob(ERZEUGNISSE)) if xy == "!!"}
    for xy, pfad in alle:
        if xy == "!!" and pfad not in geheim and pfad not in erzeugnis:
            raise _Bleibt(f"ignorierte Datei {pfad} ist kein bekanntes Erzeugnis")
    zu_sichern = any(xy != "!!" and p not in geheim for xy, p in alle)

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
            raise _Bleibt(
                f"Geheimnis-Datei {leck[0]} in ungepushtem Commit", fuer_david=True
            )
    return koepfe, zu_sichern, alle


def _ungepushte_koepfe(repo: Path, ist_worktree: bool) -> list[str]:
    """Refs mit Commits nicht auf origin (Klon: alle außer refs/remotes + HEAD; Worktree: HEAD)."""
    refs = ["HEAD"]
    if not ist_worktree:
        refs += [
            r
            for r in aufpasser.git_lauf(
                repo, "for-each-ref", "--format=%(refname)"
            ).split()
            if not r.startswith("refs/remotes/")
        ]
    return [
        ref
        for ref in refs
        if aufpasser.git_lauf(
            repo, "rev-list", "-n", "1", ref, "--not", "--remotes=origin"
        ).strip()
    ]


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
    ticket = _nummer(worktree.name, _NAME_MUSTER)
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
    """Hält Ordner als Altbestand fest: sie bleiben im echten Lauf, bis David die Liste
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
            "Hausmeister: Altbestand-Liste %s ist schon freigegeben — %d Ordner nicht "
            "ergänzt",
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
    zwischen.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(zwischen, pfad)  # nie eine halb geschriebene Liste
    return pfad


def _kein_altbestand(echt: Path) -> None:
    """Altbestand-Regel (nur echter Lauf): gelistet und nicht freigegeben ⇒ bleibt;
    Datei kaputt/unlesbar ⇒ jeder Ordner bleibt mit Fehler. Datei fehlt: am Server keine
    Wirkung; unter Windows hält der Lauf zuerst alle vorhandenen Ordner fest — der
    erste Lauf am Laptop löscht nie ohne Davids Ja."""
    pfad = altbestand_pfad()
    if _ist_windows() and not os.path.lexists(pfad):
        try:
            altbestand_festhalten([*_kandidaten(), echt])
        except OSError as fehler:
            raise _Bleibt(
                f"Altbestand-Liste {pfad} nicht anlegbar: {fehler}", fehler=True
            ) from None
        log.warning(
            "Hausmeister: Altbestand-Liste fehlte — alle Ordner festgehalten in %s",
            pfad,
        )
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):  # Datei gibt es nicht (Server)
        return
    except (OSError, ValueError) as fehler:
        raise _Bleibt(
            f"Altbestand-Liste {pfad} unlesbar: {fehler}", fehler=True
        ) from None
    liste = daten.get("ordner") if isinstance(daten, dict) else None
    if not isinstance(liste, list) or not all(isinstance(o, str) for o in liste):
        raise _Bleibt(f"Altbestand-Liste {pfad} kaputt", fehler=True)
    if daten.get("freigegeben") is True:
        return
    schluessel = os.path.normcase(str(echt))
    if any(os.path.normcase(str(Path(o))) == schluessel for o in liste):
        raise _Bleibt(f"Altbestand, wartet auf Davids Ja (Liste {pfad})")


def _log_pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_LOG")
    return (
        Path(roh).expanduser()
        if roh
        else Path.home() / ".claude" / "to-spawn" / "hausmeister.jsonl"
    )


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


def _log_zeile(zeile: dict[str, object]) -> bool:
    """Eine JSON-Zeile ins Hausmeister-Log; False = nicht schreibbar."""
    try:
        datei = _log_pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Hausmeister-Log nicht schreibbar: %s", fehler)
        return False
    return True
