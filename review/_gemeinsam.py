"""Gemeinsame Bausteine für review_route, review_beleg, review_bericht (VERTRAG.md §2–§5)."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

LOG = logging.getLogger("review")
HIER = Path(__file__).resolve().parent
STUFEN = ("keine", "klein", "mittel", "gross")
MOMENTE = (
    "subagent",
    "commit",
    "ticket",
    "deploy",
    "bug",
    "ui",
    "plan",
    "manuell",
    "sitzung",
)
LEERER_BAUM = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
KEIN_SHA = "0000000"


class EingabeFehler(Exception):
    """Kaputte Eingabe → Exit 2."""


#: Ausgelieferte Standardwerte; der Installer aktualisiert nur diese Datei.
STANDARD_CONFIG = HIER / "config.standard.json"


def config_pfad() -> Path:
    """Lokale Einstellungen (optional, nie ausgeliefert): der Installer fasst sie nie an."""
    return Path(os.environ.get("REVIEW_CONFIG") or HIER / "config.json")


def log_pfad() -> Path:
    return Path(os.environ.get("REVIEW_LOG") or HIER / "ausloeser.jsonl")


def lade_config() -> dict[str, Any]:
    """Standard plus lokale Config; lokale Schlüssel gewinnen, fehlende kommen aus dem Standard.

    ponytail: Mischung nur auf oberster Ebene (lokales ``stufen`` ersetzt das ganze ``stufen``),
    tief mischen erst, wenn Teil-Überschreibungen gebraucht werden.
    """
    cfg: dict[str, Any] = {}
    for pfad in (STANDARD_CONFIG, config_pfad()):
        try:
            teil = json.loads(pfad.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # Lokale Config ist optional; fehlt auch der Standard, prüfen die Aufrufer ihre Schlüssel.
        except (OSError, json.JSONDecodeError) as fehler:
            raise EingabeFehler(f"Config {pfad} unlesbar: {fehler}") from fehler
        if not isinstance(teil, dict):
            raise EingabeFehler(f"Config {pfad} muss ein JSON-Objekt sein")
        cfg.update(teil)
    return cfg


def jetzt() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def logging_einrichten() -> None:
    for strom in (sys.stdout, sys.stderr):
        if isinstance(strom, io.TextIOWrapper):
            strom.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="review: %(levelname)s %(message)s")


# ---------------------------------------------------------------- git


def git(repo: Path, *args: str, check: bool = True) -> str:
    ergebnis = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and ergebnis.returncode != 0:
        raise EingabeFehler(f"git {' '.join(args)}: {ergebnis.stderr.strip()}")
    return ergebnis.stdout


def repo_wurzel(pfad: Path) -> Path | None:
    if not pfad.is_dir():
        raise EingabeFehler(f"Repo-Pfad fehlt: {pfad}")
    try:
        ergebnis = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=pfad,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as fehler:
        LOG.warning("git nicht startbar: %s", fehler)
        return None
    if ergebnis.returncode != 0:
        return None
    return Path(ergebnis.stdout.strip())


def kurz_sha(repo: Path) -> str:
    sha = git(repo, "rev-parse", "--short=7", "HEAD", check=False).strip()
    return sha or KEIN_SHA


@dataclass
class DateiDiff:
    pfad: str
    zeilen: int = 0
    hinzu: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


_KOPF = re.compile(r"^diff --git a/(.*) b/(.*)$")


def parse_diff(text: str) -> dict[str, DateiDiff]:
    """Unified Diff → je Datei: geänderte Zeilen (+/-), hinzugefügte Zeilen, Rohtext."""
    dateien: dict[str, DateiDiff] = {}
    aktuell: DateiDiff | None = None
    im_hunk = False
    alt: str | None = None
    for zeile in text.splitlines():
        kopf = _KOPF.match(zeile)
        if kopf:
            aktuell = DateiDiff(kopf.group(2))
            dateien[aktuell.pfad] = aktuell
            im_hunk, alt = False, None
        if aktuell is None:
            continue
        aktuell.text.append(zeile)
        if not im_hunk and zeile.startswith("--- "):
            alt = None if zeile == "--- /dev/null" else zeile[4:].removeprefix("a/")
        elif not im_hunk and zeile.startswith("+++ "):
            neu = None if zeile == "+++ /dev/null" else zeile[4:].removeprefix("b/")
            pfad = neu or alt
            if pfad and pfad != aktuell.pfad:
                del dateien[aktuell.pfad]
                aktuell.pfad = pfad
                dateien[pfad] = aktuell
        elif zeile.startswith("@@"):
            im_hunk = True
        elif im_hunk and zeile.startswith("+"):
            aktuell.zeilen += 1
            aktuell.hinzu.append(zeile[1:])
        elif im_hunk and zeile.startswith("-"):
            aktuell.zeilen += 1
    return dateien


def diff_holen(repo: Path, base: str | None, staged: bool, files: list[str] | None) -> dict[str, DateiDiff]:
    hat_head = bool(git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip())
    basis = ["diff", "--no-color", "--no-ext-diff", "--no-renames"]
    if staged:
        argumente = [*basis, "--cached"]
    elif base:
        if not hat_head:
            raise EingabeFehler("--base ohne Commit im Repo")
        mb = git(repo, "merge-base", base, "HEAD").strip()
        argumente = [*basis, mb]
    else:
        argumente = [*basis, "HEAD" if hat_head else LEERER_BAUM]
    if files:
        argumente += ["--", *files]
    dateien = parse_diff(git(repo, *argumente))
    if files:
        bekannt = set(git(repo, "ls-files", "--", *files).splitlines())
        for f in files:
            rel = PurePosixPath(Path(f).as_posix()).as_posix()
            ziel = repo / rel
            if rel in bekannt or rel in dateien or not ziel.is_file():
                continue
            try:
                inhalt = ziel.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as fehler:
                LOG.warning("untracked %s unlesbar: %s", rel, fehler)
                continue
            dateien[rel] = DateiDiff(
                rel,
                len(inhalt),
                inhalt,
                [f"untracked {rel}", *("+" + z for z in inhalt)],
            )
    return dateien


# Keine Code-Dateien für die Größen-Regel: Tests, Doku, Daten, Lockfiles.
_GROESSE_AUSNAHMEN = (
    "test_*.py", "*_test.py", "*.test.*", "*.spec.*", "tests/**", "*/tests/**",
    "*.md", "*.txt", "*.rst", "*.json", "*.lock", "*-lock.*", "*.lockb",
)  # fmt: skip


def _zeilen_von(repo: Path, rev: str | None, pfad: str) -> int | None:
    """Zeilenzahl von `rev:pfad` (rev "" = Index, None = Arbeitsbaum); fehlt die Datei → None."""
    if rev is None:
        ziel = repo / pfad
        if not ziel.is_file():
            return None
        try:
            return len(ziel.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as fehler:
            LOG.warning("Datei-Größe: %s unlesbar: %s", pfad, fehler)
            return None
    ergebnis = subprocess.run(
        ["git", "-c", "core.quotepath=false", "show", f"{rev}:{pfad}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return len(ergebnis.stdout.splitlines()) if ergebnis.returncode == 0 else None


def datei_groesse(
    repo: Path, dateien: list[str], base: str | None, staged: bool, cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """Code-Dateien, die die Zeilengrenze überschreiten oder oberhalb stark wachsen (VERTRAG §4)."""
    regel = cfg.get("datei_groesse", {})
    grenze, zuwachs = int(regel.get("grenze", 1000)), int(regel.get("zuwachs", 150))
    hat_head = bool(git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip())
    # Basis und Stand genau wie diff_holen: staged = HEAD→Index, base = merge-base→Arbeitsbaum, sonst HEAD→Arbeitsbaum.
    vor_rev = git(repo, "merge-base", base, "HEAD").strip() if base else ("HEAD" if hat_head else "")
    nach_rev = "" if staged else None
    treffer: list[dict[str, Any]] = []
    for pfad in dateien:
        if passt(pfad, _GROESSE_AUSNAHMEN):
            continue
        nachher = _zeilen_von(repo, nach_rev, pfad)
        if nachher is None:  # gelöscht
            continue
        vorher = (_zeilen_von(repo, vor_rev, pfad) if vor_rev else None) or 0
        if vorher < grenze <= nachher:
            art = "ueber_1000"
        elif vorher >= grenze and nachher - vorher > zuwachs:
            art = "waechst_gross"
        else:
            continue
        treffer.append({"datei": pfad, "vorher": vorher, "nachher": nachher, "art": art})
    return treffer


# ---------------------------------------------------------------- Signale (§3)


@dataclass(frozen=True)
class Signal:
    id: str
    klasse: str
    muster: tuple[str, ...] = ()
    pfad_woerter: tuple[str, ...] = ()
    zeilen_woerter: tuple[str, ...] = ()
    pruefer: tuple[tuple[str, str], ...] = ()  # (name, typ)


_SICHERHEIT = (
    "auth", "login", "token", "bearer", "secret", "password", ".env", "permission", "rolle", "role",
)  # fmt: skip
# Projekt-eigene Wörter (Modul-/Dienstnamen) nur in der Config → "projekt_woerter" (#329 P2).
_DATENBANK = ("migration", "set_status", "alter table", "create table")
_KOSTEN = ("shutdown", "boot", "proxy", "power_off")
_INFRA = (
    "Dockerfile*",
    "requirements*.txt",
    "*compose*.y*ml",
    "pyproject.toml",
    "package.json",
)

SIGNALE: tuple[Signal, ...] = (
    Signal("python", "normal", ("*.py",), pruefer=(("python-reviewer", "agent"),)),
    Signal(
        "typescript",
        "normal",
        ("*.ts", "*.tsx"),
        pruefer=(("typescript-reviewer", "agent"),),
    ),
    Signal("react", "normal", ("*.tsx", "*.jsx"), pruefer=(("react-reviewer", "agent"),)),
    Signal(
        "javascript",
        "normal",
        ("*.js", "*.mjs", "*.cjs"),
        pruefer=(("code-reviewer", "agent"),),
    ),
    Signal(
        "shell",
        "hart",
        ("*.sh", "*.ps1"),
        ("deploy",),
        pruefer=(("code-reviewer", "agent"),),
    ),
    Signal("n8n", "normal", pruefer=(("code-reviewer", "agent"),)),
    Signal(
        "fehlerpfad",
        "normal",
        zeilen_woerter=("except", "catch", "|| ", "?? ", ".get(", 'or ""', "default="),
        pruefer=(("silent-failure-hunter", "agent"),),
    ),
    Signal(
        "sicherheit",
        "hart",
        (),
        _SICHERHEIT,
        _SICHERHEIT,
        (("security-reviewer", "agent"),),
    ),
    Signal(
        "datenbank",
        "hart",
        ("*.sql",),
        _DATENBANK,
        _DATENBANK,
        (("database-reviewer", "agent"), ("silent-failure-hunter", "agent")),
    ),
    Signal("kosten", "hart", (), _KOSTEN, _KOSTEN, (("silent-failure-hunter", "agent"),)),
    Signal(
        "oberflaeche",
        "normal",
        ("web/static/**", "*.html", "*.css", "*.tsx", "*.jsx", "*.vue"),
        pruefer=(("oberflaeche", "skill"),),
    ),
    Signal("infra", "hart", _INFRA, pruefer=(("code-reviewer", "agent"),)),
    Signal(
        "tests",
        "normal",
        ("test_*.py", "*_test.py", "*.test.*", "*.spec.*"),
        pruefer=(("pr-test-analyzer", "agent"),),
    ),
)


def signale_fuer(cfg: dict[str, Any]) -> tuple[Signal, ...]:
    """SIGNALE plus ``cfg["projekt_woerter"]`` ({Signal-ID: [Wort, …]}): gelten für Pfad und Zeilen.

    So bleibt der Code projekt-neutral; Modul- und Dienstnamen eines Projekts stehen nur in der Config.
    """
    extra = cfg.get("projekt_woerter") or {}
    if not isinstance(extra, dict) or not all(
        isinstance(w, list) and all(isinstance(x, str) for x in w) for w in extra.values()
    ):
        raise EingabeFehler('Config "projekt_woerter" muss {Signal-ID: [Wort, …]} sein')
    unbekannt = sorted(set(extra) - {s.id for s in SIGNALE})
    if unbekannt:
        raise EingabeFehler(f'Config "projekt_woerter": unbekannte Signal-ID {", ".join(unbekannt)}')
    return tuple(
        replace(
            s,
            pfad_woerter=s.pfad_woerter + tuple(extra[s.id]),
            zeilen_woerter=s.zeilen_woerter + tuple(extra[s.id]),
        )
        if extra.get(s.id)
        else s
        for s in SIGNALE
    )


LINSEN_OBERFLAECHE = ["impeccable-audit", "web-design-guidelines", "accessibility"]
_REACT_IMPORT = re.compile(r"""(from\s+['"]react['"]|require\(\s*['"]react['"]\s*\))""")


def passt(pfad: str, muster: tuple[str, ...] | list[str]) -> bool:
    name = PurePosixPath(pfad).name
    return any(fnmatch.fnmatchcase(pfad, m) or fnmatch.fnmatchcase(name, m) for m in muster)


def ist_ignoriert(pfad: str, cfg: dict[str, Any]) -> bool:
    # Infra-Dateien (requirements*.txt) schlagen die *.txt-Ignorierregel.
    return passt(pfad, cfg.get("ignorieren", [])) and not passt(pfad, _INFRA)


def _ist_n8n(repo: Path, d: DateiDiff) -> bool:
    if not d.pfad.endswith(".json"):
        return False
    try:
        inhalt = (repo / d.pfad).read_text(encoding="utf-8", errors="replace")
    except OSError:
        inhalt = "\n".join(d.hinzu)
    return '"nodes"' in inhalt and '"connections"' in inhalt


_CAMEL_GRENZE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NICHT_ALNUM = re.compile(r"[^a-zA-Z0-9]+")
_ENV_LITERAL = re.compile(r"""['"]\.env['"]""")


def _tokenisiert(text: str) -> str:
    """Zerlegt Bezeichner in Wortteile (snake_case/camelCase) für Wortgrenzen-Matching."""
    mit_leerzeichen = _CAMEL_GRENZE.sub(" ", text)
    normalisiert = _NICHT_ALNUM.sub(" ", mit_leerzeichen).lower()
    return f" {normalisiert} "


def _zeilenwort_trifft(wort: str, tokens: str, roh_klein: str) -> bool:
    """Reine Bezeichner-Wörter (z.B. "token") nur an Wortgrenzen; Symbol-/Mehrwort-
    Muster (z.B. ".get(", "set_status") bleiben Teilstring-Treffer wie bisher."""
    if wort == ".env":
        return bool(_ENV_LITERAL.search(roh_klein))
    kern = _NICHT_ALNUM.sub(" ", wort).strip().lower()
    if not kern:
        return wort in roh_klein
    if kern != wort.strip().lower():
        return wort in roh_klein
    return f" {kern} " in tokens


def _pfadwort_trifft(wort: str, pfad_tokens: str, pfad_klein: str) -> bool:
    """Pfad-Wörter mit derselben Wortgrenzen-Logik wie Zeilen-Wörter; ".env" und
    andere Symbol-Muster bleiben Teilstring-Treffer im Pfad."""
    kern = _NICHT_ALNUM.sub(" ", wort).strip().lower()
    if not kern or kern != wort.strip().lower():
        return wort.lower() in pfad_klein
    # Präfix-Treffer je Pfad-Token (authentication, passwordless), nie Wortmitte (kontrolle).
    return any(t.startswith(kern) for t in pfad_tokens.split())


# String-Literale ('…', "…"); Kommentare (#, //) außerhalb von Literalen entfallen.
_STRING_LITERAL = re.compile(r""""(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""")
_KOMMENTAR = re.compile(r"#|//")
_ZUWEISUNG = re.compile(r"^\s*[A-Za-z_][\w.]*\s*(?::[^=]+)?=(?!=)")
_NUR_DATEN = re.compile(r"""^[\s()\[\]{},]*(?:(?:""|'')[\s()\[\]{},]*)+$""")
# Geheim-Wörter (tokenisiert): Datenzeilen damit behalten ihren Literal-Inhalt.
# ponytail: feste Liste, Katalogzeilen mit diesen Wörtern feuern "sicherheit" beim Editieren.
_GEHEIM = re.compile(r" (?:password|passwort|secret|token|apikey|api key|authorization|bearer|private key) ")


def _ohne_literale(zeile: str) -> str:
    """Literal-Inhalte durch Leerzeichen ersetzen (Länge bleibt), Kommentar entfernen."""

    def leer(m: re.Match[str]) -> str:
        quote = m.group(0)[0]
        return quote + " " * (len(m.group(0)) - 2) + quote

    blank = _STRING_LITERAL.sub(leer, zeile)
    kommentar = _KOMMENTAR.search(blank)
    return blank[: kommentar.start()] if kommentar else blank


def _code_anteil(zeile: str) -> str:
    """Text, gegen den Zeilen-Signale laufen: reine Datenzeilen (Signal-Kataloge)
    ohne Literal-Inhalt, außer sie enthalten Geheim-Wörter; alle anderen Zeilen mit
    Literal-Inhalt, stets ohne Kommentar."""
    blank = _ohne_literale(zeile)
    code = zeile[: len(blank)]
    zuweisung = _ZUWEISUNG.match(blank)
    rest = blank[zuweisung.end() :] if zuweisung else blank
    kompakt = _STRING_LITERAL.sub(lambda m: m.group(0)[0] * 2, rest)
    literale = len(_STRING_LITERAL.findall(rest))
    fortsetzung = zuweisung is None and literale >= 1 and "," in rest
    daten = _NUR_DATEN.match(kompakt) and (literale >= 2 or fortsetzung)
    if daten and not _GEHEIM.search(_tokenisiert(code)):
        return blank
    return code


def signal_trifft(sig: Signal, repo: Path, d: DateiDiff) -> bool:
    if sig.id == "n8n":
        return _ist_n8n(repo, d)
    if passt(d.pfad, sig.muster):
        return True
    pfad_klein = d.pfad.lower()
    pfad_tokens = _tokenisiert(d.pfad)
    if any(_pfadwort_trifft(w, pfad_tokens, pfad_klein) for w in sig.pfad_woerter):
        return True
    if sig.zeilen_woerter:
        roh_klein = "\n".join(d.hinzu).lower()
        code_klein = "\n".join(_code_anteil(z) for z in d.hinzu).lower()
        tokens = _tokenisiert(code_klein)
        if any(
            _ENV_LITERAL.search(roh_klein) if w == ".env" else _zeilenwort_trifft(w, tokens, code_klein)
            for w in sig.zeilen_woerter
        ):
            return True
    return sig.id == "react" and any(_REACT_IMPORT.search(z) for z in d.hinzu)


# ---------------------------------------------------------------- Route (§3, §4)


def _blast_radius(repo: Path, dateien: list[str]) -> int:
    """Größte Knotenzahl aus `graphify affected` je Datei; 0 wenn graphify fehlt."""
    exe = shutil.which("graphify")
    if not exe or not (repo / "graphify-out").is_dir():
        return 0
    groesste = 0
    frist = time.monotonic() + 5.0
    for pfad in dateien[:10]:
        rest = frist - time.monotonic()
        if rest <= 0:
            break
        knoten = PurePosixPath(pfad).stem
        try:
            aus = subprocess.run(
                [exe, "affected", knoten],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(rest, 0.1),
            ).stdout
        except (OSError, subprocess.TimeoutExpired) as fehler:
            LOG.warning("graphify übersprungen: %s", fehler)
            return groesste
        groesste = max(groesste, sum(1 for z in aus.splitlines() if z.startswith("- ")))
    return groesste


def fingerprint(sha: str, dateien: list[str], diffs: dict[str, DateiDiff]) -> str:
    teile = [sha, *sorted(dateien)] + ["\n".join(diffs[p].text) for p in sorted(dateien)]
    return hashlib.sha256("\n".join(teile).encode("utf-8")).hexdigest()[:16]


def datei_hash(diff: DateiDiff) -> str:
    """sha256 des Diff-Texts einer Datei — Grundlage für Teilmengen-Gültigkeit von Belegen."""
    return hashlib.sha256("\n".join(diff.text).encode("utf-8")).hexdigest()[:16]


#: Kopfzeilen, die zum Inhalt zählen (Modus-Wechsel, Binär) — index/---/+++ nicht (Format-abhängig).
_INHALT_KOPF = ("old mode ", "new mode ", "Binary files ")


def inhalt_hash(diff: DateiDiff) -> str:
    """sha256 über Hunk-Köpfe + Hunk-Zeilen (+ Modus/Binär-Kopf) — gleich für „untracked“ und
    „new file“ nach Commit, anders bei anderer Stelle im File. Ohne Inhalt → "" (nie Treffer)."""
    if diff.text and diff.text[0].startswith("untracked "):
        rumpf = diff.text[1:]
        if not rumpf:
            return ""
        zahl = "" if len(rumpf) == 1 else f",{len(rumpf)}"
        teile = [f"@@ -0,0 +1{zahl} @@", *rumpf]
    else:
        teile, im_hunk = [], False
        for zeile in diff.text:
            if zeile.startswith("@@"):
                im_hunk = True
                teile.append(zeile)
            elif im_hunk and not zeile.startswith("\\"):
                teile.append(zeile)
            elif not im_hunk and zeile.startswith(_INHALT_KOPF):
                teile.append(zeile)
    if not teile:
        return ""
    return hashlib.sha256("\n".join(teile).encode("utf-8")).hexdigest()[:16]


def beleg_pfad(repo: Path, sha: str, moment: str, fp: str = "") -> Path:
    """Mit fingerprint im Namen: parallele Sessions im selben Repo (gleiche sha, gleicher Moment,
    andere Dateien) überschreiben sich den Beleg nicht mehr. Ohne fp = alter Name (nur Lesen)."""
    zusatz = f"-{fp[:12]}" if fp else ""
    return repo / ".review" / f"beleg-{sha[:7]}-{moment}{zusatz}.json"


def _beleg_alter(pfad: Path) -> float:
    try:
        return pfad.stat().st_mtime
    except OSError:  # zwischen glob und stat gelöscht
        return 0.0


def belege_suchen(ordner: Path, sha_muster: str, moment: str) -> list[Path]:
    """Belege eines Moments (alter und neuer Name), jüngster zuerst."""
    treffer = {
        *ordner.glob(f"beleg-{sha_muster}-{moment}.json"),
        *ordner.glob(f"beleg-{sha_muster}-{moment}-*.json"),
    }
    return sorted(treffer, key=_beleg_alter, reverse=True)


#: Nur die jüngsten Belege durchsuchen — .review wächst je SHA×Moment, nichts räumt auf.
BELEG_SUCHE_MAX = 50


def beleg_nach_inhalt(
    repo: Path, moment: str, dateien: list[str], inhalt_hashes: dict[str, str]
) -> tuple[Path, dict[str, Any]] | None:
    """Jüngster Beleg desselben Moments, dessen inhalt_hashes alle Dateien gleich abdecken —
    unabhängig von HEAD. Commit des geprüften Codes verschiebt HEAD, die geprüften Hunks
    gegen dieselbe Basis bleiben aber identisch → kein zweites Review."""
    if not dateien or not inhalt_hashes:
        return None
    ordner = repo / ".review"
    if not ordner.is_dir():
        return None
    from review_beleg import beleg_inhalt_gleich  # spät: review_beleg importiert dieses Modul

    kandidaten = belege_suchen(ordner, "*", moment)
    for pfad in kandidaten[:BELEG_SUCHE_MAX]:
        try:
            beleg = json.loads(pfad.read_text(encoding="utf-8"))
            if isinstance(beleg, dict) and beleg_inhalt_gleich(beleg, dateien, inhalt_hashes):
                return pfad, beleg
        except (OSError, ValueError, TypeError) as fehler:
            LOG.warning("Beleg %s unbrauchbar: %s", pfad, fehler)
    return None


def _pruefer_liste(
    stufe: str, signale: list[dict[str, Any]], dateien: list[str], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    if stufe == "keine":
        return []
    modell = cfg.get("modelle", {}).get("pruefer", "sonnet")
    profil = cfg.get("profil", "schlank")
    liste: dict[str, dict[str, Any]] = {}

    def dazu(name: str, typ: str, auftrag: str, betroffen: list[str]) -> None:
        eintrag = liste.setdefault(
            name,
            {
                "name": name,
                "typ": typ,
                "modell": modell,
                "dateien": [],
                "auftraege": ["basis"],
            },
        )
        eintrag["dateien"] = sorted(set(eintrag["dateien"]) | set(betroffen))
        if auftrag not in eintrag["auftraege"]:
            eintrag["auftraege"].append(auftrag)
        if typ == "skill":
            eintrag["linsen"] = LINSEN_OBERFLAECHE

    if profil == "schlank" and stufe == "klein":
        dazu("general-purpose", "agent", "basis", dateien)
        for sig in signale:
            dazu("general-purpose", "agent", sig["id"], sig["dateien"])
        return list(liste.values())

    if profil == "schlank":
        dazu("general-purpose", "agent", "spec", dateien)
        nach_id = {s.id: s for s in SIGNALE}
        signal_ids = {sig["id"] for sig in signale}
        test_muster = nach_id["tests"].muster
        ohne_test = bool(dateien) and not any(passt(p, test_muster) for p in dateien)
        for sig in signale:
            if sig["id"] == "sicherheit":
                dazu("security-reviewer", "agent", "sicherheit", sig["dateien"])
            elif sig["id"] == "tests":
                dazu("pr-test-analyzer", "agent", "tests", sig["dateien"])
            else:
                dazu("general-purpose", "agent", sig["id"], sig["dateien"])
        if "tests" not in signal_ids and ohne_test:
            dazu("pr-test-analyzer", "agent", "tests", dateien)
        return list(liste.values())

    nur_python = all(p.endswith(".py") for p in dateien)
    dazu(
        "python-reviewer" if stufe == "klein" and nur_python else "code-reviewer",
        "agent",
        "basis",
        dateien,
    )
    if stufe != "klein":
        dazu("general-purpose", "agent", "spec", dateien)
    nach_id = {s.id: s for s in SIGNALE}
    for sig in signale:
        for name, typ in nach_id[sig["id"]].pruefer:
            dazu(name, typ, sig["id"], sig["dateien"])
    ergebnis = list(liste.values())
    return ergebnis[:2] if stufe == "klein" else ergebnis


def berechne_route(
    repo_arg: str | None,
    moment: str,
    base: str | None = None,
    staged: bool = False,
    files: list[str] | None = None,
) -> dict[str, Any]:
    if moment not in MOMENTE:
        raise EingabeFehler(f"moment unbekannt: {moment}")
    cfg = lade_config()
    start = Path(repo_arg) if repo_arg else Path.cwd()
    repo = repo_wurzel(start.resolve())
    ergebnis: dict[str, Any] = {
        "version": 1,
        "moment": moment,
        "repo": (repo or start.resolve()).as_posix(),
        "stufe": "keine",
        "modus": cfg.get("modus", "hinweis"),
        "sha": None,
        "fingerprint": "",
        "diff_zeilen": 0,
        "dateien": [],
        "signale": [],
        "datei_groesse": [],
        "pruefer": [],
        "builtin": [],
        "richter": False,
        "beleg": None,
        "beleg_gueltig": False,
        "fix_runde": 0,
        "diff_modus": {"staged": bool(staged), "base": base},
    }
    if repo is None:
        return ergebnis
    sha = kurz_sha(repo)
    diffs = diff_holen(repo, base, staged, files)
    relevant = sorted(p for p in diffs if not ist_ignoriert(p, cfg))
    zeilen = sum(diffs[p].zeilen for p in relevant)

    signale: list[dict[str, Any]] = []
    for sig in signale_fuer(cfg):
        treffer = [p for p in relevant if signal_trifft(sig, repo, diffs[p])]
        if treffer:
            signale.append({"id": sig.id, "klasse": sig.klasse, "dateien": treffer})
    hart = any(s["klasse"] == "hart" for s in signale)

    stufen_cfg = cfg.get("stufen", {})
    if not relevant:
        stufe = "keine"
    else:
        klein, mittel = stufen_cfg.get("klein", {}), stufen_cfg.get("mittel", {})
        if hart:
            stufe = "gross"
        elif zeilen <= klein.get("max_zeilen", 50) and len(relevant) <= klein.get("max_dateien", 3):
            stufe = "klein"
        elif zeilen <= mittel.get("max_zeilen", 400) and len(relevant) <= mittel.get("max_dateien", 15):
            stufe = "mittel"
        else:
            stufe = "gross"
        if stufe != "gross" and _blast_radius(repo, relevant) > 10:
            stufe = STUFEN[STUFEN.index(stufe) + 1]

    builtin: list[dict[str, Any]] = []
    if stufe in ("mittel", "gross"):
        builtin.append(
            {
                "befehl": "/code-review",
                "stufe": cfg.get("code_review_stufe", {}).get(stufe, "high"),
            }
        )
    if stufe != "keine" and any(s["id"] == "sicherheit" for s in signale):
        builtin.append({"befehl": "/security-review"})

    fp = fingerprint(sha, relevant, diffs)
    datei_hashes = {p: datei_hash(diffs[p]) for p in relevant if p in diffs}
    inhalt_hashes = {p: inhalt_hash(diffs[p]) for p in relevant if p in diffs}
    beleg = beleg_pfad(repo, sha, moment, fp)
    gueltig, fix_runde = False, 0
    from review_beleg import beleg_gueltig_pruefen  # spät: review_beleg importiert dieses Modul

    gleicher_stand = belege_suchen(repo / ".review", sha[:7], moment) if (repo / ".review").is_dir() else []
    # Eigener Name zuerst, dann jüngste Belege derselben sha (andere Sessions/alter Name).
    for kandidat in sorted(gleicher_stand, key=lambda k: k != beleg):
        try:
            alt = json.loads(kandidat.read_text(encoding="utf-8"))
            if not isinstance(alt, dict):
                raise ValueError("kein JSON-Objekt")
            if beleg_gueltig_pruefen(alt, sha, fp, relevant, datei_hashes):
                beleg, gueltig, fix_runde = kandidat, True, 0
                break
            # Fix-Runde nur aus Belegen, die eigene Dateien betreffen — nie aus fremden Sessions.
            eigene = set(relevant) & {d for d in alt.get("dateien", []) if isinstance(d, str)}
            if not fix_runde and eigene and beleg_status(alt.get("befunde", []), cfg) == "rot":
                fix_runde = int(alt.get("fix_runde", 0)) + 1
        except (OSError, ValueError, TypeError, AttributeError) as fehler:
            LOG.warning("Beleg %s unlesbar: %s", kandidat, fehler)
    if not gueltig:
        treffer = beleg_nach_inhalt(repo, moment, relevant, inhalt_hashes)
        if treffer is not None:
            beleg, gueltig = treffer[0], True  # fix_runde bleibt: kein Reset des Eskalationszählers
    ergebnis.update(
        stufe=stufe,
        sha=sha,
        fingerprint=fp,
        datei_hashes=datei_hashes,
        inhalt_hashes=inhalt_hashes,
        diff_zeilen=zeilen,
        dateien=relevant,
        signale=signale,
        datei_groesse=datei_groesse(repo, relevant, base, staged, cfg),
        pruefer=_pruefer_liste(stufe, signale, relevant, cfg),
        builtin=builtin,
        richter=stufe in ("mittel", "gross"),
        beleg=beleg.as_posix() if beleg.is_file() else None,
        beleg_gueltig=gueltig,
        fix_runde=fix_runde,
    )
    return ergebnis


def diff_argumente(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo")
    quelle = parser.add_mutually_exclusive_group()
    quelle.add_argument("--base")
    quelle.add_argument("--staged", action="store_true")
    parser.add_argument("--files", nargs="+")


# ---------------------------------------------------------------- Beleg-Status (§5)


def beleg_status(befunde: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    konf = cfg.get("konfidenz", {})
    unter, rot_ab = konf.get("verwerfen_unter", 70), konf.get("rot_ab", 85)
    zaehlen = [b for b in befunde if b.get("status") == "behalten" and b.get("konfidenz", 0) >= unter]
    if any(b.get("schwere") == "hoch" and b.get("konfidenz", 0) >= rot_ab for b in zaehlen):
        return "rot"
    return "gelb" if zaehlen else "gruen"
