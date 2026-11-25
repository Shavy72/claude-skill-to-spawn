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


_REINE_IMPORT_WURZELN = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "operator",
        "pytest",
        "re",
        "statistics",
        "string",
        "types",
        "typing",
        "unicodedata",
        "uuid",
    }
)
_UNREINE_BUILTINS = frozenset({"__import__", "breakpoint", "compile", "eval", "exec", "input", "open"})


def _test_knoten(
    baum: ast.Module, name: str
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, list[ast.ClassDef]] | None:
    """Löst einen pytest-Knotennamen (`TestA::test_x[1]`) zu (Funktion, umschließende Klassen) auf."""
    teile = name.split("[")[0].split("::")
    rumpf: list[ast.stmt] = baum.body
    klassen: list[ast.ClassDef] = []
    for teil in teile[:-1]:
        treffer = [k for k in rumpf if isinstance(k, ast.ClassDef) and k.name == teil]
        if not treffer:
            return None
        klasse = treffer[-1]
        klassen.append(klasse)
        rumpf = klasse.body
    treffer_fn = [
        k for k in rumpf if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)) and k.name == teile[-1]
    ]
    if not treffer_fn:
        return None
    return treffer_fn[-1], klassen


def _rumpf_prueft_nichts(rumpf: list[ast.stmt]) -> bool:
    return all(
        isinstance(z, ast.Pass)
        or (isinstance(z, ast.Expr) and isinstance(z.value, ast.Constant))
        or (isinstance(z, ast.Assert) and isinstance(z.test, ast.Constant))
        for z in rumpf
    )


def prueft_nichts(datei: Path, name: str) -> bool:
    """True, wenn der Test (`TestA::test_x[1]`) nur Docstring/`pass`/`assert <Konstante>` enthält.

    Solche Tests werden unabhängig vom Code nie rot. Nicht lesbar/nicht gefunden → True (Befund bleibt scharf).
    ponytail: `assert 1 == 1` u. ä. zählt als Prüfung; nur wörtliche Konstanten gelten als leer.
    """
    try:
        baum = ast.parse(datei.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError) as fehler:
        LOG.info("Test %s nicht lesbar (%s), Befund bleibt", datei, fehler)
        return True
    gefunden = _test_knoten(baum, name)
    if gefunden is None:
        return True
    return _rumpf_prueft_nichts(gefunden[0].body)


def _ist_testmodul(repo: Path, datei: Path, modul: str, level: int) -> bool:
    """True, wenn das importierte Modul im Repo nachweislich eine Testdatei (``ist_test``) ist."""
    teile = [t for t in modul.split(".") if t]
    if not teile:
        return False
    rel_pfad = Path(*teile).with_suffix(".py")
    kandidaten: list[Path] = []
    if level > 0:
        basis = datei.parent
        for _ in range(level - 1):
            basis = basis.parent
        kandidaten.append(basis / rel_pfad)
    else:
        kandidaten.extend((datei.parent / rel_pfad, repo / rel_pfad))
    repo_aufgeloest = repo.resolve()
    for kandidat in kandidaten:
        try:
            aufgeloest = kandidat.resolve()
            rel = aufgeloest.relative_to(repo_aufgeloest).as_posix()
        except ValueError:
            continue
        if aufgeloest.is_file() and ist_test(repo, rel):
            return True
    return False


def _import_klassifizieren(
    knoten: ast.Import | ast.ImportFrom, repo: Path, datei: Path
) -> tuple[set[str], set[str], bool]:
    """Ordnet importierte Namen in (Produktiv-/Fremd-Code, Testmodul-Daten, Stern-Import)."""
    code_namen: set[str] = set()
    test_namen: set[str] = set()
    stern = False
    if isinstance(knoten, ast.Import):
        for alias in knoten.names:
            lokal = alias.asname or alias.name.split(".")[0]
            wurzel = alias.name.split(".")[0]
            if wurzel in _REINE_IMPORT_WURZELN:
                continue
            if _ist_testmodul(repo, datei, alias.name, 0):
                test_namen.add(lokal)
            else:
                code_namen.add(lokal)
        return code_namen, test_namen, stern
    if knoten.level == 0:
        mod = knoten.module or ""
        if mod.split(".")[0] in _REINE_IMPORT_WURZELN:
            return code_namen, test_namen, stern
        ist_test_mod = _ist_testmodul(repo, datei, mod, 0)
        for alias in knoten.names:
            if alias.name == "*":
                stern = True
            elif ist_test_mod:
                test_namen.add(alias.asname or alias.name)
            else:
                code_namen.add(alias.asname or alias.name)
        return code_namen, test_namen, stern
    for alias in knoten.names:
        if alias.name == "*":
            stern = True
            continue
        ziel_mod = knoten.module if knoten.module is not None else alias.name
        if _ist_testmodul(repo, datei, ziel_mod, knoten.level):
            test_namen.add(alias.asname or alias.name)
        else:
            code_namen.add(alias.asname or alias.name)
    return code_namen, test_namen, stern


def _hat_unsicheren_import(knoten: ast.AST, repo: Path, datei: Path) -> bool:
    for kind in ast.walk(knoten):
        if isinstance(kind, (ast.Import, ast.ImportFrom)):
            c_namen, _t_namen, stern = _import_klassifizieren(kind, repo, datei)
            if c_namen or stern:
                return True
    return False


def _parametrize_namen(dekoratoren: list[ast.expr]) -> set[str]:
    namen: set[str] = set()
    for dek in dekoratoren:
        if not isinstance(dek, ast.Call):
            continue
        fn = dek.func
        ist_param = (isinstance(fn, ast.Attribute) and fn.attr == "parametrize") or (
            isinstance(fn, ast.Name) and fn.id == "parametrize"
        )
        if not ist_param:
            continue
        arg: ast.expr | None = (
            dek.args[0] if dek.args else next((k.value for k in dek.keywords if k.arg == "argnames"), None)
        )
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            namen |= {p.strip() for p in arg.value.split(",") if p.strip()}
        elif isinstance(arg, (ast.List, ast.Tuple)):
            namen |= {
                e.value.strip()
                for e in arg.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.strip()
            }
    return namen


def _parameter_namen(fn: ast.FunctionDef | ast.AsyncFunctionDef, ist_methode: bool) -> list[str]:
    args = fn.args
    alle = [*(a.arg for a in args.posonlyargs), *(a.arg for a in args.args)]
    ist_statisch = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in fn.decorator_list)
    if ist_methode and not ist_statisch and alle:
        alle = alle[1:]
    alle.extend(a.arg for a in args.kwonlyargs)
    if args.vararg:
        alle.append(args.vararg.arg)
    if args.kwarg:
        alle.append(args.kwarg.arg)
    return alle


def _freie_namen(knoten: ast.AST) -> set[str]:
    """Alle geladenen Namen in ``knoten`` ohne lokal gebundene Parameter/Variablen."""
    geladen: set[str] = set()
    lokal: set[str] = set()
    global_namen: set[str] = set()
    for kind in ast.walk(knoten):
        if isinstance(kind, ast.Name):
            if isinstance(kind.ctx, ast.Load):
                geladen.add(kind.id)
            elif isinstance(kind.ctx, ast.Store):
                lokal.add(kind.id)
        elif isinstance(kind, ast.Global):
            global_namen.update(kind.names)
        elif isinstance(kind, ast.arg):
            lokal.add(kind.arg)
        elif (isinstance(kind, ast.ExceptHandler) and kind.name) or (
            isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and kind is not knoten
        ):
            lokal.add(kind.name)
    return geladen - (lokal - global_namen)


def _zuweisung_eintragen(defs: dict[str, list[ast.AST]], anw: ast.stmt) -> None:
    if isinstance(anw, ast.Assign):
        for ziel_knoten in anw.targets:
            for kind in ast.walk(ziel_knoten):
                if isinstance(kind, ast.Name) and isinstance(kind.ctx, ast.Store):
                    defs.setdefault(kind.id, []).append(anw.value)
    elif isinstance(anw, (ast.AnnAssign, ast.AugAssign)) and isinstance(anw.target, ast.Name) and anw.value is not None:
        defs.setdefault(anw.target.id, []).append(anw.value)


def ohne_code_aufruf(repo: Path, datei: Path, name: str) -> bool:
    """True, wenn der Test (`TestA::test_x[1]`) nur Testdaten prüft und keinen Produktiv-Code aufruft (#390).

    Vorsichtiger Rückfall: unlesbar, nicht gefunden, Fixture-Parameter, Stern-Import, IO/Subprocess,
    fehlender Testdaten-Bezug oder fehlendes ``assert`` → False (Befund bleibt scharf).
    """
    try:
        baum = ast.parse(datei.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError) as fehler:
        LOG.info("Test %s nicht lesbar (%s), Befund bleibt", datei, fehler)
        return False
    gefunden = _test_knoten(baum, name)
    if gefunden is None:
        return False
    fn, klassen = gefunden
    if _rumpf_prueft_nichts(fn.body):
        return False

    dekoratoren = [*fn.decorator_list, *(d for k in klassen for d in k.decorator_list)]
    param_namen = _parametrize_namen(dekoratoren)
    if any(p not in param_namen for p in _parameter_namen(fn, bool(klassen))):
        return False

    imports_code: set[str] = set()
    imports_test: set[str] = set()
    modul_defs: dict[str, list[ast.AST]] = {}
    for anw in baum.body:
        if isinstance(anw, (ast.Import, ast.ImportFrom)):
            c_namen, t_namen, stern = _import_klassifizieren(anw, repo, datei)
            if stern:
                return False
            imports_code |= c_namen
            imports_test |= t_namen
        elif isinstance(anw, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            modul_defs.setdefault(anw.name, []).append(anw)
        elif isinstance(anw, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            _zuweisung_eintragen(modul_defs, anw)
        elif (
            isinstance(anw, ast.Expr)
            and isinstance(anw.value, ast.Call)
            and isinstance(anw.value.func, ast.Attribute)
            and isinstance(anw.value.func.value, ast.Name)
        ):
            modul_defs.setdefault(anw.value.func.value.id, []).append(anw.value)

    start_knoten: list[ast.AST] = [fn, *dekoratoren]
    klassen_defs: dict[str, list[ast.AST]] = {}
    erster = ""
    if klassen:
        klasse = klassen[-1]
        start_knoten.extend(klasse.bases)
        for anw in klasse.body:
            if isinstance(anw, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if anw.name in {"setUp", "setUpClass", "setup_method", "setup_class"}:
                    return False
                klassen_defs.setdefault(anw.name, []).append(anw)
            elif isinstance(anw, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                _zuweisung_eintragen(klassen_defs, anw)
        ist_statisch = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in fn.decorator_list)
        pos_args = fn.args.posonlyargs or fn.args.args
        if not ist_statisch and pos_args:
            erster = pos_args[0].arg

    if any(_hat_unsicheren_import(k, repo, datei) for k in start_knoten):
        return False

    start_namen: set[str] = set()
    for k in start_knoten:
        start_namen |= _freie_namen(k)

    hat_klassen_daten = False
    if erster:
        for kind in ast.walk(fn):
            if isinstance(kind, ast.Attribute) and isinstance(kind.value, ast.Name) and kind.value.id == erster:
                if kind.attr not in klassen_defs:
                    return False
                hat_klassen_daten = True
                for def_k in klassen_defs[kind.attr]:
                    if _hat_unsicheren_import(def_k, repo, datei):
                        return False
                    start_namen |= _freie_namen(def_k)

    if not hat_klassen_daten and not (start_namen & (set(modul_defs) | imports_test)):
        return False

    hat_assert = any(isinstance(k, ast.Assert) for k in ast.walk(fn))
    besucht: set[str] = set()
    offen = list(start_namen)
    while offen:
        sym = offen.pop()
        if sym in besucht:
            continue
        besucht.add(sym)
        if sym in imports_code or sym in _UNREINE_BUILTINS:
            return False
        for def_k in modul_defs.get(sym, ()):
            if _hat_unsicheren_import(def_k, repo, datei):
                return False
            if isinstance(def_k, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(k, ast.Assert) for k in ast.walk(def_k)
            ):
                hat_assert = True
            offen.extend(_freie_namen(def_k))
    return hat_assert


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
        ohne_code: list[str] = []
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
            else:
                # #390: reine Testdaten-/Zähl-Tests ohne Produktiv-Code-Aufruf können gegen alten Code
                # bauartbedingt nicht rot werden → nicht prüfbar statt Befund.
                rest: list[str] = []
                for n in namen:
                    if ohne_code_aufruf(ziel, ziel / d, n):
                        ohne_code.append(f"{d}::{n}")
                    else:
                        rest.append(n)
                namen = rest
                if not namen:
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
        if ohne_code:
            hinweise.append("nicht prüfbar: Test ohne Code-Aufruf (" + ", ".join(ohne_code) + ")")
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
