"""Übergaben und Entscheidungs-Log von selbst sichern, angefangene Arbeit hochladen (L5, #145).

Eine Tür: :func:`sichern`. Sie kopiert Übergabe-Dateien und Entscheidungs-Logs dieser Maschine
in den privaten Sicherungs-Ordner und lädt angefangene Zweige der Werkzeug-Repos (Vorgabe: das
Skill-Repo) hoch. Aufrufer: die Aufseher-Ablösung (:mod:`to_spawn.respawn_aufseher`), jeder
Hausmeister-Rundgang (Netz für vergessene Ablösungen) und ``to_spawn.py sichern`` aus der
Ablösungs-Regel der Leit-Session und der Ober-Aufsicht (``docs/WIEDEREINSTIEG.md``). Wirft nie.

Alles Wissen dazu lebt nur hier (Maschinen-Liste ``~/.config/to-spawn/maschine.json``, Gruppe
``sicherung``):

- ``ziel``: Sicherungs-Ordner. Leer = Notiz-Tresor (``$OBSIDIAN_VAULT``, sonst ``~/obsidian``),
  falls vorhanden, Unterordner ``to-spawn/sicherung``; sonst ``~/.local/share/to-spawn/sicherung``.
  Je Maschine ein Unterordner (Rechnername), damit zwei Maschinen sich nie überschreiben.
- ``dateien``: weitere Muster (``~`` und ``*`` erlaubt). Immer dabei: je Repo aus ``repos[]``
  ``.claude-handoff*.md`` und ``entscheidungen.md``.
- ``zweige_slugs``: Repos, deren angefangene Zweige hochgeladen werden (Vorgabe: das Skill-Repo).

Öffentlich-Sperre (hart): liegt das Ziel im Skill-Repo oder in einem Klon davon (öffentlich),
wird nichts kopiert. Rohe Übergaben gehören nie in ein öffentliches Repo: ein Zweig, dessen
noch nicht hochgeladene Commits einen Pfad nach :data:`MUSTER_JE_REPO` enthalten
(:func:`nie_oeffentlich`), wird laut verweigert (#169). ``.gitignore`` führt dieselben Muster.

Probelauf (``trocken=True``, #169): schreibt und lädt nichts, meldet nur, was geschähe.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, konsole, prozessbaum, skill_stand

log = logging.getLogger("to_spawn.uebergabe_sichern")

#: Öffentliche Repos, in die nie gesichert wird (und deren Zweige hochgeladen werden).
OEFFENTLICHE_SLUGS: tuple[str, ...] = config.OEFFENTLICHE_SLUGS
#: Muster je Repo aus ``repos[]`` (relativ zum Repo-Ordner). Zugleich die einzige Liste der
#: Dateinamen, die nie öffentlich werden (:func:`nie_oeffentlich`, ``.gitignore``).
MUSTER_JE_REPO: tuple[str, ...] = (".claude-handoff*.md", "entscheidungen.md")
TRESOR_UNTERORDNER = Path("to-spawn") / "sicherung"
#: Zweige, die nie hochgeladen werden: Hauptzweige; ``ticket/*`` mergt die Deploy-Schlange sofort.
GESCHUETZTE_ZWEIGE: tuple[str, ...] = ("main", "master", "HEAD")
GESCHUETZTE_PRAEFIXE: tuple[str, ...] = ("ticket/",)
GIT_MAX_S = 60.0
#: Test- und Notfall-Türen (Vorrang vor der Maschinen-Liste).
UMGEBUNG_ZIEL = "TO_SPAWN_SICHERUNG_ZIEL"
#: Leerzeichen- oder ``os.pathsep``-getrennte Repo-Ordner für den Zweig-Upload; leer = keiner.
UMGEBUNG_ZWEIG_REPOS = "TO_SPAWN_SICHERUNG_ZWEIG_REPOS"
#: Zeitzone der Stand-Einträge in ``docs/WIEDEREINSTIEG.md`` (L5, #145).
STAND_ZEITZONE = "Europe/Berlin"
#: Frist zwischen jüngstem ``main``-Stand und jüngstem Stand-Eintrag in ``docs/WIEDEREINSTIEG.md``.
STAND_FRIST = timedelta(hours=24)
STAND_ZEILE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", re.MULTILINE)
#: Zitierter ``main``-Commit im neuesten Stand-Eintrag (z. B. ``(Europe/Berlin, main 7005f9c)``, #353).
STAND_REFERENZ_MUSTER = re.compile(
    r"^- \*\*\d{4}-\d{2}-\d{2} \d{2}:\d{2}\b[^\n]*?\bmain\s+([0-9a-f]{7,40})\b",
    re.MULTILINE,
)


@dataclass
class Sicherung:
    """Ergebnis: ``kopiert`` = neu geschriebene Kopien, ``hochgeladen`` = ``repo:zweig``.

    Mit ``trocken`` stehen dort, was geschrieben bzw. hochgeladen würde.
    """

    ziel: Path | None
    kopiert: list[Path] = field(default_factory=list)
    hochgeladen: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    trocken: bool = False

    def zeile(self) -> str:
        kopf = "Sicherung (Probelauf): " if self.trocken else "Sicherung: "
        if self.ziel is None:
            return kopf + "kein erlaubtes Ziel — " + "; ".join(self.fehler)
        if self.trocken:
            teile = [
                f"würde {len(self.kopiert)} Datei(en) nach {self.ziel} schreiben",
                f"würde {len(self.hochgeladen)} Zweig(e) hochladen",
            ]
        else:
            teile = [
                f"{len(self.kopiert)} Datei(en) nach {self.ziel}",
                f"{len(self.hochgeladen)} Zweig(e) hochgeladen",
            ]
        if self.fehler:
            teile.append(f"{len(self.fehler)} Fehler: " + "; ".join(self.fehler))
        return kopf + ", ".join(teile)


@dataclass
class SicherungPruefung:
    """Ergebnis von :func:`pruefen` — rein lesende Prüfung von ``sicherung.*`` (#755)."""

    maschine_datei: Path
    maschine_vorhanden: bool
    ziel: Path
    ziel_herkunft: str
    ziel_oeffentlich: bool
    ziel_vorhanden: bool
    muster: list[str] = field(default_factory=list)
    trefferlose_muster: list[str] = field(default_factory=list)
    quellen: list[Path] = field(default_factory=list)
    zweig_repos: list[Path] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fehler

    def bericht(self) -> str:
        kopf = "Sicherung-Prüfung: OK" if self.ok else f"Sicherung-Prüfung: LÜCKE ({len(self.fehler)} Fehler)"
        zeilen = [
            kopf,
            f"  Maschinen-Liste: {self.maschine_datei} ({'vorhanden' if self.maschine_vorhanden else 'fehlt'})",
            (
                f"  Ziel: {self.ziel} ({self.ziel_herkunft}, "
                f"{'ÖFFENTLICH' if self.ziel_oeffentlich else 'privat'}, "
                f"{'vorhanden' if self.ziel_vorhanden else 'wird beim ersten Lauf angelegt'})"
            ),
            (
                f"  sicherung.dateien: {len(self.muster)} Muster ({len(self.trefferlose_muster)} ohne Treffer)"
                if self.muster
                else "  sicherung.dateien: nicht eingetragen"
            ),
            f"  Quellen gefunden: {len(self.quellen)} Datei(en)",
            (
                f"  Zweig-Repos: {len(self.zweig_repos)} ({', '.join(p.name for p in self.zweig_repos)})"
                if self.zweig_repos
                else "  Zweig-Repos: 0"
            ),
        ]
        for eintrag in self.fehler:
            zeilen.append(f"  FEHLER: {eintrag}")
        for eintrag in self.hinweise:
            zeilen.append(f"  Hinweis: {eintrag}")
        return "\n".join(zeilen)


def _gruppe() -> dict[str, object]:
    roh = config.maschine().get("sicherung")
    return roh if isinstance(roh, dict) else {}


def _skill_wurzel() -> Path:
    return skill_stand.SKILL.resolve()


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,  # git schreibt Pfade in UTF-8, auch unter Windows
        **prozessbaum.fensterlos(),
        timeout=GIT_MAX_S,
        check=False,
    )


def _origin(ordner: Path) -> str:
    try:
        return _git(ordner, "config", "--get", "remote.origin.url").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def ist_oeffentlich(ziel: Path) -> bool:
    """``ziel`` liegt im Skill-Repo oder in einem Git-Ordner mit öffentlichem ``origin``."""
    ziel = ziel.expanduser().resolve()
    if ziel == _skill_wurzel() or _skill_wurzel() in ziel.parents:
        return True
    for ordner in (ziel, *ziel.parents):
        if (ordner / ".git").exists():
            url = _origin(ordner).lower()
            return any(slug.lower().split("/")[1] in url for slug in OEFFENTLICHE_SLUGS)
    return False


def _ziel_mit_herkunft() -> tuple[Path, str]:
    """Bestimmt Sicherungs-Ordner und Herkunft (``umgebung``/``maschine.json``/``notiz-tresor``/``vorgabe``)."""
    umg = os.environ.get(UMGEBUNG_ZIEL, "").strip()
    if umg:
        basis, herkunft = config.expand_heim(umg), "umgebung"
    else:
        ziel_wert = _gruppe().get("ziel")
        konfig_ziel = ziel_wert.strip() if isinstance(ziel_wert, str) else ""
        if konfig_ziel:
            basis, herkunft = config.expand_heim(konfig_ziel), "maschine.json"
        else:
            tresor = config.notiz_tresor()
            if tresor.is_dir():
                basis, herkunft = tresor / TRESOR_UNTERORDNER, "notiz-tresor"
            else:
                basis, herkunft = config.heim_ordner() / ".local" / "share" / "to-spawn" / "sicherung", "vorgabe"
    return basis / (socket.gethostname().lower() or "maschine"), herkunft


def ziel_ordner() -> Path:
    """Sicherungs-Ordner dieser Maschine (ohne Öffentlich-Prüfung, siehe :func:`sichern`)."""
    return _ziel_mit_herkunft()[0]


def _muster_treffer(muster: str) -> list[Path]:
    """Löst ein Dateimuster (``~`` und ``*`` erlaubt) zu vorhandenen Dateien auf."""
    erweitert = str(config.expand_heim(muster))
    return [pfad for treffer in glob.glob(erweitert) if (pfad := Path(treffer)).is_file()]


def _quellen(extra: list[Path]) -> list[Path]:
    skill = _skill_wurzel()
    repos = config.maschine()["repos"]
    repo_pfade = [pfad for e in repos if (pfad := config.repo_klon_pfad(e, skill=skill)) is not None]
    if not any(config.ist_skill_slug(e["slug"]) for e in repos):
        stempel_klon = skill_stand.klon_ordner(skill)
        if stempel_klon is not None:
            repo_pfade.append(stempel_klon)
    muster = [str(pfad / m) for pfad in dict.fromkeys(repo_pfade) for m in MUSTER_JE_REPO]
    roh = _gruppe().get("dateien")
    muster += [m.strip() for m in roh if isinstance(m, str) and m.strip()] if isinstance(roh, list) else []
    gefunden = [pfad for m in muster for pfad in _muster_treffer(m)]
    eindeutig: dict[Path, Path] = {}
    for pfad in [*(config.expand_heim(p) for p in extra), *gefunden]:
        if pfad.is_file():
            eindeutig.setdefault(pfad.resolve(), pfad)
    return list(eindeutig.values())


def kopie_name(quelle: Path) -> str:
    """``<Elternordner>__<Name>`` ohne führenden Punkt (sonst im Tresor unsichtbar)."""
    return f"{quelle.resolve().parent.name}__{quelle.name.lstrip('.')}"


def _kopieren(quelle: Path, ziel: Path, *, trocken: bool) -> bool:
    """Kopiert atomar, nur wenn der Inhalt sich geändert hat; ``True`` = geschrieben (würde)."""
    daten = quelle.read_bytes()
    datei = ziel / kopie_name(quelle)
    if datei.is_file() and datei.read_bytes() == daten:
        return False
    if trocken:
        return True
    fd, tmp = tempfile.mkstemp(prefix=".sicherung-", dir=ziel)
    try:
        with os.fdopen(fd, "wb") as strom:
            strom.write(daten)
        os.replace(tmp, datei)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def _zweig_repos() -> list[Path]:
    roh = os.environ.get(UMGEBUNG_ZWEIG_REPOS)
    if roh is not None:
        text = roh.strip()
        if not text:
            return []
        if os.pathsep in text:
            teile = [t.strip() for t in text.split(os.pathsep) if t.strip()]
        elif config.expand_heim(text).exists():
            teile = [text]
        else:
            teile = text.split()
        return [config.expand_heim(p) for p in teile]
    slugs = _gruppe().get("zweige_slugs")
    erlaubt = {s.lower() for s in (slugs if isinstance(slugs, list) else OEFFENTLICHE_SLUGS) if isinstance(s, str)}
    skill = _skill_wurzel()
    gefunden: list[Path] = []
    skill_abgedeckt = False
    for eintrag in config.maschine()["repos"]:
        slug = eintrag["slug"].lower()
        if slug not in erlaubt:
            continue
        if config.ist_skill_slug(slug):
            skill_abgedeckt = True
        pfad = config.repo_klon_pfad(eintrag, skill=skill)
        if pfad is not None:
            gefunden.append(pfad)
    if not skill_abgedeckt and any(config.ist_skill_slug(s) for s in erlaubt):
        aus_stempel = skill_stand.klon_ordner(skill)
        if aus_stempel is not None:
            gefunden.append(aus_stempel)
    return list(dict.fromkeys(gefunden))


def _worktree_zweige(repo: Path) -> list[tuple[Path, str]]:
    aus = _git(repo, "worktree", "list", "--porcelain")
    if aus.returncode != 0:
        raise OSError(aus.stderr.strip() or "git worktree list gescheitert")
    paare: list[tuple[Path, str]] = []
    ordner: Path | None = None
    for zeile in aus.stdout.splitlines():
        if zeile.startswith("worktree "):
            ordner = Path(zeile[len("worktree ") :])
        elif zeile.startswith("branch refs/heads/") and ordner is not None:
            paare.append((ordner, zeile[len("branch refs/heads/") :]))
    return paare


def darf_hochladen(zweig: str) -> bool:
    return zweig not in GESCHUETZTE_ZWEIGE and not zweig.startswith(GESCHUETZTE_PRAEFIXE)


def nie_oeffentlich(pfad: str) -> bool:
    """``pfad`` (Repo-relativ, ``/``-getrennt) ist eine Übergabe oder ein Entscheidungs-Log.

    Groß-/Kleinschreibung zählt nicht (``.CLAUDE-HANDOFF.md`` ist auch gesperrt).
    """
    name = PurePosixPath(pfad).name.lower()
    return any(fnmatch(name, muster.lower()) for muster in MUSTER_JE_REPO)


def pfade_aus_nul(ausgabe: str) -> list[str]:
    """Zerlegt NUL-getrennte ``git log -z --name-only --format=``-Ausgabe unverändert (#215).

    Kein ``strip("\\r\\n")``: ein echter Zeilenumbruch am Ende eines Dateinamens bleibt
    erhalten, und Pfade mit Leerzeichen oder Nicht-ASCII-Zeichen werden nicht zerlegt.
    Einzige Zerlegungsstelle für :func:`gesperrte_pfade` und die Geheimnis-Prüfung im
    Hausmeister.
    """
    return [p for p in ausgabe.split("\0") if p]


def gesperrte_pfade(ordner: Path, *bereich: str) -> list[str]:
    """Pfade nach :func:`nie_oeffentlich` in allen Commits von ``bereich`` (rev-list-Argumente).

    Die eine Prüfung vor jedem Push eines Zweigs. Ganze Historie statt Endstand: auch eine
    wieder gelöschte Übergabe würde mitgepusht. ``-z`` hält Nicht-ASCII-Pfade ungequotet,
    ``--diff-merges=separate`` zeigt Dateien, die erst in einer Merge-Auflösung entstehen.
    """
    aus = _git(ordner, "log", "-z", "--name-only", "--diff-merges=separate", "--format=", *bereich)
    if aus.returncode != 0:
        raise OSError(aus.stderr.strip() or f"git log {' '.join(bereich)} gescheitert")
    return sorted({p for p in pfade_aus_nul(aus.stdout) if nie_oeffentlich(p)})


def _gesperrte_pfade(ordner: Path, zweig: str) -> list[str]:
    """:func:`gesperrte_pfade` für Commits von ``zweig``, die ``origin`` noch fehlen."""
    return gesperrte_pfade(ordner, zweig, "--not", "--remotes=origin")


def _hochladen(repo: Path, erg: Sicherung) -> None:
    # ponytail: nur Commits, nicht der lose Arbeitsstand; wip-Schnappschuss (git stash create) wenn nötig.
    for ordner, zweig in _worktree_zweige(repo):
        if not darf_hochladen(zweig):
            continue
        offen = _git(ordner, "rev-list", "--count", f"origin/{zweig}..{zweig}")
        if offen.returncode == 0 and offen.stdout.strip() == "0":
            continue  # nichts Neues
        gesperrt = _gesperrte_pfade(ordner, zweig)
        if gesperrt:
            erg.fehler.append(f"Push {repo.name}:{zweig} verweigert — enthält {', '.join(gesperrt)}")
            log.error(
                "Sicherung: Push %s:%s verweigert, Übergabe/Entscheidungs-Log im Zweig: %s",
                repo.name,
                zweig,
                ", ".join(gesperrt),
            )
            continue
        if erg.trocken:
            erg.hochgeladen.append(f"{repo.name}:{zweig}")
            continue
        aus = _git(ordner, "push", "origin", f"refs/heads/{zweig}:refs/heads/{zweig}")
        if aus.returncode == 0:
            erg.hochgeladen.append(f"{repo.name}:{zweig}")
        else:
            erg.fehler.append(f"Push {repo.name}:{zweig} — {aus.stderr.strip()[:200]}")


def sichern(dateien: list[Path] | None = None, *, trocken: bool = False) -> Sicherung:
    """Sichert ``dateien`` + die bekannten Übergaben/Logs dieser Maschine, lädt Zweige hoch.

    Wirft nie; Fehler stehen in :attr:`Sicherung.fehler` und im Log. Liegt das Ziel in einem
    öffentlichen Repo, wird nichts kopiert (``ziel`` = ``None``), Zweige werden trotzdem
    hochgeladen (die liegen ohnehin im Repo) — außer sie enthalten Übergaben/Logs (#169).
    ``trocken=True`` (Probelauf): nichts schreiben, nichts hochladen, nur melden (#169).
    """
    erg = Sicherung(None, trocken=trocken)
    try:
        ziel = ziel_ordner()
        if ist_oeffentlich(ziel):
            erg.fehler.append(f"{ziel} liegt in einem öffentlichen Repo — nichts kopiert")
            log.error("Sicherung: %s liegt in einem öffentlichen Repo — nichts kopiert", ziel)
        else:
            if not trocken:
                ziel.mkdir(parents=True, exist_ok=True)
            erg.ziel = ziel
            for quelle in _quellen(list(dateien or [])):
                try:
                    if _kopieren(quelle, ziel, trocken=trocken):
                        erg.kopiert.append(quelle)
                except OSError as fehler:
                    erg.fehler.append(f"{quelle.name}: {fehler}")
    except Exception as fehler:  # Vertrag: wirft nie (Aufrufer: Ablösung, Hausmeister)
        log.exception("Sicherung: Dateien gescheitert")
        erg.fehler.append(f"Dateien: {type(fehler).__name__}: {fehler}")
    try:
        for repo in _zweig_repos():
            if not repo.is_dir():
                log.info(
                    "Sicherung: Klon-Ordner %s fehlt — Zweig-Upload übersprungen",
                    repo,
                )
                continue
            try:
                _hochladen(repo, erg)
            except (OSError, subprocess.SubprocessError) as fehler:
                erg.fehler.append(f"Zweige {repo}: {fehler}")
    except Exception as fehler:  # Vertrag: wirft nie
        log.exception("Sicherung: Zweige gescheitert")
        erg.fehler.append(f"Zweige: {type(fehler).__name__}: {fehler}")
    (log.warning if erg.fehler else log.info)("%s", erg.zeile())
    return erg


def _stand_zone() -> tzinfo:
    """``Europe/Berlin``, bei fehlender Zeitzonen-Datenbank Rückfall auf die Rechner-Ortszeit."""
    try:
        return ZoneInfo(STAND_ZEITZONE)
    except ZoneInfoNotFoundError:
        log.warning(
            "Zeitzone %s nicht verfügbar — Rückfall auf Rechner-Ortszeit",
            STAND_ZEITZONE,
        )
        return datetime.now().astimezone().tzinfo or UTC


def _stand_abschnitt(text: str) -> str:
    """Text des Abschnitts ``## 3. Stand`` (leer, wenn der Abschnitt fehlt)."""
    if "## 3. Stand" not in text:
        return ""
    return text.split("## 3. Stand", 1)[1].split("\n## ", 1)[0]


def stand_zeitpunkte(text: str) -> list[datetime]:
    """Alle geparsten Zeitstempel im Abschnitt ``## 3. Stand`` in Reihenfolge ihres Auftretens."""
    abschnitt = _stand_abschnitt(text)
    if not abschnitt:
        return []
    zone = _stand_zone()
    return [datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=zone) for t in STAND_ZEILE.findall(abschnitt)]


def stand_zeitpunkt(text: str) -> datetime | None:
    """Jüngster Zeitstempel im Abschnitt ``## 3. Stand`` von ``docs/WIEDEREINSTIEG.md``."""
    zeiten = stand_zeitpunkte(text)
    return max(zeiten) if zeiten else None


def stand_referenz_commit(text: str) -> str | None:
    """Zitierter ``main``-Commit-SHA aus dem obersten Stand-Eintrag (``None``, wenn nicht genannt, #353)."""
    abschnitt = _stand_abschnitt(text)
    if not abschnitt:
        return None
    for zeile in abschnitt.splitlines():
        if not STAND_ZEILE.match(zeile):
            continue
        treffer = STAND_REFERENZ_MUSTER.match(zeile)
        return treffer.group(1) if treffer else None
    return None


def commit_zeitpunkt(repo: Path, ref: str) -> datetime | None:
    """Commit-Zeitpunkt von ``ref`` in ``repo`` (in :data:`STAND_ZEITZONE`); ``None`` wenn unbekannt (#353)."""
    try:
        aus = _git(repo, "log", "-1", "--format=%ct", ref)
    except (OSError, subprocess.SubprocessError):
        return None
    roh = aus.stdout.strip()
    if aus.returncode != 0 or not roh.isdigit():
        return None
    return datetime.fromtimestamp(int(roh), tz=UTC).astimezone(_stand_zone())


def stand_veraltet(text: str, referenz: datetime, frist: timedelta = STAND_FRIST) -> bool:
    """``True``, wenn der jüngste Stand-Eintrag fehlt oder älter ist als ``referenz`` minus ``frist``."""
    juengster = stand_zeitpunkt(text)
    if juengster is None:
        return True
    if referenz.tzinfo is None:
        referenz = referenz.replace(tzinfo=juengster.tzinfo)
    return juengster + frist < referenz


def wiedereinstieg_stand_hinweis(
    repo: Path,
    *,
    referenz: datetime | None = None,
    frist: timedelta = STAND_FRIST,
) -> str | None:
    """Betriebs-Hinweis, falls ``docs/WIEDEREINSTIEG.md`` älter als ``origin/main`` minus ``frist`` ist (#353).

    Gibt ``None`` zurück, wenn das Dokument fehlt, aktuell ist oder der Git-Verlauf flach ist.
    """
    datei = repo / "docs" / "WIEDEREINSTIEG.md"
    if not datei.is_file():
        return None
    merge = referenz if referenz is not None else juengster_main_stand(repo)
    if merge is None:
        return None
    try:
        text = datei.read_text(encoding="utf-8")
    except OSError:
        return None
    if not stand_veraltet(text, merge, frist):
        return None
    lokal = merge.astimezone(_stand_zone()) if merge.tzinfo is not None else merge
    return (
        f"docs/WIEDEREINSTIEG.md: jüngster Stand-Eintrag älter als jüngster main-Stand "
        f"{lokal:%Y-%m-%d %H:%M %Z} minus {frist} — Abschnitt „Stand“ fortschreiben (L5)."
    )


def juengster_main_stand(repo: Path) -> datetime | None:
    """Zeitpunkt des jüngsten Commits auf ``origin/main`` (inkl. Squash-Merges); ``None`` bei flachem Klon.

    Prüft ``origin/main`` (erste Elternlinie), damit Squash-Merges genauso zählen wie
    Merge-Commits und ungemergte Commits auf Arbeitszweigen keinen Fehlalarm auslösen.
    Ohne ``origin/main`` fällt die Prüfung bei vollständigem Verlauf auf ``main`` bzw. ``HEAD``
    zurück; bei flachem Klon ohne ``origin/main`` liefert sie ``None``.
    """
    try:
        flach = _git(repo, "rev-parse", "--is-shallow-repository")
        if flach.returncode != 0:
            return None
        ist_flach = flach.stdout.strip().lower() == "true"
        ziel_ref: str | None = None
        for kandidat in ("origin/main", "main", "HEAD"):
            if ist_flach and kandidat != "origin/main":
                break
            if _git(repo, "rev-parse", "--verify", "--quiet", kandidat).returncode == 0:
                ziel_ref = kandidat
                break
        if ziel_ref is None:
            return None
        aus = _git(repo, "log", "-1", "--first-parent", "--format=%ct", ziel_ref)
    except (OSError, subprocess.SubprocessError):
        return None
    roh = aus.stdout.strip()
    if aus.returncode != 0 or not roh.isdigit():
        return None
    return datetime.fromtimestamp(int(roh), tz=UTC).astimezone(_stand_zone())


def pruefen() -> SicherungPruefung:
    """Prüft ``sicherung.ziel`` und ``sicherung.dateien`` der Maschinen-Liste rein lesend (#755).

    Legt keine Ordner an, kopiert keine Dateien und lädt keine Zweige hoch. Meldet unlesbare
    ``maschine.json``, ungültige Feld-Typen, öffentliche Ziele und Muster ohne Treffer als Fehler.
    """
    datei = config.maschine_datei()
    vorhanden = datei.is_file()
    fehler: list[str] = []
    hinweise: list[str] = []
    muster: list[str] = []
    trefferlose: list[str] = []

    roh_maschine: dict[str, object] | None = None
    if vorhanden:
        gelesen, lesefehler = config._lies(datei)
        if gelesen is None:
            fehler.append(f"Maschinen-Liste unlesbar ({datei}): {lesefehler}")
        else:
            roh_maschine = gelesen
    else:
        hinweise.append(f"Maschinen-Liste fehlt ({datei}) — Vorgaben gelten")

    if roh_maschine is not None and "sicherung" in roh_maschine:
        roh_gruppe = roh_maschine["sicherung"]
        if not isinstance(roh_gruppe, dict):
            fehler.append(f"sicherung ist kein Objekt ({type(roh_gruppe).__name__})")
        else:
            if "ziel" in roh_gruppe and roh_gruppe["ziel"] is not None and not isinstance(roh_gruppe["ziel"], str):
                fehler.append(f"sicherung.ziel ist kein Text ({type(roh_gruppe['ziel']).__name__})")
            if "dateien" in roh_gruppe and roh_gruppe["dateien"] is not None:
                roh_dateien = roh_gruppe["dateien"]
                if not isinstance(roh_dateien, list):
                    fehler.append(f"sicherung.dateien ist keine Liste ({type(roh_dateien).__name__})")
                else:
                    for eintrag in roh_dateien:
                        if not isinstance(eintrag, str) or not eintrag.strip():
                            fehler.append(f"sicherung.dateien enthält ungültigen Eintrag: {eintrag!r}")
                            continue
                        sauber = eintrag.strip()
                        muster.append(sauber)
                        if not _muster_treffer(sauber):
                            trefferlose.append(sauber)
                    if trefferlose:
                        fehler.append("sicherung.dateien: Muster ohne Treffer: " + ", ".join(trefferlose))

    ziel, herkunft = _ziel_mit_herkunft()
    oeffentlich = ist_oeffentlich(ziel)
    ziel_da = ziel.is_dir()
    if oeffentlich:
        fehler.append(f"{ziel} liegt in einem öffentlichen Repo — Sicherung wäre gesperrt")
    elif not ziel_da:
        hinweise.append("Ziel-Ordner noch nicht angelegt (wird beim ersten Lauf erstellt)")

    if not muster and not any("sicherung.dateien" in f for f in fehler):
        hinweise.append(f"sicherung.dateien nicht eingetragen — nur Repo-Muster ({', '.join(MUSTER_JE_REPO)}) aktiv")

    quellen = _quellen([])
    zweig_repos = _zweig_repos()
    for repo in zweig_repos:
        if not repo.is_dir():
            hinweise.append(f"Zweig-Repo fehlt auf der Platte: {repo}")
    stand_hinweis = wiedereinstieg_stand_hinweis(_skill_wurzel())
    if stand_hinweis:
        hinweise.append(stand_hinweis)

    erg = SicherungPruefung(
        maschine_datei=datei,
        maschine_vorhanden=vorhanden,
        ziel=ziel,
        ziel_herkunft=herkunft,
        ziel_oeffentlich=oeffentlich,
        ziel_vorhanden=ziel_da,
        muster=muster,
        trefferlose_muster=trefferlose,
        quellen=quellen,
        zweig_repos=zweig_repos,
        fehler=fehler,
        hinweise=hinweise,
    )
    (log.warning if erg.fehler else log.info)("%s", erg.bericht().splitlines()[0])
    return erg


def richte_parser_ein(unter: object) -> None:
    p = unter.add_parser(  # type: ignore[attr-defined]
        "sichern",
        help="Übergaben + Entscheidungs-Log sichern, Zweige hochladen (L5, #145)",
    )
    p.add_argument("dateien", type=Path, nargs="*", help="zusätzliche Übergabe-Dateien")
    p.add_argument(
        "--trocken",
        action="store_true",
        help="nur zeigen, was gesichert und hochgeladen würde (#169)",
    )
    p.add_argument(
        "--pruefen",
        action="store_true",
        help="Maschinen-Pfade (sicherung.dateien / sicherung.ziel) rein lesend prüfen (#755)",
    )


def lauf(dateien: list[Path], *, trocken: bool = False, nur_pruefen: bool = False) -> int:
    if nur_pruefen:
        pruefung = pruefen()
        print(pruefung.bericht())
        return 0 if pruefung.ok else 1
    erg = sichern(dateien, trocken=trocken)
    print(erg.zeile())
    return 1 if erg.fehler else 0
