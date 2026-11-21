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
wird nichts kopiert. Rohe Übergaben gehören nie in ein öffentliches Repo.
"""

from __future__ import annotations

import glob
import logging
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger("to_spawn.uebergabe_sichern")

#: Öffentliche Repos, in die nie gesichert wird (und deren Zweige hochgeladen werden).
OEFFENTLICHE_SLUGS: tuple[str, ...] = ("Shavy72/claude-skill-to-spawn",)
#: Muster je Repo aus ``repos[]`` (relativ zum Repo-Ordner).
MUSTER_JE_REPO: tuple[str, ...] = (".claude-handoff*.md", "entscheidungen.md")
TRESOR_UNTERORDNER = Path("to-spawn") / "sicherung"
#: Zweige, die nie hochgeladen werden: Hauptzweige; ``ticket/*`` mergt die Deploy-Schlange sofort.
GESCHUETZTE_ZWEIGE: tuple[str, ...] = ("main", "master", "HEAD")
GESCHUETZTE_PRAEFIXE: tuple[str, ...] = ("ticket/",)
GIT_MAX_S = 60.0
#: Test- und Notfall-Türen (Vorrang vor der Maschinen-Liste).
UMGEBUNG_ZIEL = "TO_SPAWN_SICHERUNG_ZIEL"
#: Leerzeichen-getrennte Repo-Ordner für den Zweig-Upload; leer = keiner.
UMGEBUNG_ZWEIG_REPOS = "TO_SPAWN_SICHERUNG_ZWEIG_REPOS"


@dataclass
class Sicherung:
    """Ergebnis: ``kopiert`` = neu geschriebene Kopien, ``hochgeladen`` = ``repo:zweig``."""

    ziel: Path | None
    kopiert: list[Path] = field(default_factory=list)
    hochgeladen: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)

    def zeile(self) -> str:
        if self.ziel is None:
            return "Sicherung: kein erlaubtes Ziel — " + "; ".join(self.fehler)
        teile = [f"{len(self.kopiert)} Datei(en) nach {self.ziel}"]
        teile.append(f"{len(self.hochgeladen)} Zweig(e) hochgeladen")
        if self.fehler:
            teile.append(f"{len(self.fehler)} Fehler: " + "; ".join(self.fehler))
        return "Sicherung: " + ", ".join(teile)


def _gruppe() -> dict[str, object]:
    roh = config.maschine().get("sicherung")
    return roh if isinstance(roh, dict) else {}


def _skill_wurzel() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
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


def ziel_ordner() -> Path:
    """Sicherungs-Ordner dieser Maschine (ohne Öffentlich-Prüfung, siehe :func:`sichern`)."""
    roh = os.environ.get(UMGEBUNG_ZIEL) or str(_gruppe().get("ziel") or "").strip()
    if roh:
        basis = Path(roh).expanduser()
    else:
        tresor = Path(os.environ.get("OBSIDIAN_VAULT") or "~/obsidian").expanduser()
        if tresor.is_dir():
            basis = tresor / TRESOR_UNTERORDNER
        else:
            basis = Path.home() / ".local" / "share" / "to-spawn" / "sicherung"
    return basis / (socket.gethostname().lower() or "maschine")


def _quellen(extra: list[Path]) -> list[Path]:
    muster = [
        str(Path(e["pfad"]).expanduser() / m)
        for e in config.maschine()["repos"]
        if e["pfad"]
        for m in MUSTER_JE_REPO
    ]
    roh = _gruppe().get("dateien")
    muster += (
        [str(m) for m in roh if isinstance(m, str)] if isinstance(roh, list) else []
    )
    gefunden = [Path(t) for m in muster for t in glob.glob(os.path.expanduser(m))]
    eindeutig: dict[Path, Path] = {}
    for pfad in [*extra, *gefunden]:
        if pfad.is_file():
            eindeutig.setdefault(pfad.resolve(), pfad)
    return list(eindeutig.values())


def kopie_name(quelle: Path) -> str:
    """``<Elternordner>__<Name>`` ohne führenden Punkt (sonst im Tresor unsichtbar)."""
    return f"{quelle.resolve().parent.name}__{quelle.name.lstrip('.')}"


def _kopieren(quelle: Path, ziel: Path) -> bool:
    """Kopiert atomar, nur wenn der Inhalt sich geändert hat; ``True`` = geschrieben."""
    daten = quelle.read_bytes()
    datei = ziel / kopie_name(quelle)
    if datei.is_file() and datei.read_bytes() == daten:
        return False
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
        return [Path(p).expanduser() for p in roh.split()]
    slugs = _gruppe().get("zweige_slugs")
    erlaubt = {
        s.lower()
        for s in (slugs if isinstance(slugs, list) else OEFFENTLICHE_SLUGS)
        if isinstance(s, str)
    }
    return [
        Path(e["pfad"]).expanduser()
        for e in config.maschine()["repos"]
        if e["pfad"] and e["slug"].lower() in erlaubt
    ]


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
    return zweig not in GESCHUETZTE_ZWEIGE and not zweig.startswith(
        GESCHUETZTE_PRAEFIXE
    )


def _hochladen(repo: Path, erg: Sicherung) -> None:
    # ponytail: nur Commits, nicht der lose Arbeitsstand; wip-Schnappschuss (git stash create) wenn nötig.
    for ordner, zweig in _worktree_zweige(repo):
        if not darf_hochladen(zweig):
            continue
        offen = _git(ordner, "rev-list", "--count", f"origin/{zweig}..{zweig}")
        if offen.returncode == 0 and offen.stdout.strip() == "0":
            continue  # nichts Neues
        aus = _git(ordner, "push", "origin", f"refs/heads/{zweig}:refs/heads/{zweig}")
        if aus.returncode == 0:
            erg.hochgeladen.append(f"{repo.name}:{zweig}")
        else:
            erg.fehler.append(f"Push {repo.name}:{zweig} — {aus.stderr.strip()[:200]}")


def sichern(dateien: list[Path] | None = None) -> Sicherung:
    """Sichert ``dateien`` + die bekannten Übergaben/Logs dieser Maschine, lädt Zweige hoch.

    Wirft nie; Fehler stehen in :attr:`Sicherung.fehler` und im Log. Liegt das Ziel in einem
    öffentlichen Repo, wird nichts kopiert (``ziel`` = ``None``), Zweige werden trotzdem
    hochgeladen (die liegen ohnehin im Repo).
    """
    erg = Sicherung(None)
    try:
        ziel = ziel_ordner()
        if ist_oeffentlich(ziel):
            erg.fehler.append(
                f"{ziel} liegt in einem öffentlichen Repo — nichts kopiert"
            )
            log.error(
                "Sicherung: %s liegt in einem öffentlichen Repo — nichts kopiert", ziel
            )
        else:
            ziel.mkdir(parents=True, exist_ok=True)
            erg.ziel = ziel
            for quelle in _quellen(list(dateien or [])):
                try:
                    if _kopieren(quelle, ziel):
                        erg.kopiert.append(quelle)
                except OSError as fehler:
                    erg.fehler.append(f"{quelle.name}: {fehler}")
    except Exception as fehler:  # Vertrag: wirft nie (Aufrufer: Ablösung, Hausmeister)
        log.exception("Sicherung: Dateien gescheitert")
        erg.fehler.append(f"Dateien: {type(fehler).__name__}: {fehler}")
    try:
        for repo in _zweig_repos():
            try:
                _hochladen(repo, erg)
            except (OSError, subprocess.SubprocessError) as fehler:
                erg.fehler.append(f"Zweige {repo}: {fehler}")
    except Exception as fehler:  # Vertrag: wirft nie
        log.exception("Sicherung: Zweige gescheitert")
        erg.fehler.append(f"Zweige: {type(fehler).__name__}: {fehler}")
    (log.warning if erg.fehler else log.info)("%s", erg.zeile())
    return erg


def richte_parser_ein(unter: object) -> None:
    p = unter.add_parser(  # type: ignore[attr-defined]
        "sichern",
        help="Übergaben + Entscheidungs-Log sichern, Zweige hochladen (L5, #145)",
    )
    p.add_argument("dateien", type=Path, nargs="*", help="zusätzliche Übergabe-Dateien")


def lauf(dateien: list[Path]) -> int:
    erg = sichern(dateien)
    print(erg.zeile())
    return 1 if erg.fehler else 0
