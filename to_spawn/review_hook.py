"""Review-Hook (``review-hook/`` im Skill) nach ``~/.claude/hooks/review`` installieren (#214),
dazu das Review-Werkzeug (``review/``) nach ``~/.claude/review`` (#329 P2).

Die eine Stelle für die Überschreib-Regel, gilt für ``install.sh`` und ``install.ps1``:
eine vorhandene Datei wird nur ersetzt, wenn sie dem zuletzt installierten Stand entspricht
(Prüfsummen in ``.installiert.json`` im Ziel) oder ohne CR gleich der neuen ist. Weicht sie
ab, landet sie vorher unter ``<ziel>/../_alt/<zielname>-<zeit>/`` und der Bericht nennt sie —
nie still überschreiben, nie nachfragen. Dateien, die nur im Ziel liegen, bleiben unberührt.
Ziel-Dateien, die Links sind, werden nur gemeldet, nie durchgeschrieben; geschrieben wird atomar.

Eingespielt werden nur versionierte Dateien der Quelle (``git ls-files``); ohne git (eingespielter
Skill-Ordner) alle außer den in ``<quelle>/.gitignore`` genannten. Darum geben die Installer den Klon mit.

Beim Werkzeug kommt vorher (:func:`installiere_werkzeug`): Zeilen, die nur in lokal geänderten
Auftrags-Vorlagen stehen (bisher fest eingebaute Projektregeln), wandern in die private ``regeln.md``
(VERTRAG.md §2a); danach verliert eine lokale ``config.json``, was nur einen ausgelieferten Stand wiederholt.

Türen: :func:`installiere`, :func:`installiere_werkzeug`.
Aufruf: ``python -m to_spawn.review_hook [--ziel <ordner>] [--klon <skill-klon>]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

log = logging.getLogger(__name__)

QUELL_ORDNER = Path(__file__).resolve().parents[1] / "review-hook"
#: Review-Werkzeug (#329 P2): gleiche Regel, Ziel ``~/.claude/review``; Laufzeit-Daten dort bleiben.
WERKZEUG_QUELLE = Path(__file__).resolve().parents[1] / "review"
STAND_DATEI = ".installiert.json"
#: Private Projektregeln im Werkzeug-Ordner; gleicher Name wie ``REGELN_DATEI`` in ``review/_gemeinsam.py``.
REGELN_DATEI = "regeln.md"
#: Gleich ``_FALLS_REPO`` in ``review/_gemeinsam.py``; kein Import, ``install.ps1`` startet diese Datei direkt.
_FALLS_REPO = re.compile(r"^[\s>*-]*Falls Repo\s+([^:]+?)\s*:")
CONFIG_LOKAL = "config.json"
CONFIG_STANDARD = "config.standard.json"
_NIE = {"__pycache__", ".pytest_cache", ".git"}


def _pruefsumme(daten: bytes) -> str:
    """Prüfsumme ohne CR: CRLF und LF gelten als gleich."""
    return hashlib.sha256(daten.replace(b"\r", b"")).hexdigest()


def _git(quelle: Path, *args: str) -> str | None:
    """``git -C quelle …``; ``None``, wenn git fehlt oder ``quelle`` in keinem Repo liegt."""
    try:
        return subprocess.run(
            ["git", "-C", str(quelle), *args], capture_output=True, check=True, text=True, encoding="utf-8"
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _ignoriert(rel: str, muster: list[str]) -> bool:
    """Einfache ``.gitignore``-Muster: ``name/`` = Ordner auf jeder Ebene, sonst Name oder Pfad per fnmatch.

    ponytail: keine Negation (``!``) und kein ``**``; reicht für ``review/.gitignore``, sonst echtes git nutzen.
    """
    teile = rel.split("/")
    for m in muster:
        if m.endswith("/"):
            if m.rstrip("/") in teile[:-1]:
                return True
        elif fnmatch(teile[-1], m) or fnmatch(rel, m.lstrip("/")):
            return True
    return False


def _quell_dateien(quelle: Path) -> list[str]:
    """Nur versionierte Dateien; ohne git alle außer den in ``quelle/.gitignore`` genannten."""
    versioniert = _git(quelle, "ls-files", "-z", "--", ".")
    if versioniert:
        kandidaten = [r for r in versioniert.split("\0") if r and (quelle / r).is_file()]
    else:  # Kein Repo (eingespielter Skill-Ordner) oder dort nichts versioniert.
        try:
            zeilen = (quelle / ".gitignore").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            zeilen = []
        muster = [z.strip() for z in zeilen if z.strip() and not z.lstrip().startswith("#")]
        kandidaten = [
            p.relative_to(quelle).as_posix()
            for p in quelle.rglob("*")
            if p.is_file() and not _ignoriert(p.relative_to(quelle).as_posix(), muster)
        ]
    return sorted(r for r in kandidaten if not _NIE.intersection(r.split("/")))


def _lade_stand(ziel: Path) -> dict[str, str]:
    try:
        stand = json.loads((ziel / STAND_DATEI).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return stand if isinstance(stand, dict) else {}  # Kaputter Stand: jede abweichende Datei wird gesichert.


def _sicherungs_ordner(ziel: Path, zeit: str | None) -> Path:
    sicherung = ziel.parent / "_alt" / f"{ziel.name}-{zeit or datetime.now().strftime('%Y%m%d-%H%M%S')}"
    while sicherung.exists():
        sicherung = sicherung.with_name(sicherung.name + "-1")
    return sicherung


def installiere(ziel: Path, quelle: Path = QUELL_ORDNER, zeit: str | None = None) -> list[str]:
    """Bringt ``ziel`` auf den Stand von ``quelle``; gibt die Berichtszeilen zurück."""
    stand_pfad = ziel / STAND_DATEI
    alt_stand = _lade_stand(ziel)
    sicherung = _sicherungs_ordner(ziel, zeit)
    bericht: list[str] = []
    neu_stand: dict[str, str] = {}
    ziel_echt = ziel.resolve()
    for rel in _quell_dateien(quelle):
        neu = (quelle / rel).read_bytes()
        datei = ziel / rel
        if datei.is_symlink() or not datei.resolve().is_relative_to(ziel_echt):
            # Link (auch tot oder über einen Unter-Ordner): nie durchschreiben, nur melden.
            bericht.append(f"abweichend, Link nicht angefasst: {rel}")
            continue
        if datei.is_dir():
            bericht.append(f"abweichend, Ordner nicht angefasst: {rel}")
            continue
        neu_stand[rel] = _pruefsumme(neu)
        if datei.exists():
            alt = datei.read_bytes()
            if alt == neu:
                continue
            summe = _pruefsumme(alt)
            if summe not in (neu_stand[rel], alt_stand.get(rel)):
                (sicherung / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(datei, sicherung / rel)
                bericht.append(f"abweichend, gesichert: {rel} → {sicherung / rel}")
            else:
                bericht.append(f"aktualisiert: {rel}")
        else:
            datei.parent.mkdir(parents=True, exist_ok=True)
            bericht.append(f"angelegt: {rel}")
        _schreibe_atomar(datei, neu)
    if neu_stand != alt_stand:
        _schreibe_atomar(stand_pfad, (json.dumps(neu_stand, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return bericht


def installiere_werkzeug(ziel: Path, quelle: Path = WERKZEUG_QUELLE, zeit: str | None = None) -> list[str]:
    """Wie :func:`installiere`, vorher Projektregeln übernehmen, danach lokale Config entschlacken.

    Entschlackt wird erst nach dem Einspielen: bricht es ab, bleibt die lokale Config unberührt.
    """
    bericht = regeln_uebernehmen(ziel, quelle) + installiere(ziel, quelle, zeit)
    return bericht + config_entschlacken(ziel, quelle, zeit)


def _abschnitt_bereich(zeilen: list[str], name: str) -> tuple[int, int] | None:
    """``(kopf, ende)`` des ersten Abschnitts ``## <name>``; ``ende`` = nächste ``## ``-Zeile oder Dateiende."""
    kopf = next((i for i, z in enumerate(zeilen) if z.strip() == f"## {name}"), None)
    if kopf is None:
        return None
    ende = next((i for i in range(kopf + 1, len(zeilen)) if zeilen[i].startswith("## ")), len(zeilen))
    return kopf, ende


def regeln_uebernehmen(ziel: Path, quelle: Path) -> list[str]:
    """Zeilen, die nur in einer lokal geänderten Vorlage ``ziel/auftraege/<name>.md`` stehen, kommen in den
    Abschnitt ``## <name>`` von ``ziel/regeln.md``, bevor :func:`installiere` die Vorlage ersetzt.

    Lokal geändert = weder gleich der neuen Vorlage noch dem zuletzt installierten Stand (wie die Sicherungs-Regel).
    Vorlagen, die frühere Läufe unter ``../_alt/<zielname>-*/auftraege`` gesichert haben, zählen nur für einen
    Abschnitt, den ``regeln.md`` noch nicht hat (erste Übernahme, auch bei fehlender Datei): steht er, hat ein
    früherer Lauf sie schon übernommen, und Handänderungen dort (gelöscht, mit Präfix versehen) bleiben.
    Dubletten zählen je Abschnitt, auch als ``Falls Repo <name>: <derselbe Text>``; ein vorhandener Abschnitt
    wächst an seinem Ende, sonst kommt ein neuer ans Dateiende. Alle übrigen Zeilen der Datei bleiben unberührt.
    Ohne ``.installiert.json`` landen auch Zeilen älterer ausgelieferter Fassungen dort: lieber zu viel als verloren.

    ponytail: wer einen ganzen Abschnitt löscht, bekommt ihn aus ``_alt`` zurück; dann die Sicherung mit löschen.
    """
    alt_stand = _lade_stand(ziel)
    regeln = ziel / REGELN_DATEI
    bestand = regeln.read_text(encoding="utf-8") if regeln.is_file() else ""
    zeilen = bestand.splitlines()
    kandidaten = [(alt, True) for alt in sorted((ziel / "auftraege").glob("*.md"))]
    kandidaten += [(alt, False) for alt in sorted(ziel.parent.glob(f"_alt/{ziel.name}-*/auftraege/*.md"))]
    neu_je: dict[str, list[str]] = {}
    for alt, installiert in kandidaten:
        vorlage = quelle / "auftraege" / alt.name
        bereich = _abschnitt_bereich(zeilen, alt.stem)
        if alt.is_symlink() or not vorlage.is_file() or (bereich and not installiert):
            continue  # Sicherung, deren Abschnitt schon steht: übernommen, Handänderungen dort gewinnen.
        alt_bytes, neu_bytes = alt.read_bytes(), vorlage.read_bytes()
        bekannt = (_pruefsumme(neu_bytes), alt_stand.get(f"auftraege/{alt.name}") if installiert else None)
        if _pruefsumme(alt_bytes) in bekannt:
            continue
        neu_zeilen = set(neu_bytes.decode("utf-8").splitlines())
        abschnitt = zeilen[bereich[0] : bereich[1]] if bereich else []
        schon_da = set(abschnitt) | {z[t.end() :].strip() for z in abschnitt if (t := _FALLS_REPO.match(z))}
        eigene = neu_je.setdefault(alt.stem, [])
        for z in alt_bytes.decode("utf-8").splitlines():
            if z.strip() and z not in neu_zeilen and not {z, z.strip()} & schon_da and z not in eigene:
                eigene.append(z)
    neu_je = {name: eigene for name, eigene in neu_je.items() if eigene}
    if not neu_je:
        return []
    if not zeilen:
        zeilen = [
            "# Projektregeln (privat, nie im öffentlichen Repo; VERTRAG.md §2a)",
            "",
            "Je Abschnitt `## <auftrag>`; eine Zeile `Falls Repo <name>: …` gilt nur in diesem Repo.",
        ]
    for name, eigene in neu_je.items():
        bereich = _abschnitt_bereich(zeilen, name)
        if bereich is None:
            zeilen += ["", f"## {name}", "", *eigene]
            continue
        kopf, ende = bereich
        while ende > kopf + 1 and not zeilen[ende - 1].strip():
            ende -= 1  # vor die Leerzeilen, die den Abschnitt vom nächsten trennen
        zeilen[ende:ende] = eigene
    _schreibe_atomar(regeln, ("\n".join(zeilen) + "\n").encode("utf-8"))
    anzahl = sum(len(e) for e in neu_je.values())
    return [f"Projektregeln übernommen: {anzahl} Zeile(n) aus lokal geänderten Vorlagen → {regeln}"]


def _ausgelieferte_configs(quelle: Path) -> list[dict]:
    """Alle je ausgelieferten Config-Stände: der aktuelle Standard plus der git-Verlauf beider Dateinamen."""
    texte = [(quelle / CONFIG_STANDARD).read_text(encoding="utf-8")]
    for sha in (_git(quelle, "log", "--format=%H", "--", CONFIG_LOKAL, CONFIG_STANDARD) or "").split():
        texte += [t for name in (CONFIG_LOKAL, CONFIG_STANDARD) if (t := _git(quelle, "show", f"{sha}:./{name}"))]
    stande = []
    for text in texte:
        try:
            stande.append(json.loads(text))
        except ValueError:
            continue  # Kaputter Alt-Stand im Verlauf vergleicht sich mit nichts.
    return stande


def config_entschlacken(ziel: Path, quelle: Path, zeit: str | None = None) -> list[str]:
    """Lokale ``config.json`` verdeckt sonst künftige Standard-Änderungen still (Mischung oberste Ebene).

    Gleicht sie einem ausgelieferten Stand → nach ``_alt/``. Sonst fallen Schlüssel weg, deren Wert dem
    aktuellen Standard gleicht; echte Abweichungen bleiben. Das Original liegt immer vorher in ``_alt/``.
    """
    lokal_pfad = ziel / CONFIG_LOKAL
    if lokal_pfad.is_symlink() or not lokal_pfad.is_file() or not (quelle / CONFIG_STANDARD).is_file():
        return []
    try:
        lokal = json.loads(lokal_pfad.read_text(encoding="utf-8"))
    except ValueError as fehler:
        return [f"lokale Config unlesbar, nicht angefasst: {lokal_pfad} ({fehler})"]
    stande = _ausgelieferte_configs(quelle)
    if not isinstance(lokal, dict) or not stande or not isinstance(stande[0], dict):
        return []
    rest = {k: v for k, v in lokal.items() if k not in stande[0] or stande[0][k] != v}
    if rest == lokal and lokal not in stande:
        return []
    sicherung = _sicherungs_ordner(ziel, zeit)
    sicherung.mkdir(parents=True)
    shutil.copy2(lokal_pfad, sicherung / CONFIG_LOKAL)
    if lokal in stande or not rest:
        lokal_pfad.unlink()
        return [f"lokale Config glich ausgeliefertem Stand, entfernt: {CONFIG_LOKAL} → {sicherung / CONFIG_LOKAL}"]
    _schreibe_atomar(lokal_pfad, (json.dumps(rest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return [f"lokale Config entschlackt auf {sorted(rest)}: {CONFIG_LOKAL} (Original {sicherung / CONFIG_LOKAL})"]


def _schreibe_atomar(datei: Path, daten: bytes) -> None:
    """Erst Temp-Datei im selben Ordner, dann ``os.replace``: der laufende Hook liest nie halbe Dateien.

    Die Temp-Datei entsteht neu und exklusiv (``mkstemp``), ein dort liegender Link wird nie durchgeschrieben.
    """
    fd, name = tempfile.mkstemp(dir=datei.parent, prefix=f".{datei.name}.", suffix=".neu")
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(daten)
        if datei.exists():
            shutil.copymode(datei, temp)
        else:
            os.chmod(temp, 0o666 & ~_umask())  # mkstemp legt 0600 an; neue Datei wie write_bytes
        os.replace(temp, datei)
    finally:
        temp.unlink(missing_ok=True)


def _umask() -> int:
    alt = os.umask(0)
    os.umask(alt)
    return alt


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Review-Hook nach ~/.claude/hooks/review installieren")
    parser.add_argument("--ziel", type=Path, default=Path.home() / ".claude" / "hooks" / "review")
    parser.add_argument("--klon", type=Path, help="Skill-Klon (git): Dateiliste nur aus versionierten Dateien")
    args = parser.parse_args(argv)
    basis = args.klon or QUELL_ORDNER.parent
    for name, ziel, quelle, einspielen in (
        ("Review-Hook", args.ziel, basis / QUELL_ORDNER.name, installiere),
        ("Review-Werkzeug", Path.home() / ".claude" / "review", basis / WERKZEUG_QUELLE.name, installiere_werkzeug),
    ):
        try:
            bericht = einspielen(ziel, quelle)
        except (OSError, ValueError) as fehler:
            log.error("[!!] %s nicht installiert (%s): %s", name, ziel, fehler)
            return 2
        for zeile in bericht:
            log.info("%s %s", name, zeile)
        log.info("%s installiert: %s (%s)", name, ziel, f"{len(bericht)} Änderung(en)" if bericht else "schon aktuell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
