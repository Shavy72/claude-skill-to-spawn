"""Prüf-Skript für doppelte ``to-spawn``-Ablauf-Texte außerhalb des Skills (#584, E20/E21).

Einzige Quelle für die Regel-Prüfung: Ablaufbeschreibungen von ``to-spawn`` gehören
ausschließlich in den Skill (``SKILL.md``, ``to_spawn/anleitung.py``, ``to_spawn/befugnis.py``,
``repo-scripts/_default.json``). Globale Regel-Blöcke (``kette/global/*.md``), andere
Ketten-Skills (``kette/skills/*``) und Projekt-Regeldateien (``CLAUDE.md``, ``AGENTS.md``,
``.claude/rules/*.md``, ``.to-spawn/kette.md``) dürfen kurze Querverweise tragen, aber keine
doppelten Ablauf-Texte.

Zwei Erkennungswege (laufen auf Linux und Windows gleich, rein über ``pathlib``):
1. **Kopie (``kopie``):** Wortfolge ab :data:`SHINGLE_LAENGE` aufeinanderfolgenden
   normalisierten Wörtern aus den kanonischen ``to-spawn``-Quellen.
2. **Ablauf-Cluster (``ablauf_cluster``):** mindestens :data:`CLUSTER_SCHWELLE` verschiedene
   ``to-spawn``-Laufzeit-Signale innerhalb desselben Markdown-Abschnitts (fängt auch
   umformulierte Ablauf-Kopien ab, ohne einzelne Querverweise zu melden).

Türen: :func:`pruefe_text`, :func:`pruefe_dateien`, :func:`pruefe_repo`,
:func:`pruefe_skill_repo`.
Aufruf: ``python -m to_spawn.regel_pruefung [--wurzel <Pfad>] [--datei <Pfad>…] [--skill-repo] [--json]``
oder ``python to_spawn.py regeln-pruefen …`` (Exit 0 sauber · 1 Funde · 2 Eingabefehler).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import anleitung, befugnis, konsole

log = logging.getLogger(__name__)

#: Wurzelordner des Skill-Repos (dort liegen die kanonischen Quellen).
SKILL_WURZEL = Path(__file__).resolve().parents[1]
#: Mindestlänge einer zusammenhängenden Wortfolge für die Kopie-Erkennung.
SHINGLE_LAENGE = 10
#: Mindestzahl verschiedener Laufzeit-Signale je Abschnitt für ein Ablauf-Cluster.
CLUSTER_SCHWELLE = 3

#: Generierte Spec-Lauf-Dateien unter ``docs/agents/``, die keine handgepflegten Regeldateien sind.
_LAUF_ARTEFAKT_MUSTER = re.compile(
    r"^(?:rundschau_auftrag_|entscheidungen_|selbstpruefung_|live_bereit_|thermo_|rueckblick_|abschluss_).+\.md$"
    r"|^FEHLERKATALOG_spawn\.md$",
    re.IGNORECASE,
)
_WORT_MUSTER = re.compile(r"[a-zäöüß0-9][a-zäöüß0-9_./:-]*")
_UEBERSCHRIFT_MUSTER = re.compile(r"^\s{0,3}#{1,6}\s+")
_FRONTMATTER_MUSTER = re.compile(r"\A---\s*\r?\n.*?\r?\n---\s*(?:\r?\n|$)", re.DOTALL)

#: Laufzeit-Signale von ``to-spawn``, die nicht in Projekt- oder globalen Regeln beschrieben werden.
#: Normale Ketten-Aufrufe wie ``anhaengen``, ``regularien``, ``lernstoff`` oder ``/to-spawn <S>``
#: gehören bewusst nicht dazu, damit kurze Querverweise nie Fehlalarme auslösen.
_ABLAUF_SIGNALE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bau-skript", re.compile(r"\bbau\.py\b|\bbau\s+<n>", re.IGNORECASE)),
    ("aufseher-skript", re.compile(r"\bwache\.py\b|\baufseher\s+<s>", re.IGNORECASE)),
    ("sessions-stand", re.compile(r"\bsessions_stand\.py\b|\bsessions\s+<s>", re.IGNORECASE)),
    ("verwaist", re.compile(r"\bverwaist\b", re.IGNORECASE)),
    ("abloese-sop", re.compile(r"ablöse-?sop|staffel:\s*weiter|\bstaffel_stop\.py\b", re.IGNORECASE)),
    ("eingriffs-leiter", re.compile(r"eingriffs-?leiter|to_spawn\.py\s+leiter\b", re.IGNORECASE)),
    ("respawn", re.compile(r"to_spawn\.py\s+respawn\b", re.IGNORECASE)),
    ("umzug", re.compile(r"to_spawn\.py\s+umzug(?:-alle)?\b|umzug:\s*server", re.IGNORECASE)),
    ("spawn-skript", re.compile(r"\bspawn_local\.ps1\b|\bspawn_srv\.(?:ps1|sh)\b", re.IGNORECASE)),
    ("schedule-wakeup", re.compile(r"\bschedulewakeup\b", re.IGNORECASE)),
    ("aufseher-stand", re.compile(r"to_spawn\.py\s+aufseher-stand\b|\bcapo\.py\b", re.IGNORECASE)),
    (
        "abschluss-paket",
        re.compile(r"\babschluss_paket\.py\b|\bthermo_lauf\.py\b|\brueckblick\.py\b", re.IGNORECASE),
    ),
    ("briefkasten-ablauf", re.compile(r"to_spawn\.py\s+(?:frage|antwort|antworten|offene)\b", re.IGNORECASE)),
    (
        "mindset-kern",
        re.compile(r"staging,\s*entscheide\s+selbst|alles\s+geht\s+erst\s+auf\s+staging", re.IGNORECASE),
    ),
    ("aufpasser", re.compile(r"\baufpasser\.py\b|to_spawn\.py\s+aufpasser\b", re.IGNORECASE)),
    (
        "harte-env-regel",
        re.compile(r"claude_code_force_session_persistence|claude_code_disable_mouse_clicks", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class Fund:
    """Ein gefundener doppelter ``to-spawn``-Ablauf-Text außerhalb des Skills."""

    datei: str
    zeile: int
    art: str
    quelle: str
    ausschnitt: str

    def als_dict(self) -> dict[str, Any]:
        return {
            "datei": self.datei,
            "zeile": self.zeile,
            "art": self.art,
            "quelle": self.quelle,
            "ausschnitt": self.ausschnitt,
        }

    def zeile_text(self) -> str:
        return f"{self.datei}:{self.zeile} [{self.art}] ({self.quelle}): {self.ausschnitt}"


@dataclass(frozen=True)
class Ergebnis:
    """Gesamturteil eines Prüflaufs."""

    ok: bool
    gepruefte_dateien: int
    funde: tuple[Fund, ...]

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "gepruefte_dateien": self.gepruefte_dateien,
            "funde": [fund.als_dict() for fund in self.funde],
        }

    def text(self) -> str:
        if self.ok:
            return f"[ok] Keine doppelten to-spawn-Ablauf-Texte gefunden ({self.gepruefte_dateien} Datei(en) geprüft)."
        kopf = (
            f"[!!] {len(self.funde)} doppelte(r) to-spawn-Ablauf-Text(e) außerhalb des Skills "
            f"({self.gepruefte_dateien} Datei(en) geprüft):"
        )
        return "\n".join([kopf, *(f"  - {fund.zeile_text()}" for fund in self.funde)])


def _woerter_mit_zeile(text: str) -> list[tuple[str, int]]:
    """Zerlegt ``text`` in normalisierte Wörter samt 1-basierter Zeilennummer."""
    ergebnis: list[tuple[str, int]] = []
    for zeilen_nr, zeile in enumerate(text.splitlines(), start=1):
        for treffer in _WORT_MUSTER.finditer(zeile.lower()):
            wort = treffer.group(0).strip("._:/-")
            if wort:
                ergebnis.append((wort, zeilen_nr))
    return ergebnis


def _kanonische_shingles(
    skill_wurzel: Path = SKILL_WURZEL,
    shingle_laenge: int = SHINGLE_LAENGE,
) -> dict[tuple[str, ...], str]:
    """Erstellt den Shingle-Index aller kanonischen ``to-spawn``-Ablauf-Quellen."""
    quellen: list[tuple[str, str]] = [
        ("to_spawn/anleitung.py:MINDSET", anleitung.MINDSET),
        ("to_spawn/anleitung.py:STOSS_SCHLUSS", anleitung.STOSS_SCHLUSS),
        ("to_spawn/anleitung.py:entscheidungs_linie", anleitung.entscheidungs_linie()),
        ("to_spawn/anleitung.py:ABLOESE_SOP", anleitung.ABLOESE_SOP),
        ("to_spawn/anleitung.py:ABSCHLUSS_ANWEISUNG", anleitung.ABSCHLUSS_ANWEISUNG),
        ("to_spawn/anleitung.py:handoff_auftrag", anleitung.handoff_auftrag("<handoff>", "<start>")),
        ("to_spawn/befugnis.py:start_text", befugnis.start_text("<N>")),
        ("to_spawn/befugnis.py:aufseher_text", befugnis.aufseher_text("python to_spawn.py")),
    ]
    skill_md = skill_wurzel / "SKILL.md"
    if skill_md.is_file():
        roh = _FRONTMATTER_MUSTER.sub("", skill_md.read_text(encoding="utf-8"))
        quellen.append(("SKILL.md", roh))
    vorlage = skill_wurzel / "repo-scripts" / "_default.json"
    if vorlage.is_file():
        try:
            daten = json.loads(vorlage.read_text(encoding="utf-8"))
        except (OSError, ValueError) as fehler:
            log.warning("Vorlage %s nicht lesbar: %s", vorlage, fehler)
        else:
            prompt = daten.get("prompt_template")
            if isinstance(prompt, str) and prompt.strip():
                quellen.append(("repo-scripts/_default.json", prompt))

    index: dict[tuple[str, ...], str] = {}
    for quellen_name, inhalt in quellen:
        woerter = [wort for wort, _ in _woerter_mit_zeile(inhalt)]
        if len(woerter) < shingle_laenge:
            continue
        for pos in range(len(woerter) - shingle_laenge + 1):
            shingle = tuple(woerter[pos : pos + shingle_laenge])
            index.setdefault(shingle, quellen_name)
    return index


def _abschnitte(text: str) -> list[tuple[int, str]]:
    """Teilt Markdown-Text anhand von Überschriften in ``(start_zeile, inhalt)``."""
    teile: list[tuple[int, list[str]]] = []
    start = 1
    aktuell: list[str] = []
    for nr, zeile in enumerate(text.splitlines(), start=1):
        if _UEBERSCHRIFT_MUSTER.match(zeile) and aktuell:
            teile.append((start, aktuell))
            start = nr
            aktuell = [zeile]
        else:
            if not aktuell:
                start = nr
            aktuell.append(zeile)
    if aktuell:
        teile.append((start, aktuell))
    return [(nr, "\n".join(zeilen)) for nr, zeilen in teile]


def pruefe_text(
    text: str,
    rel_pfad: str,
    *,
    shingles: dict[tuple[str, ...], str] | None = None,
    skill_wurzel: Path = SKILL_WURZEL,
    shingle_laenge: int = SHINGLE_LAENGE,
    cluster_schwelle: int = CLUSTER_SCHWELLE,
) -> list[Fund]:
    """Prüft einen einzelnen Regeltext auf doppelte ``to-spawn``-Abläufe."""
    norm_pfad = rel_pfad.replace("\\", "/")
    index = shingles if shingles is not None else _kanonische_shingles(skill_wurzel, shingle_laenge)
    funde: list[Fund] = []

    # 1. Wörtliche bzw. nahezu wörtliche Kopien (Shingles).
    woerter = _woerter_mit_zeile(text)
    if len(woerter) >= shingle_laenge:
        gemeldete_zeilen: set[tuple[int, str]] = set()
        pos = 0
        grenze = len(woerter) - shingle_laenge + 1
        while pos < grenze:
            fenster = tuple(w for w, _ in woerter[pos : pos + shingle_laenge])
            quelle = index.get(fenster)
            if quelle is not None:
                zeile = woerter[pos][1]
                schluessel = (zeile, quelle)
                if schluessel not in gemeldete_zeilen:
                    gemeldete_zeilen.add(schluessel)
                    ausschnitt = " ".join(fenster[:10])
                    funde.append(
                        Fund(
                            datei=norm_pfad,
                            zeile=zeile,
                            art="kopie",
                            quelle=quelle,
                            ausschnitt=ausschnitt,
                        )
                    )
                pos += shingle_laenge
            else:
                pos += 1

    # 2. Umformulierte Ablauf-Blöcke (Signal-Cluster je Abschnitt).
    for start_zeile, abschnitt in _abschnitte(text):
        treffer_namen: list[str] = []
        erste_zeile: int | None = None
        for name, muster in _ABLAUF_SIGNALE:
            match = muster.search(abschnitt)
            if match is not None:
                treffer_namen.append(name)
                zeilen_versatz = abschnitt.count("\n", 0, match.start())
                fund_zeile = start_zeile + zeilen_versatz
                if erste_zeile is None or fund_zeile < erste_zeile:
                    erste_zeile = fund_zeile
        if len(treffer_namen) >= cluster_schwelle and erste_zeile is not None:
            funde.append(
                Fund(
                    datei=norm_pfad,
                    zeile=erste_zeile,
                    art="ablauf_cluster",
                    quelle="to-spawn-Ablauf-Signale",
                    ausschnitt=", ".join(treffer_namen),
                )
            )

    return funde


def _rel_pfad(pfad: Path, basis: Path | None) -> str:
    if basis is not None:
        try:
            return pfad.resolve().relative_to(basis.resolve()).as_posix()
        except ValueError:
            pass
    return pfad.as_posix()


def pruefe_dateien(
    dateien: Iterable[Path],
    *,
    basis: Path | None = None,
    skill_wurzel: Path = SKILL_WURZEL,
) -> Ergebnis:
    """Prüft eine explizite Liste von Regeldateien gegen die kanonischen Skill-Quellen."""
    shingles = _kanonische_shingles(skill_wurzel)
    alle_funde: list[Fund] = []
    gezaehlt = 0
    for datei in dateien:
        if not datei.is_file():
            raise FileNotFoundError(f"Datei nicht gefunden: {datei}")
        gezaehlt += 1
        inhalt = datei.read_text(encoding="utf-8")
        alle_funde.extend(pruefe_text(inhalt, _rel_pfad(datei, basis), shingles=shingles))
    return Ergebnis(ok=not alle_funde, gepruefte_dateien=gezaehlt, funde=tuple(alle_funde))


def _zusatz_datei_aus_konfig(wurzel: Path) -> Path | None:
    """Liest ``start_text.zusatz_datei`` aus ``.to-spawn/config.json``, falls innerhalb von ``wurzel``."""
    konfig = wurzel / ".to-spawn" / "config.json"
    if not konfig.is_file():
        return None
    try:
        daten = json.loads(konfig.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    start_text = daten.get("start_text") if isinstance(daten, dict) else None
    rel = start_text.get("zusatz_datei") if isinstance(start_text, dict) else None
    if not isinstance(rel, str) or not rel.strip():
        return None
    kandidat = (wurzel / rel).resolve()
    try:
        kandidat.relative_to(wurzel.resolve())
    except ValueError:
        return None
    return kandidat if kandidat.is_file() else None


def repo_regeldateien(wurzel: Path) -> list[Path]:
    """Findet alle handgepflegten Regeldateien eines Projekt-Repos (ohne Lauf-Artefakte)."""
    kandidaten: list[Path] = []
    for rel in ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md", ".to-spawn/kette.md"):
        pfad = wurzel / rel
        if pfad.is_file():
            kandidaten.append(pfad)
    regeln_dir = wurzel / ".claude" / "rules"
    if regeln_dir.is_dir():
        kandidaten.extend(sorted(p for p in regeln_dir.rglob("*.md") if p.is_file()))
    agenten_dir = wurzel / "docs" / "agents"
    if agenten_dir.is_dir():
        for pfad in sorted(agenten_dir.glob("*.md")):
            if pfad.is_file() and not _LAUF_ARTEFAKT_MUSTER.match(pfad.name):
                kandidaten.append(pfad)
    zusatz = _zusatz_datei_aus_konfig(wurzel)
    if zusatz is not None:
        kandidaten.append(zusatz)

    eindeutig: list[Path] = []
    gesehen: set[Path] = set()
    for pfad in kandidaten:
        aufgeloest = pfad.resolve()
        if aufgeloest not in gesehen:
            gesehen.add(aufgeloest)
            eindeutig.append(pfad)
    return eindeutig


def pruefe_repo(
    wurzel: Path,
    *,
    extra_dateien: Sequence[Path] = (),
    skill_wurzel: Path = SKILL_WURZEL,
) -> Ergebnis:
    """Prüft alle Regeldateien eines Projekt-Repos auf doppelte ``to-spawn``-Ablauf-Texte."""
    if not wurzel.is_dir():
        raise FileNotFoundError(f"Ordner nicht gefunden: {wurzel}")
    dateien = repo_regeldateien(wurzel)
    gesehen = {p.resolve() for p in dateien}
    for extra in extra_dateien:
        if extra.resolve() not in gesehen:
            dateien.append(extra)
            gesehen.add(extra.resolve())
    return pruefe_dateien(dateien, basis=wurzel, skill_wurzel=skill_wurzel)


def pruefe_skill_repo(skill_wurzel: Path = SKILL_WURZEL) -> Ergebnis:
    """Prüft alle Ketten-Regeldateien im Skill-Repo außerhalb des ``to-spawn``-Skills."""
    if not skill_wurzel.is_dir():
        raise FileNotFoundError(f"Skill-Ordner nicht gefunden: {skill_wurzel}")
    dateien: list[Path] = []
    global_dir = skill_wurzel / "kette" / "global"
    if global_dir.is_dir():
        dateien.extend(sorted(p for p in global_dir.glob("*.md") if p.is_file()))
    kette_skills = skill_wurzel / "kette" / "skills"
    if kette_skills.is_dir():
        dateien.extend(sorted(p for p in kette_skills.glob("*/SKILL.md") if p.is_file()))
    return pruefe_dateien(dateien, basis=skill_wurzel, skill_wurzel=skill_wurzel)


def _parser_optionen(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wurzel",
        type=Path,
        default=None,
        help="Projekt-Repo-Ordner für die Prüfung (CLAUDE.md, AGENTS.md, .claude/rules, .to-spawn/kette.md)",
    )
    parser.add_argument(
        "--datei",
        action="append",
        type=Path,
        default=[],
        metavar="PFAD",
        help="zusätzliche oder einzelne Regeldatei prüfen (mehrfach möglich)",
    )
    parser.add_argument(
        "--skill-repo",
        action="store_true",
        help="Ketten-Regeldateien des Skill-Repos (kette/global/*.md, kette/skills/*/SKILL.md) prüfen",
    )
    parser.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")


def richte_parser_ein(unter: argparse._SubParsersAction[Any]) -> None:
    """Registriert den Unterbefehl ``regeln-pruefen`` in ``to_spawn.py`` (#584)."""
    parser = unter.add_parser(
        "regeln-pruefen",
        help="Regeldateien außerhalb des Skills auf doppelte to-spawn-Ablauf-Texte prüfen (Exit 0/1/2)",
    )
    _parser_optionen(parser)


def lauf(args: argparse.Namespace) -> int:
    """Führt den CLI-Befehl aus; Exit 0 = sauber, 1 = Funde, 2 = Eingabefehler."""
    try:
        if args.skill_repo:
            ziel = args.wurzel if args.wurzel is not None else SKILL_WURZEL
            ergebnis = pruefe_skill_repo(ziel)
        elif args.wurzel is not None:
            ergebnis = pruefe_repo(args.wurzel, extra_dateien=args.datei)
        elif args.datei:
            ergebnis = pruefe_dateien(args.datei)
        else:
            ergebnis = pruefe_repo(Path.cwd())
    except (FileNotFoundError, OSError) as fehler:
        log.error("[!!] %s", fehler)
        return 2

    if args.json:
        sys.stdout.write(json.dumps(ergebnis.als_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(ergebnis.text() + "\n")
    return 0 if ergebnis.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    # Windows-Pipes sind cp1252: Umlaute in JSON/Text sonst UnicodeDecodeError beim Aufrufer.
    konsole.utf8_ausgabe()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="to_spawn.regel_pruefung",
        description="Regeldateien außerhalb des Skills auf doppelte to-spawn-Ablauf-Texte prüfen",
    )
    _parser_optionen(parser)
    return lauf(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
