"""Rot-Probe: geänderte Tests laufen gegen den alten Code und müssen rot werden.

Ein Skript entscheidet, ob ein Test die Änderung prüft — nicht die KI.
Ausgabe (stdout): {"befunde": [Befund nach VERTRAG.md §5], "hinweis": "<Text>"}.
Exit 0 immer, außer kaputte Eingabe (Exit 2). Infra-Fehler → kein Befund, nur hinweis.
Umgebungsprobleme (auch „Umgebung: Platz knapp“) beginnen im hinweis immer mit „Umgebung:“
und sind nie ein Befund/rot.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from _gemeinsam import logging_einrichten

LOG = logging.getLogger("review.rot_probe")
PRUEFER = "rot-probe"
STANDARD_TIMEOUT_S = 300.0
_UMGEBUNG = ("No module named pytest", "INTERNALERROR")
# Worktrees auf der Platte, nie in /tmp (RAM-Laufwerk, lief am 21.11. voll, #1493).
# Env-Variablen nur für Tests.
ARBEITSORDNER = Path(os.environ.get("ROT_PROBE_ORDNER") or Path.home() / ".cache" / "rot-probe")
MIN_FREI_MB = int(os.environ.get("ROT_PROBE_MIN_FREI_MB") or 2048)
# Länger als jeder Lauf (3 × Timeout); jünger bleibt stehen, auch ohne Besitzer-Datei.
VERWAIST_AB_S = 3600.0
BESITZER = "besitzer.pid"


def lebt(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def verwaiste_wegraeumen(ordner: Path, jetzt: float | None = None) -> list[Path]:
    """Löscht rp*-Ordner älter als VERWAIST_AB_S ohne lebenden Besitzer, dann worktree prune.

    Laufende Proben (PID lebt) oder junge Ordner (Anlegen läuft noch) bleiben immer stehen.
    """
    jetzt = time.time() if jetzt is None else jetzt
    weg: list[Path] = []
    for kandidat in sorted(ordner.glob("rp*")):
        besitzer = kandidat / BESITZER
        try:
            alter = jetzt - (besitzer if besitzer.is_file() else kandidat).stat().st_mtime
            pid = int(besitzer.read_text(encoding="utf-8").strip()) if besitzer.is_file() else 0
        except (OSError, ValueError) as fehler:
            LOG.warning("Rot-Probe-Ordner %s nicht lesbar: %s", kandidat, fehler)
            continue
        if alter < VERWAIST_AB_S or (pid > 0 and lebt(pid)):
            continue
        gitdatei = kandidat / "wt" / ".git"
        hauptrepo: Path | None = None
        try:
            zeile = gitdatei.read_text(encoding="utf-8").strip() if gitdatei.is_file() else ""
            if zeile.startswith("gitdir:"):
                hauptrepo = Path(zeile.split(":", 1)[1].strip()).parent.parent
        except OSError as fehler:
            LOG.warning("worktree-Verweis %s nicht lesbar: %s", gitdatei, fehler)
        shutil.rmtree(kandidat, ignore_errors=True)
        if hauptrepo is not None and hauptrepo.is_dir():
            subprocess.run(["git", "--git-dir", str(hauptrepo), "worktree", "prune"], capture_output=True, check=False)
        LOG.info("verwaisten Rot-Probe-Ordner entfernt: %s", kandidat)
        weg.append(kandidat)
    return weg


_TESTFUNKTION = re.compile(r"^\s*(?:async\s+)?def\s+test_|^\s*class\s+Test", re.MULTILINE)


def _hat_testnamen(pfad: str) -> bool:
    name = PurePosixPath(pfad).name
    return name.startswith("test_") or name.endswith("_test.py")


def ist_test(repo: Path, pfad: str) -> bool:
    """Einzige Regel „ist Testdatei“: Testname, außerhalb tests/ zusätzlich Testfunktion im Inhalt.

    Unter tests/ entscheidet der Name; alles andere dort ist Hilfsdatei (ist_hilfsdatei, #1533).
    Außerhalb reicht der Name nicht — Werkzeuge wie scripts/test_plaetze.py sind Code (#1454).
    Gelöschte/unlesbare Datei: Name entscheidet (kein Inhalt, kein Lauf, kein Befund).
    """
    if "tests" in PurePosixPath(pfad).parts[:-1]:
        return _hat_testnamen(pfad)
    if not _hat_testnamen(pfad):
        return False
    datei = repo / pfad
    if not datei.is_file():
        LOG.info("Testdatei %s fehlt (gelöscht?), Name entscheidet", pfad)
        return True
    try:
        return bool(_TESTFUNKTION.search(datei.read_text(encoding="utf-8", errors="replace")))
    except OSError as fehler:
        LOG.warning("Testdatei %s unlesbar (%s), Name entscheidet", pfad, fehler)
        return True


def ist_hilfsdatei(pfad: str) -> bool:
    """Test-Hilfsdatei (conftest.py, __init__.py, Helfer unter tests/): bleibt auf neuem Stand,
    wird selbst nicht beurteilt und löst allein kein „Code geändert ohne Test“ aus (#1533)."""
    return "tests" in PurePosixPath(pfad).parts[:-1] and not _hat_testnamen(pfad)


def ist_testlauf(repo: Path, pfad: str) -> bool:
    """Testdatei, die pytest selbst sammelt (conftest/Helfer unter tests/ nicht)."""
    return _hat_testnamen(pfad) and ist_test(repo, pfad)


def befund(datei: str, zeile: int, schwere: str, text: str) -> dict[str, Any]:
    return {
        "datei": datei,
        "zeile": zeile,
        "schwere": schwere,
        "konfidenz": 95,
        "pruefer": PRUEFER,
        "text": text,
        "status": "behalten",
    }


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def gruene_knoten(ausgabe: str) -> dict[str, list[str]]:
    """PASSED-Zeilen aus -rA → {datei: [testname, …]}."""
    knoten: dict[str, list[str]] = {}
    for treffer in re.finditer(r"^PASSED (\S+?)::(\S+)", ausgabe, re.MULTILINE):
        knoten.setdefault(treffer.group(1), []).append(treffer.group(2))
    return knoten


def sammeln(ziel: Path, dateien: list[str], timeout: float) -> set[str]:
    """Testknoten-IDs per --collect-only; fehlende Dateien liefern keine Knoten."""
    vorhanden = [d for d in dateien if (ziel / d).is_file()]
    if not vorhanden:
        return set()
    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *vorhanden,
            "--collect-only",
            "--continue-on-collection-errors",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ziel,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return set(re.findall(r"^(\S+?::\S+)$", lauf.stdout, re.MULTILINE))


def zeile_von(datei: Path, testname: str) -> int:
    name = testname.split("[")[0].split("::")[-1]
    try:
        for nr, text in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(rf"\s*(async\s+)?def {re.escape(name)}\b", text):
                return nr
    except OSError as fehler:
        LOG.warning("Zeile für %s nicht lesbar: %s", datei, fehler)
    return 1


def _ausfuehrbar(quelle: str) -> str:
    """AST ohne Kommentare, Leerzeilen und Docstrings — nur was beim Ausführen zählt."""
    baum = ast.parse(quelle)
    for knoten in ast.walk(baum):
        rumpf = getattr(knoten, "body", None)
        if (
            isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and rumpf
            and isinstance(rumpf[0], ast.Expr)
            and isinstance(rumpf[0].value, ast.Constant)
            and isinstance(rumpf[0].value.value, str)
        ):
            knoten.body = rumpf[1:] or [ast.Pass()]
    return ast.dump(baum)


def aendert_ausfuehrbar(repo: Path, base: str, pfad: str) -> bool:
    """True, wenn pfad gegenüber base ausführbar anders ist. Neu, gelöscht, unlesbar oder
    Syntaxfehler zählen vorsichtshalber als Änderung (dann läuft die Probe wie bisher, #326)."""
    alt = git(repo, "show", f"{base}:{pfad}")
    datei = repo / pfad
    if alt.returncode != 0 or not datei.is_file():
        return True
    try:
        return _ausfuehrbar(alt.stdout) != _ausfuehrbar(datei.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError) as fehler:
        LOG.info("Vergleich %s nicht möglich (%s), zählt als Änderung", pfad, fehler)
        return True


def prueft_nichts(datei: Path, name: str) -> bool:
    """True, wenn der Test (`TestA::test_x[1]`) nur Docstring/`pass`/`assert <Konstante>` enthält.

    Solche Tests werden unabhängig vom Code nie rot. Nicht lesbar/nicht gefunden → True (Befund bleibt scharf).
    ponytail: `assert 1 == 1` u. ä. zählt als Prüfung; nur wörtliche Konstanten gelten als leer.
    """
    try:
        rumpf: list[ast.stmt] = ast.parse(datei.read_text(encoding="utf-8")).body
    except (SyntaxError, ValueError, OSError) as fehler:
        LOG.info("Test %s nicht lesbar (%s), Befund bleibt", datei, fehler)
        return True
    for teil in name.split("[")[0].split("::"):
        treffer = [
            k for k in rumpf if isinstance(k, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and k.name == teil
        ]
        if not treffer:
            return True
        rumpf = treffer[-1].body
    return all(
        isinstance(z, ast.Pass)
        or (isinstance(z, ast.Expr) and isinstance(z.value, ast.Constant))
        or (isinstance(z, ast.Assert) and isinstance(z.test, ast.Constant))
        for z in rumpf
    )


def pruefen(repo: Path, base: str, dateien: list[str], timeout: float) -> tuple[list[dict[str, Any]], list[str]]:
    hinweise: list[str] = []
    fremd = [d for d in dateien if not d.endswith(".py")]
    if fremd:
        hinweise.append("ignoriert (nur Python/pytest): " + ", ".join(fremd))
    py = [d for d in dateien if d.endswith(".py")]
    tests = [d for d in py if ist_test(repo, d)]
    hilfen = [d for d in py if ist_hilfsdatei(d)]
    code = [d for d in py if d not in tests and d not in hilfen]
    laeufe = [d for d in tests if ist_testlauf(repo, d) and (repo / d).is_file()]

    if not laeufe:
        return [befund(d, 1, "mittel", "Code geändert ohne Test") for d in code], hinweise
    # #326: nur Kommentare/Docstrings im Code → alter Stand = neuer Stand, jeder Test bliebe grün.
    # Reine Test-Diffs laufen weiter (Platz/Timeout/Umgebung bleiben sichtbar); #401 lässt erst beim Befund aus.
    if code and not any(aendert_ausfuehrbar(repo, base, d) for d in code):
        hinweise.append("nicht beurteilbar: Diff ändert den geprüften Code nicht (" + ", ".join(code) + ")")
        return [], hinweise

    ARBEITSORDNER.mkdir(parents=True, exist_ok=True)
    verwaiste_wegraeumen(ARBEITSORDNER)
    git(repo, "worktree", "prune")
    frei_mb = shutil.disk_usage(ARBEITSORDNER).free // (1024 * 1024)
    if frei_mb < MIN_FREI_MB:
        hinweise.append(
            f"Umgebung: Platz knapp ({frei_mb} MB frei in {ARBEITSORDNER}, nötig {MIN_FREI_MB} MB), keine Aussage"
        )
        return [], hinweise

    lauf_ordner = Path(tempfile.mkdtemp(prefix="rp", dir=ARBEITSORDNER))
    (lauf_ordner / BESITZER).write_text(str(os.getpid()), encoding="utf-8")
    ziel = lauf_ordner / "wt"
    try:
        angelegt = git(repo, "worktree", "add", "--detach", str(ziel), base)
        if angelegt.returncode != 0:
            hinweise.append(f"Umgebung: worktree add fehlgeschlagen: {angelegt.stderr.strip()}")
            return [], hinweise
        try:
            alt_knoten = sammeln(ziel, laeufe, timeout)
        except subprocess.TimeoutExpired:
            hinweise.append(f"Timeout nach {timeout:g} s, keine Aussage")
            return [], hinweise
        for d in tests + hilfen:
            if (repo / d).is_file():
                (ziel / d).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo / d, ziel / d)
        try:
            neu_knoten = sammeln(ziel, laeufe, timeout) - alt_knoten
            lauf = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *laeufe,
                    "-q",
                    "-rA",
                    "--continue-on-collection-errors",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=ziel,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            hinweise.append(f"Timeout nach {timeout:g} s, keine Aussage")
            return [], hinweise
        ausgabe = lauf.stdout + lauf.stderr
        if lauf.returncode in (3, 4, 5) or any(m in ausgabe for m in _UMGEBUNG):
            hinweise.append(f"Umgebung: pytest Exit {lauf.returncode}, keine Aussage")
            return [], hinweise
        if lauf.returncode == 2:
            hinweise.append("rot durch Import-/Sammelfehler, nicht durch Assertion")
            return [], hinweise
        sammelfehler = set(re.findall(r"^ERROR (\S+?\.py)(?:\s|$)", ausgabe, re.MULTILINE))
        if sammelfehler:
            hinweise.append("rot durch Import-/Sammelfehler, nicht durch Assertion: " + ", ".join(sorted(sammelfehler)))
        befunde: list[dict[str, Any]] = []
        knoten = gruene_knoten(ausgabe)
        ohne_neue: list[str] = []
        ausgelassen: list[str] = []
        for d in laeufe:
            # ponytail: nur neue Knoten zählen; geänderte Bodies alter Tests bleiben ungeprüft.
            neu = {k.split("::", 1)[1] for k in neu_knoten if k.startswith(f"{d}::")}
            if d in sammelfehler:
                continue
            if not neu:
                ohne_neue.append(d)
                continue
            namen = [n for n in knoten.get(d, []) if n in neu]
            if set(namen) != neu:
                continue
            # #401: Diff nur aus Testdateien (keine Code-, Hilfs- oder Nicht-Python-Datei) → alter Stand =
            # neuer Stand, keine Aussage. Gemeldet werden dann nur Tests ohne jede Prüfung.
            # #1533: geänderte Hilfsdateien bleiben scharf.
            if not code and not hilfen and not fremd:
                namen = [n for n in namen if prueft_nichts(ziel / d, n)]
                if not namen:
                    ausgelassen.append(d)
                    continue
            zeile = zeile_von(ziel / d, namen[0]) if namen else 1
            liste = f" ({', '.join(namen)})" if namen else ""
            text = f"Test kann nicht rot werden: prüft die Änderung nicht{liste}"
            if hilfen:
                # #1533: Erkennung bleibt scharf, der Befund erklärt nur den möglichen Fehlalarm.
                text += (
                    f". Hilfsdatei unter tests/ mitgeändert ({', '.join(hilfen)}): Prüft der Test nur deren"
                    " Werte, ist das ein Fehlalarm — Richter prüft von Hand ohne diese Zeilen."
                )
            befunde.append(befund(d, zeile, "hoch", text))
        if ohne_neue:
            hinweise.append(
                "ohne neue Testknoten, nicht geprüft (geänderte Bodies alter Tests zählen nicht): "
                + ", ".join(ohne_neue)
            )
        if ausgelassen:
            hinweise.append("ausgelassen: nur Tests geändert, keine Aussage (" + ", ".join(ausgelassen) + ")")
        return befunde, hinweise
    finally:
        entfernt = git(repo, "worktree", "remove", "--force", str(ziel))
        if entfernt.returncode != 0:
            LOG.warning("worktree remove: %s", entfernt.stderr.strip())
        git(repo, "worktree", "prune")
        shutil.rmtree(lauf_ordner, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    logging_einrichten()
    parser = argparse.ArgumentParser(description="Rot-Probe: Tests gegen alten Code laufen lassen.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--timeout", type=float, default=STANDARD_TIMEOUT_S)
    args = parser.parse_args(argv)

    oben = git(Path(args.repo), "rev-parse", "--show-toplevel")
    if oben.returncode != 0:
        LOG.error("kein Git-Repo: %s", args.repo)
        return 2
    repo = Path(oben.stdout.strip())
    dateien = [Path(d).as_posix() for d in args.files]
    try:
        befunde, hinweise = pruefen(repo, args.base, dateien, args.timeout)
    except OSError as fehler:
        LOG.error("Rot-Probe Umgebungsfehler: %s", fehler)
        befunde, hinweise = [], [f"Umgebung: {fehler}"]
    ausgabe = {"befunde": befunde, "hinweis": "; ".join(hinweise)}
    sys.stdout.write(json.dumps(ausgabe, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
