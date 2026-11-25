"""Test-Schutz-Prüfung per AST-Diff (#794, Teil von #580, Beschluss E19).

Analysiert den git-Diff geänderter Python-Testdateien (Vorbild AST-Vergleich aus
``review/rot_probe.py``) plattformübergreifend unter Linux und Windows:

- Zählt gelöschte Testdateien, entfernte Testfunktionen und entfernte Prüfungen
  (``assert``, ``pytest.raises``/``pytest.warns``, ``unittest``-/Mock-``assert*``).
- Erkennt Entschärfungen: neu hinzugefügte ``skip``/``xfail``-Marker, triviale
  Konstanten-Asserts (``assert True``), ``or``-Aufweichungen, reduzierte
  ``@pytest.mark.parametrize``-Fälle und verbreiterte ``pytest.raises(Exception)``.
- Erkennt geänderte Grenzwerte in Tests: geänderte Zahlenwerte oder Vergleichsoperatoren
  in Assertions, ``pytest.approx``-Toleranzen sowie geänderte numerische Konstanten
  auf Modul- oder Test-Ebene.

Normaler Diff (Code-Änderung, neue Tests, zusätzliche Asserts, Kommentare/Formatierung,
neue Parametrize-Fälle) bleibt grün (Exit 0). Verstöße ergeben rot (Exit 1),
git-/Aufruf-Fehler ergeben Exit 2.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import logging
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from to_spawn import prozessbaum

log = logging.getLogger("to_spawn.pruef.test_schutz")

EXIT_GRUEN = 0
EXIT_ROT = 1
EXIT_FEHLER = 2

_TESTFUNKTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_|^\s*class\s+Test", re.MULTILINE)
_DEAKTIVIERUNG_NAMEN = frozenset({"skip", "skipif", "xfail", "skipIf", "skipUnless", "expectedFailure"})
_DEAKTIVIERUNG_AUFRUFE = frozenset({"skip", "xfail", "skipTest"})
_RAISES_METHODEN = frozenset(
    {"raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"}
)
_BREITE_AUSNAHMEN = frozenset({"Exception", "BaseException"})


@dataclass(frozen=True)
class Verstoss:
    """Einzelner Test-Schutz-Verstoß."""

    datei: str
    art: str
    test: str
    zeile: int
    text: str
    alt: str = ""
    neu: str = ""

    def als_dict(self) -> dict[str, Any]:
        daten: dict[str, Any] = {
            "datei": self.datei,
            "art": self.art,
            "test": self.test,
            "zeile": self.zeile,
            "text": self.text,
        }
        if self.alt:
            daten["alt"] = self.alt
        if self.neu:
            daten["neu"] = self.neu
        return daten

    def kurz(self) -> str:
        ort = f"{self.datei}:{self.zeile}"
        if self.test:
            ort = f"{ort} ({self.test})"
        detail = f" [{self.alt} → {self.neu}]" if self.alt and self.neu else ""
        return f"- [{self.art}] {ort}: {self.text}{detail}"


@dataclass(frozen=True)
class Zaehler:
    """Zählwerte eines Test-Schutz-Laufs."""

    dateien_geprueft: int = 0
    entfernte_tests: int = 0
    entfernte_asserts: int = 0
    entfernte_raises: int = 0
    entfernte_pruefungen: int = 0
    geaenderte_grenzwerte: int = 0

    def als_dict(self) -> dict[str, int]:
        return {
            "dateien_geprueft": self.dateien_geprueft,
            "entfernte_tests": self.entfernte_tests,
            "entfernte_asserts": self.entfernte_asserts,
            "entfernte_raises": self.entfernte_raises,
            "entfernte_pruefungen": self.entfernte_pruefungen,
            "geaenderte_grenzwerte": self.geaenderte_grenzwerte,
        }

    def plus(self, ander: Zaehler) -> Zaehler:
        return Zaehler(
            dateien_geprueft=self.dateien_geprueft + ander.dateien_geprueft,
            entfernte_tests=self.entfernte_tests + ander.entfernte_tests,
            entfernte_asserts=self.entfernte_asserts + ander.entfernte_asserts,
            entfernte_raises=self.entfernte_raises + ander.entfernte_raises,
            entfernte_pruefungen=self.entfernte_pruefungen + ander.entfernte_pruefungen,
            geaenderte_grenzwerte=self.geaenderte_grenzwerte + ander.geaenderte_grenzwerte,
        )


@dataclass(frozen=True)
class Ergebnis:
    """Gesamtergebnis von :func:`pruefe_diff`."""

    ok: bool
    exit_code: int
    verstoesse: tuple[Verstoss, ...]
    zaehler: Zaehler
    hinweis: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "zaehler": self.zaehler.als_dict(),
            "verstoesse": [v.als_dict() for v in self.verstoesse],
            "hinweis": self.hinweis,
        }

    def bericht(self) -> str:
        if self.exit_code == EXIT_FEHLER:
            return f"Test-Schutz Fehler: {self.hinweis}"
        if self.ok:
            return f"Test-Schutz grün ({self.zaehler.dateien_geprueft} Testdatei(en) geprüft, keine Verstöße)."
        kopf = (
            f"Test-Schutz rot ({len(self.verstoesse)} Verstoß/Verstöße in "
            f"{self.zaehler.dateien_geprueft} Testdatei(en): "
            f"{self.zaehler.entfernte_tests} entfernte Tests, "
            f"{self.zaehler.entfernte_pruefungen} entfernte Prüfungen, "
            f"{self.zaehler.geaenderte_grenzwerte} geänderte Grenzwerte):"
        )
        return "\n".join([kopf, *(v.kurz() for v in self.verstoesse)])


def _normal_pfad(pfad: str) -> str:
    return pfad.replace("\\", "/").removeprefix("./")


def _hat_testnamen(pfad: str) -> bool:
    name = PurePosixPath(_normal_pfad(pfad)).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def ist_testdatei(pfad: str, inhalt: str | None = None) -> bool:
    """``True``, wenn ``pfad`` eine Python-Testdatei ist (analog ``review/rot_probe.ist_test``)."""
    normal = _normal_pfad(pfad)
    if not _hat_testnamen(normal):
        return False
    ordner = {teil.lower() for teil in PurePosixPath(normal).parts[:-1]}
    if "tests" in ordner or "test" in ordner:
        return True
    if inhalt is None:
        return True
    return bool(_TESTFUNKTION_RE.search(inhalt))


# --- AST-Normalisierung und Prüf-Extraktion ----------------------------------------------------


def _zahl_wert(knoten: ast.AST) -> int | float | None:
    """Numerischer Literalwert (inkl. unärem ``+``/``-``), nie ``bool``."""
    if (
        isinstance(knoten, ast.Constant)
        and isinstance(knoten.value, (int, float))
        and not isinstance(knoten.value, bool)
    ):
        return knoten.value
    if isinstance(knoten, ast.UnaryOp) and isinstance(knoten.op, (ast.UAdd, ast.USub)):
        innen = _zahl_wert(knoten.operand)
        if innen is not None:
            return -innen if isinstance(knoten.op, ast.USub) else +innen
    return None


class _SkelettBauer(ast.NodeTransformer):
    """Ersetzt numerische Literale und Vergleichsoperatoren durch Platzhalter und sammelt sie."""

    def __init__(self) -> None:
        self.zahlen: list[int | float] = []
        self.operatoren: list[str] = []

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        wert = _zahl_wert(node)
        if wert is not None:
            self.zahlen.append(wert)
            return ast.Name(id="__ZAHL__", ctx=ast.Load())
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        wert = _zahl_wert(node)
        if wert is not None:
            self.zahlen.append(wert)
            return ast.Name(id="__ZAHL__", ctx=ast.Load())
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.operatoren.extend(type(op).__name__ for op in node.ops)
        node.ops = [ast.Eq() for _ in node.ops]
        return self.generic_visit(node)


def _skelett_und_merkmale(knoten: ast.AST) -> tuple[str, tuple[str, ...], tuple[int | float, ...]]:
    kopie = copy.deepcopy(knoten)
    bauer = _SkelettBauer()
    normalisiert = bauer.visit(kopie)
    return ast.dump(normalisiert, include_attributes=False), tuple(bauer.operatoren), tuple(bauer.zahlen)


def _unparse_sicher(knoten: ast.AST) -> str:
    try:
        return ast.unparse(knoten)
    except (ValueError, TypeError):
        return ast.dump(knoten, include_attributes=False)


def _zerlege_and(ausdruck: ast.expr) -> list[ast.expr]:
    """Zerlegt ``assert A and B`` auf oberster Ebene in Einzelprüfungen ``[A, B]``."""
    if isinstance(ausdruck, ast.BoolOp) and isinstance(ausdruck.op, ast.And):
        teile: list[ast.expr] = []
        for wert in ausdruck.values:
            teile.extend(_zerlege_and(wert))
        return teile
    return [ausdruck]


def _or_zweige_exakt(ausdruck: ast.expr) -> set[str]:
    """Alle Zweige eines ``or``-Ausdrucks als kanonische AST-Dumps."""
    if not (isinstance(ausdruck, ast.BoolOp) and isinstance(ausdruck.op, ast.Or)):
        return set()
    zweige: set[str] = set()
    for wert in ausdruck.values:
        if isinstance(wert, ast.BoolOp) and isinstance(wert.op, ast.Or):
            zweige.update(_or_zweige_exakt(wert))
        else:
            zweige.add(ast.dump(wert, include_attributes=False))
    return zweige


@dataclass(frozen=True)
class _PruefEintrag:
    """Einzelne Assertion (``assert`` oder ``assert*``-Methodenaufruf) im AST."""

    exakt: str
    skelett: str
    operatoren: tuple[str, ...]
    zahlen: tuple[int | float, ...]
    zeile: int
    quelltext: str
    ist_trivial: bool
    or_zweige: frozenset[str]


@dataclass(frozen=True)
class _RaisesEintrag:
    """Einzelner ``pytest.raises``/``pytest.warns``/``assertRaises``-Aufruf."""

    zeile: int
    quelltext: str
    ist_breit: bool


@dataclass(frozen=True)
class _FunktionProfil:
    """Extrahierte Prüf-Merkmale einer Test- oder Hilfsfunktion."""

    name: str
    ist_test: bool
    zeile: int
    asserts: tuple[_PruefEintrag, ...]
    raises: tuple[_RaisesEintrag, ...]
    method_asserts: tuple[_PruefEintrag, ...]
    deaktivierungen: int
    parametrize: tuple[tuple[str, int], ...]
    lokale_grenzen: dict[str, tuple[int | float, int]]


def _aufruf_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _ist_raises_aufruf(knoten: ast.Call) -> bool:
    if isinstance(knoten.func, ast.Attribute):
        return knoten.func.attr in _RAISES_METHODEN
    if isinstance(knoten.func, ast.Name):
        return knoten.func.id in ("raises", "warns")
    return False


def _ist_breite_ausnahme(knoten: ast.Call) -> bool:
    if not knoten.args:
        return False
    erst = knoten.args[0]
    if isinstance(erst, ast.Name):
        return erst.id in _BREITE_AUSNAHMEN
    if isinstance(erst, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in _BREITE_AUSNAHMEN for e in erst.elts)
    return False


def _ist_method_assert(knoten: ast.Call) -> bool:
    if not isinstance(knoten.func, ast.Attribute):
        return False
    attr = knoten.func.attr
    if attr in _RAISES_METHODEN:
        return False
    return attr.startswith("assert_") or (attr.startswith("assert") and len(attr) > 6 and attr[6].isupper())


def _deaktivierungs_zaehler(fn: ast.FunctionDef | ast.AsyncFunctionDef, klassen: Sequence[ast.ClassDef]) -> int:
    anzahl = 0
    for dek in [*fn.decorator_list, *(d for k in klassen for d in k.decorator_list)]:
        ziel = dek.func if isinstance(dek, ast.Call) else dek
        if _aufruf_name(ziel) in _DEAKTIVIERUNG_NAMEN:
            anzahl += 1
    for kind in ast.walk(fn):
        # Nur echte Aufrufe wie pytest.skip(...), pytest.xfail(...), self.skipTest(...).
        if (
            isinstance(kind, ast.Call)
            and isinstance(kind.func, ast.Attribute)
            and _aufruf_name(kind.func) in _DEAKTIVIERUNG_AUFRUFE
        ):
            anzahl += 1
    return anzahl


def _parametrize_faelle(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, klassen: Sequence[ast.ClassDef]
) -> tuple[tuple[str, int], ...]:
    faelle: list[tuple[str, int]] = []
    for dek in [*fn.decorator_list, *(d for k in klassen for d in k.decorator_list)]:
        if not isinstance(dek, ast.Call) or _aufruf_name(dek.func) != "parametrize":
            continue
        argnames = (
            dek.args[0] if len(dek.args) >= 1 else next((k.value for k in dek.keywords if k.arg == "argnames"), None)
        )
        argvalues = (
            dek.args[1] if len(dek.args) >= 2 else next((k.value for k in dek.keywords if k.arg == "argvalues"), None)
        )
        if isinstance(argvalues, (ast.List, ast.Tuple, ast.Set)):
            schluessel = _unparse_sicher(argnames) if argnames is not None else "?"
            faelle.append((schluessel, len(argvalues.elts)))
    return tuple(faelle)


def _geladene_namen(knoten: ast.AST) -> set[str]:
    return {k.id for k in ast.walk(knoten) if isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load)}


def _funktion_profil(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    ist_test: bool,
    klassen: Sequence[ast.ClassDef],
) -> _FunktionProfil:
    asserts: list[_PruefEintrag] = []
    raises: list[_RaisesEintrag] = []
    method_asserts: list[_PruefEintrag] = []
    namen_in_pruefungen: set[str] = set()

    for kind in ast.walk(fn):
        if isinstance(kind, ast.Assert):
            namen_in_pruefungen.update(_geladene_namen(kind.test))
            for teil in _zerlege_and(kind.test):
                skelett, ops, zahlen = _skelett_und_merkmale(teil)
                asserts.append(
                    _PruefEintrag(
                        exakt=ast.dump(teil, include_attributes=False),
                        skelett=skelett,
                        operatoren=ops,
                        zahlen=zahlen,
                        zeile=getattr(teil, "lineno", kind.lineno),
                        quelltext=_unparse_sicher(teil),
                        ist_trivial=isinstance(teil, ast.Constant),
                        or_zweige=frozenset(_or_zweige_exakt(teil)),
                    )
                )
        elif isinstance(kind, ast.Call):
            if _ist_raises_aufruf(kind):
                raises.append(
                    _RaisesEintrag(
                        zeile=kind.lineno,
                        quelltext=_unparse_sicher(kind),
                        ist_breit=_ist_breite_ausnahme(kind),
                    )
                )
            elif _ist_method_assert(kind):
                namen_in_pruefungen.update(_geladene_namen(kind))
                skelett, ops, zahlen = _skelett_und_merkmale(kind)
                method_asserts.append(
                    _PruefEintrag(
                        exakt=ast.dump(kind, include_attributes=False),
                        skelett=skelett,
                        operatoren=ops,
                        zahlen=zahlen,
                        zeile=kind.lineno,
                        quelltext=_unparse_sicher(kind),
                        ist_trivial=False,
                        or_zweige=frozenset(),
                    )
                )

    # Einfache lokale Zahlen-Zuweisungen, die in einer Prüfung der Funktion genutzt werden.
    zuweisungen: dict[str, list[tuple[int | float, int]]] = {}
    for anw in fn.body:
        if isinstance(anw, ast.Assign) and len(anw.targets) == 1 and isinstance(anw.targets[0], ast.Name):
            wert = _zahl_wert(anw.value)
            if wert is not None:
                zuweisungen.setdefault(anw.targets[0].id, []).append((wert, anw.lineno))
        elif isinstance(anw, ast.AnnAssign) and isinstance(anw.target, ast.Name) and anw.value is not None:
            wert = _zahl_wert(anw.value)
            if wert is not None:
                zuweisungen.setdefault(anw.target.id, []).append((wert, anw.lineno))
    lokale_grenzen = {
        var: werte[0] for var, werte in zuweisungen.items() if len(werte) == 1 and var in namen_in_pruefungen
    }

    return _FunktionProfil(
        name=name,
        ist_test=ist_test,
        zeile=fn.lineno,
        asserts=tuple(asserts),
        raises=tuple(raises),
        method_asserts=tuple(method_asserts),
        deaktivierungen=_deaktivierungs_zaehler(fn, klassen),
        parametrize=_parametrize_faelle(fn, klassen),
        lokale_grenzen=lokale_grenzen,
    )


@dataclass(frozen=True)
class _ModulProfil:
    funktionen: dict[str, _FunktionProfil]
    modul_grenzen: dict[str, tuple[int | float, int]]


def _modul_profil(baum: ast.Module) -> _ModulProfil:
    funktionen: dict[str, _FunktionProfil] = {}
    modul_grenzen: dict[str, tuple[int | float, int]] = {}

    def _sammle(rumpf: Sequence[ast.stmt], klassen: list[ast.ClassDef]) -> None:
        praefix = "::".join(k.name for k in klassen)
        for anw in rumpf:
            if isinstance(anw, (ast.FunctionDef, ast.AsyncFunctionDef)):
                voll = f"{praefix}::{anw.name}" if praefix else anw.name
                ist_test = anw.name.startswith("test_")
                funktionen[voll] = _funktion_profil(anw, voll, ist_test, klassen)
            elif isinstance(anw, ast.ClassDef):
                _sammle(anw.body, [*klassen, anw])
            elif not klassen:
                if isinstance(anw, ast.Assign) and len(anw.targets) == 1 and isinstance(anw.targets[0], ast.Name):
                    wert = _zahl_wert(anw.value)
                    if wert is not None:
                        modul_grenzen[anw.targets[0].id] = (wert, anw.lineno)
                elif isinstance(anw, ast.AnnAssign) and isinstance(anw.target, ast.Name) and anw.value is not None:
                    wert = _zahl_wert(anw.value)
                    if wert is not None:
                        modul_grenzen[anw.target.id] = (wert, anw.lineno)

    _sammle(baum.body, [])
    return _ModulProfil(funktionen=funktionen, modul_grenzen=modul_grenzen)


def _abziehen_exakt(
    alt: Sequence[_PruefEintrag], neu: Sequence[_PruefEintrag]
) -> tuple[list[_PruefEintrag], list[_PruefEintrag]]:
    """Entfernt identische Prüfungen (Multimengen-Differenz), behält nur geänderte/entfernte/neue."""
    neu_zaehler = Counter(e.exakt for e in neu)
    alt_rest: list[_PruefEintrag] = []
    for eintrag in alt:
        if neu_zaehler[eintrag.exakt] > 0:
            neu_zaehler[eintrag.exakt] -= 1
        else:
            alt_rest.append(eintrag)

    alt_zaehler = Counter(e.exakt for e in alt)
    neu_rest: list[_PruefEintrag] = []
    for eintrag in neu:
        if alt_zaehler[eintrag.exakt] > 0:
            alt_zaehler[eintrag.exakt] -= 1
        else:
            neu_rest.append(eintrag)
    return alt_rest, neu_rest


def _bestes_skelett_paar(alt_eintrag: _PruefEintrag, kandidaten: list[_PruefEintrag]) -> _PruefEintrag | None:
    passend = [k for k in kandidaten if k.skelett == alt_eintrag.skelett]
    if not passend:
        return None
    if len(passend) == 1:
        return passend[0]

    def _abstand(k: _PruefEintrag) -> tuple[int, int]:
        gemeinsam = sum(1 for a, b in zip(alt_eintrag.zahlen, k.zahlen, strict=False) if a == b)
        return (-gemeinsam, abs(k.zeile - alt_eintrag.zeile))

    return min(passend, key=_abstand)


def _vergleiche_eintraege(
    datei: str,
    test_name: str,
    alt_liste: Sequence[_PruefEintrag],
    neu_liste: Sequence[_PruefEintrag],
    *,
    entfernt_art: str,
    label: str,
) -> tuple[list[Verstoss], int, int]:
    """Vergleicht Assertions (``assert`` oder ``assert*``) einer Funktion.

    Rückgabe: ``(verstoesse, anzahl_entfernt, anzahl_grenzwert)``.
    """
    verstoesse: list[Verstoss] = []
    entfernt = max(0, len(alt_liste) - len(neu_liste))
    grenzwert = 0
    alt_rest, neu_rest = _abziehen_exakt(alt_liste, neu_liste)

    if entfernt > 0:
        zeile = alt_rest[0].zeile if alt_rest else (alt_liste[0].zeile if alt_liste else 1)
        verstoesse.append(
            Verstoss(
                datei=datei,
                art=entfernt_art,
                test=test_name,
                zeile=zeile,
                text=f"{entfernt} {label} entfernt ({len(alt_liste)} → {len(neu_liste)})",
            )
        )

    verfuegbar = list(neu_rest)
    unzugeordnet_alt: list[_PruefEintrag] = []
    for alt_e in alt_rest:
        treffer = _bestes_skelett_paar(alt_e, verfuegbar)
        if treffer is not None and (alt_e.zahlen != treffer.zahlen or alt_e.operatoren != treffer.operatoren):
            verfuegbar.remove(treffer)
            grenzwert += 1
            verstoesse.append(
                Verstoss(
                    datei=datei,
                    art="grenzwert_geaendert",
                    test=test_name,
                    zeile=treffer.zeile,
                    text="Grenzwert oder Vergleichsoperator in Prüfung geändert",
                    alt=alt_e.quelltext,
                    neu=treffer.quelltext,
                )
            )
            continue
        # Aufweichung durch zusätzlichen or-Zweig: assert A → assert A or B
        or_treffer = next((k for k in verfuegbar if alt_e.exakt in k.or_zweige), None)
        if or_treffer is not None:
            verfuegbar.remove(or_treffer)
            verstoesse.append(
                Verstoss(
                    datei=datei,
                    art="pruefung_entschaerft",
                    test=test_name,
                    zeile=or_treffer.zeile,
                    text="Assertion durch or-Zweig aufgeweicht",
                    alt=alt_e.quelltext,
                    neu=or_treffer.quelltext,
                )
            )
            continue
        unzugeordnet_alt.append(alt_e)

    # Ersetzen einer echten Assertion durch eine triviale Konstante (z. B. assert True).
    alt_trivial = sum(1 for e in alt_liste if e.ist_trivial)
    neu_trivial = sum(1 for e in neu_liste if e.ist_trivial)
    if neu_trivial > alt_trivial and unzugeordnet_alt:
        triv = next((k for k in verfuegbar if k.ist_trivial), None)
        if triv is not None:
            verstoesse.append(
                Verstoss(
                    datei=datei,
                    art="pruefung_entschaerft",
                    test=test_name,
                    zeile=triv.zeile,
                    text="Assertion durch triviale Konstante ersetzt",
                    alt=unzugeordnet_alt[0].quelltext,
                    neu=triv.quelltext,
                )
            )

    return verstoesse, entfernt, grenzwert


def pruefe_inhalt(datei: str, alt: str | None, neu: str | None) -> tuple[list[Verstoss], Zaehler]:
    """Vergleicht zwei Textstände einer Python-Testdatei per AST-Diff.

    - ``alt is None`` (neue Testdatei): immer grün.
    - ``neu is None`` (gelöschte Testdatei): immer rot (``testdatei_geloescht``).
    - Syntaxfehler im neuen Stand: fail-closed rot (``syntax_fehler``).
    """
    normal = _normal_pfad(datei)
    if alt is None:
        return [], Zaehler(dateien_geprueft=1)

    try:
        alt_baum = ast.parse(alt, filename=normal)
    except SyntaxError as fehler:
        log.warning("Alter Stand von %s hat Syntaxfehler (%s) — nicht vergleichbar", normal, fehler)
        return [], Zaehler(dateien_geprueft=1)
    alt_profil = _modul_profil(alt_baum)

    if neu is None:
        alt_tests = [f for f in alt_profil.funktionen.values() if f.ist_test]
        entf_asserts = sum(len(f.asserts) for f in alt_profil.funktionen.values())
        entf_raises = sum(len(f.raises) for f in alt_profil.funktionen.values())
        entf_method = sum(len(f.method_asserts) for f in alt_profil.funktionen.values())
        verstoss = Verstoss(
            datei=normal,
            art="testdatei_geloescht",
            test="",
            zeile=1,
            text=f"Testdatei gelöscht ({len(alt_tests)} Testfunktion(en) entfernt)",
        )
        return [verstoss], Zaehler(
            dateien_geprueft=1,
            entfernte_tests=len(alt_tests),
            entfernte_asserts=entf_asserts,
            entfernte_raises=entf_raises,
            entfernte_pruefungen=entf_asserts + entf_raises + entf_method,
        )

    try:
        neu_baum = ast.parse(neu, filename=normal)
    except SyntaxError as fehler:
        return [
            Verstoss(
                datei=normal,
                art="syntax_fehler",
                test="",
                zeile=fehler.lineno or 1,
                text=f"Testdatei nicht parsbar (SyntaxError: {fehler.msg})",
            )
        ], Zaehler(dateien_geprueft=1)

    neu_profil = _modul_profil(neu_baum)
    verstoesse: list[Verstoss] = []
    entf_tests = 0
    entf_asserts = 0
    entf_raises = 0
    entf_method = 0
    geaend_grenz = 0

    # 1. Modul-Grenzwerte (Konstanten auf oberster Ebene)
    for name, (alt_wert, _alt_zeile) in alt_profil.modul_grenzen.items():
        if name in neu_profil.modul_grenzen:
            neu_wert, neu_zeile = neu_profil.modul_grenzen[name]
            if alt_wert != neu_wert:
                geaend_grenz += 1
                verstoesse.append(
                    Verstoss(
                        datei=normal,
                        art="grenzwert_geaendert",
                        test="",
                        zeile=neu_zeile,
                        text=f"Modul-Grenzwert {name} geändert",
                        alt=f"{name} = {alt_wert}",
                        neu=f"{name} = {neu_wert}",
                    )
                )

    # Gesamt-Prüfungen der Datei für Hilfsfunktionen-Inlining-Schutz.
    gesamt_alt_asserts = sum(len(f.asserts) for f in alt_profil.funktionen.values())
    gesamt_neu_asserts = sum(len(f.asserts) for f in neu_profil.funktionen.values())
    gesamt_alt_raises = sum(len(f.raises) for f in alt_profil.funktionen.values())
    gesamt_neu_raises = sum(len(f.raises) for f in neu_profil.funktionen.values())
    gesamt_alt_method = sum(len(f.method_asserts) for f in alt_profil.funktionen.values())
    gesamt_neu_method = sum(len(f.method_asserts) for f in neu_profil.funktionen.values())

    # 2. Bestehende Testfunktionen und Hilfsfunktionen vergleichen
    for name, alt_fn in alt_profil.funktionen.items():
        neu_fn = neu_profil.funktionen.get(name)
        if neu_fn is None:
            if alt_fn.ist_test:
                entf_tests += 1
                entf_asserts += len(alt_fn.asserts)
                entf_raises += len(alt_fn.raises)
                entf_method += len(alt_fn.method_asserts)
                verstoesse.append(
                    Verstoss(
                        datei=normal,
                        art="test_entfernt",
                        test=name,
                        zeile=alt_fn.zeile,
                        text=f"Testfunktion {name} entfernt",
                    )
                )
            continue

        # Bei Hilfsfunktionen nur melden, wenn Prüfungen tatsächlich geschrumpft oder Grenzwerte geändert wurden.
        ist_helfer = not alt_fn.ist_test

        # 2a. Neu deaktivierte Tests (skip / xfail)
        if alt_fn.ist_test and neu_fn.deaktivierungen > alt_fn.deaktivierungen:
            verstoesse.append(
                Verstoss(
                    datei=normal,
                    art="test_deaktiviert",
                    test=name,
                    zeile=neu_fn.zeile,
                    text=f"Test {name} neu deaktiviert (skip/xfail ergänzt)",
                )
            )

        # 2b. Reduziertes @pytest.mark.parametrize
        if alt_fn.ist_test and alt_fn.parametrize:
            neu_param = dict(neu_fn.parametrize)
            for schluessel, alt_anz in alt_fn.parametrize:
                neu_anz = neu_param.get(schluessel, 0)
                if neu_anz < alt_anz:
                    verstoesse.append(
                        Verstoss(
                            datei=normal,
                            art="parametrize_reduziert",
                            test=name,
                            zeile=neu_fn.zeile,
                            text=f"parametrize({schluessel}) reduziert ({alt_anz} → {neu_anz} Fälle)",
                        )
                    )

        # 2c. assert-Prüfungen
        v_assert, e_assert, g_assert = _vergleiche_eintraege(
            normal, name, alt_fn.asserts, neu_fn.asserts, entfernt_art="assert_entfernt", label="assert-Prüfung(en)"
        )
        if ist_helfer and gesamt_neu_asserts >= gesamt_alt_asserts:
            v_assert = [v for v in v_assert if v.art != "assert_entfernt"]
            e_assert = 0
        verstoesse.extend(v_assert)
        entf_asserts += e_assert
        geaend_grenz += g_assert

        # 2d. pytest.raises / pytest.warns / assertRaises
        if len(neu_fn.raises) < len(alt_fn.raises):
            if not ist_helfer or gesamt_neu_raises < gesamt_alt_raises:
                diff_r = len(alt_fn.raises) - len(neu_fn.raises)
                entf_raises += diff_r
                verstoesse.append(
                    Verstoss(
                        datei=normal,
                        art="pytest_raises_entfernt",
                        test=name,
                        zeile=alt_fn.raises[0].zeile,
                        text=f"{diff_r} pytest.raises/warns-Prüfung(en) entfernt ({len(alt_fn.raises)} → {len(neu_fn.raises)})",
                    )
                )
        else:
            alt_breit = sum(1 for r in alt_fn.raises if r.ist_breit)
            neu_breit = sum(1 for r in neu_fn.raises if r.ist_breit)
            if neu_breit > alt_breit and len(neu_fn.raises) == len(alt_fn.raises):
                breiter = next(r for r in neu_fn.raises if r.ist_breit)
                verstoesse.append(
                    Verstoss(
                        datei=normal,
                        art="pruefung_entschaerft",
                        test=name,
                        zeile=breiter.zeile,
                        text="Fehlerprüfung auf allgemeines Exception/BaseException aufgeweicht",
                        neu=breiter.quelltext,
                    )
                )

        # 2e. unittest-/Mock-assert*-Aufrufe
        v_meth, e_meth, g_meth = _vergleiche_eintraege(
            normal,
            name,
            alt_fn.method_asserts,
            neu_fn.method_asserts,
            entfernt_art="pruefung_entfernt",
            label="assert*-Aufruf(e)",
        )
        if ist_helfer and gesamt_neu_method >= gesamt_alt_method:
            v_meth = [v for v in v_meth if v.art != "pruefung_entfernt"]
            e_meth = 0
        verstoesse.extend(v_meth)
        entf_method += e_meth
        geaend_grenz += g_meth

        # 2f. Lokale Grenzwert-Variablen, die in Prüfungen genutzt werden
        for var, (alt_wert, _alt_z) in alt_fn.lokale_grenzen.items():
            if var in neu_fn.lokale_grenzen:
                neu_wert, neu_z = neu_fn.lokale_grenzen[var]
                if alt_wert != neu_wert:
                    geaend_grenz += 1
                    verstoesse.append(
                        Verstoss(
                            datei=normal,
                            art="grenzwert_geaendert",
                            test=name,
                            zeile=neu_z,
                            text=f"Lokaler Grenzwert {var} geändert",
                            alt=f"{var} = {alt_wert}",
                            neu=f"{var} = {neu_wert}",
                        )
                    )

    zaehler = Zaehler(
        dateien_geprueft=1,
        entfernte_tests=entf_tests,
        entfernte_asserts=entf_asserts,
        entfernte_raises=entf_raises,
        entfernte_pruefungen=entf_asserts + entf_raises + entf_method,
        geaenderte_grenzwerte=geaend_grenz,
    )
    return verstoesse, zaehler


# --- Git-Diff-Analyse --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str, str]:
    try:
        erg = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError as fehler:
        return 127, "", str(fehler)
    return erg.returncode, erg.stdout, erg.stderr


def _git_inhalt(repo: Path, ref: str, pfad: str) -> str | None:
    code, raus, _err = _git(repo, "show", f"{ref}:{pfad}")
    return raus if code == 0 else None


def _arbeitsbaum_inhalt(repo: Path, pfad: str) -> str | None:
    datei = repo / pfad
    if not datei.is_file():
        return None
    try:
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Testdatei %s nicht lesbar: %s", datei, fehler)
        return None


def _diff_pfade(repo: Path, basis: str, ziel: str | None) -> tuple[list[str] | None, str]:
    args = ["diff", "--name-only", "--no-renames", "-z", basis]
    if ziel is not None:
        args.append(ziel)
    args.append("--")
    code, raus, err = _git(repo, *args)
    if code != 0:
        kurz = (err.strip().splitlines() or [f"git diff Exit {code}"])[0]
        return None, kurz
    pfade = sorted({_normal_pfad(p) for p in raus.split("\0") if p})
    return pfade, ""


def pruefe_diff(
    repo: Path,
    basis: str = "HEAD",
    ziel: str | None = None,
    *,
    pfade: Sequence[str] | None = None,
) -> Ergebnis:
    """Prüft alle im git-Diff geänderten Testdateien gegen ``basis``.

    Ist ``ziel`` angegeben (z. B. ``"HEAD"`` in der CI), wird ``basis`` gegen ``ziel``
    verglichen; bei ``ziel=None`` wird ``basis`` gegen den aktuellen Arbeitsbaum geprüft
    (vor dem Commit/Push).
    """
    oben_code, oben_raus, oben_err = _git(repo, "rev-parse", "--show-toplevel")
    if oben_code != 0:
        grund = (oben_err.strip().splitlines() or ["kein Git-Repo"])[0]
        return Ergebnis(False, EXIT_FEHLER, (), Zaehler(), hinweis=f"kein Git-Repo ({repo}): {grund}")
    wurzel = Path(oben_raus.strip())

    if pfade is None:
        diff_liste, fehler = _diff_pfade(wurzel, basis, ziel)
        if diff_liste is None:
            return Ergebnis(False, EXIT_FEHLER, (), Zaehler(), hinweis=f"git diff gescheitert: {fehler}")
    else:
        diff_liste = sorted({_normal_pfad(p) for p in pfade if p})

    alle_verstoesse: list[Verstoss] = []
    gesamt = Zaehler()
    for pfad in diff_liste:
        if not _hat_testnamen(pfad):
            continue
        alt = _git_inhalt(wurzel, basis, pfad)
        neu = _git_inhalt(wurzel, ziel, pfad) if ziel is not None else _arbeitsbaum_inhalt(wurzel, pfad)
        if not ist_testdatei(pfad, alt if alt is not None else neu):
            continue
        verstoesse, zaehler = pruefe_inhalt(pfad, alt, neu)
        alle_verstoesse.extend(verstoesse)
        gesamt = gesamt.plus(zaehler)

    ok = not alle_verstoesse
    return Ergebnis(
        ok=ok,
        exit_code=EXIT_GRUEN if ok else EXIT_ROT,
        verstoesse=tuple(alle_verstoesse),
        zaehler=gesamt,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.test_schutz",
        description="Test-Schutz-Prüfung (AST-Diff): erkennt entfernte Prüfungen und geänderte Grenzwerte.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner (Vorgabe: aktueller Ordner)")
    parser.add_argument(
        "--basis", "--base", dest="basis", default="HEAD", help="Ausgangsstand (git-Ref, Vorgabe: HEAD)"
    )
    parser.add_argument(
        "--ziel",
        default=None,
        help="Zielstand (git-Ref, z. B. HEAD in CI; weglassen = Arbeitsbaum gegen Basis prüfen)",
    )
    parser.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout ausgeben")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    erg = pruefe_diff(args.repo, args.basis, args.ziel)
    if args.als_json:
        sys.stdout.write(json.dumps(erg.als_dict(), ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(erg.bericht() + "\n")
    return erg.exit_code


if __name__ == "__main__":
    sys.exit(main())
