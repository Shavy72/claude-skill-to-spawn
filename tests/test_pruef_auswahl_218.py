"""Offene Abnahmen aus #218: Node-Adapter im echten Node-Repo und Symlink-Zweig in ``erklaerte_doku``.

- Node-Adapter (``adapter_node.ADAPTER`` unverändert, echtes Git-Repo, echtes ``npm test`` + ``node``):
  Blatt-Änderung wählt gezielt aus, eingebauter Fehler wird rot, Nicht-Code-Datei erzwingt R1/voll,
  und ein Repo mit ``node --test`` (ohne vitest) fällt sauber auf voll zurück.
- Symlink-Zweig in ``sofort.erklaerte_doku`` (``_nicht_nur_doku``): Symlink unter erklärtem
  Doku-Pfad auf Code-Datei oder Symlink auf geänderte erklärte Doku verhindert sowohl „nur Doku“
  als auch eine reine Teil-Auswahl (R1 → voll).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pruef_probe_helfer as probe_helfer
import pytest
from test_pruef_auswahl_101_fix1 import NUR_WERK, _git, _pruef_stufe, repo  # noqa: F401 — Fixture ``repo``

from to_spawn import pruef
from to_spawn.pruef import cache, leiter, sofort


def _git_run(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.org", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _node_repo(ordner: Path, *, mit_vitest: bool) -> Path:
    """Frisches Node-Git-Repo mit zwei unabhängigen Modulen + fs-gelesener JSON-Datei; Tag ``base``."""
    ordner.mkdir(parents=True, exist_ok=True)
    skript = "vitest run" if mit_vitest else "node --test"
    dateien: dict[str, str] = {
        "package.json": json.dumps(
            {"name": "probe-node-218", "private": True, "type": "module", "scripts": {"test": skript}},
            indent=2,
        )
        + "\n",
        ".gitignore": "node_modules/\n",
        "src/format.js": 'export const euro = (x) => x.toFixed(2) + " EUR";\n',
        "src/math.js": "export const add = (a, b) => a + b;\n",
        "src/data/stock.json": '{"A1": 3}\n',
        "src/stock.js": (
            'import { readFileSync } from "node:fs";\n'
            'const PFAD = new URL("./data/stock.json", import.meta.url);\n'
            'export const bestand = (sku) => JSON.parse(readFileSync(PFAD, "utf8"))[sku];\n'
        ),
    }
    if mit_vitest:
        dateien.update(
            {
                "test/format.test.js": (
                    'import { test, expect } from "vitest";\n'
                    'import { euro } from "../src/format.js";\n'
                    'test("euro eins", () => expect(euro(1)).toBe("1.00 EUR"));\n'
                    'test("euro null", () => expect(euro(0)).toBe("0.00 EUR"));\n'
                ),
                "test/math.test.js": (
                    'import { test, expect } from "vitest";\n'
                    'import { add } from "../src/math.js";\n'
                    'test("add eins", () => expect(add(1, 2)).toBe(3));\n'
                    'test("add null", () => expect(add(0, 0)).toBe(0));\n'
                ),
                "test/stock.test.js": (
                    'import { test, expect } from "vitest";\n'
                    'import { bestand } from "../src/stock.js";\n'
                    'test("bestand A1", () => expect(bestand("A1")).toBe(3));\n'
                ),
            }
        )
    else:
        dateien["test/math.test.js"] = (
            'import test from "node:test";\n'
            'import assert from "node:assert/strict";\n'
            'import { add } from "../src/math.js";\n'
            'test("add eins", () => assert.strictEqual(add(1, 2), 3));\n'
        )
    for pfad, text in dateien.items():
        ziel = ordner / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
    _git_run(ordner, "init", "-q")
    _git_run(ordner, "add", "-A")
    _git_run(ordner, "commit", "-qm", "base")
    _git_run(ordner, "tag", "base")
    if mit_vitest:
        probe_helfer.installiere_lokales_vitest(ordner)
    return ordner


@pytest.fixture(autouse=True)
def _ohne_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "treffer", lambda _schluessel: False)
    monkeypatch.setattr(cache, "merke", lambda *_a, **_k: None)


# --- Node-Adapter im echten Node-Repo -----------------------------------------------------------

braucht_npm = pytest.mark.skipif(shutil.which("npm") is None, reason="npm nicht im PATH")


@braucht_npm
def test_node_adapter_auswahl_greift_fuer_blatt_aenderung(tmp_path: Path) -> None:
    ort = _node_repo(tmp_path / "node_ok", mit_vitest=True)
    (ort / "src" / "format.js").write_text("export const euro = (x) => `${x.toFixed(2)} EUR`;\n", encoding="utf-8")
    _git_run(ort, "commit", "-qam", "refaktoriert format")

    urteil = pruef.pruefen(ort, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    wahl = urteil.zeile["auswahl"][0]
    assert urteil.exit == 0, urteil.zeile["fehler_kurz"]
    assert (stufe["name"], stufe["adapter"], wahl["sicher"]) == ("auswahl", "node", True)
    assert wahl["n"] == 2  # nur test/format.test.js (2 von 5 Tests)
    assert any("+vitest --changed" in w for w in urteil.zeile["werkzeug"])
    assert any(w.startswith("regeln@") for w in urteil.zeile["werkzeug"])


@braucht_npm
def test_node_adapter_auswahl_findet_eingebauten_fehler(tmp_path: Path) -> None:
    ort = _node_repo(tmp_path / "node_rot", mit_vitest=True)
    (ort / "src" / "format.js").write_text('export const euro = (x) => x.toFixed(1) + " EUR";\n', encoding="utf-8")
    _git_run(ort, "commit", "-qam", "fehler in format")

    urteil = pruef.pruefen(ort, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert urteil.exit == 1, urteil.zeile
    assert stufe["name"] == "auswahl"
    assert urteil.zeile["urteil"] == "rot"


@braucht_npm
def test_node_adapter_nicht_code_datei_erzwingt_voll_r1(tmp_path: Path) -> None:
    ort = _node_repo(tmp_path / "node_r1", mit_vitest=True)
    (ort / "src" / "data" / "stock.json").write_text('{"A1": 0}\n', encoding="utf-8")
    _git_run(ort, "commit", "-qam", "fehler in stock.json")

    urteil = pruef.pruefen(ort, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert urteil.exit == 1, urteil.zeile
    assert stufe["name"] == "voll"
    assert stufe["grund"].startswith("R1"), stufe["grund"]
    assert urteil.zeile["auswahl"][0]["sicher"] is False


@braucht_npm
def test_node_adapter_ohne_vitest_faellt_auf_voll_zurueck(tmp_path: Path) -> None:
    """Reines ``node --test`` ohne vitest: Auswahl meldet „nicht vitest“, Leiter prüft voll."""
    ort = _node_repo(tmp_path / "node_nativ", mit_vitest=False)
    (ort / "src" / "math.js").write_text("export const add = (a, b) => b + a;\n", encoding="utf-8")
    _git_run(ort, "commit", "-qam", "aendert math")

    urteil = pruef.pruefen(ort, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert urteil.exit == 0, urteil.zeile["fehler_kurz"]
    assert (stufe["name"], stufe["adapter"]) == ("voll", "node")
    assert "scripts.test ist nicht vitest" in stufe["grund"], stufe["grund"]


# --- Symlink-Zweig in ``sofort.erklaerte_doku`` -------------------------------------------------


def _symlink(ziel: str, link: Path) -> None:
    try:
        os.symlink(ziel, link)
    except OSError as fehler:  # Windows ohne Symlink-Recht
        pytest.skip(f"Symlink nicht anlegbar: {fehler}")


def test_erklaerte_doku_symlink_auf_code_keine_abkuerzung(repo: Path) -> None:  # noqa: F811
    basis = _git(repo, "rev-parse", "HEAD")
    _symlink(os.path.join("..", "app", "werk.py"), repo / "docs" / "link.md")
    _git(repo, "add", "docs/link.md")
    _git(repo, "commit", "-qm", "symlink unter docs auf code")

    assert sofort.erklaerte_doku(repo, basis, ["docs/link.md"]) == frozenset()
    liste = leiter.stufen(repo, (NUR_WERK,), basis)
    doku = next(s for s in liste if s.name == "doku")
    stufe = _pruef_stufe(liste)
    assert doku.grund == sofort.SYMLINK
    assert (stufe.name, stufe.lief) == ("voll", True), stufe.grund
    assert stufe.grund.startswith("R1"), stufe.grund


def test_erklaerte_doku_code_und_symlink_im_doku_pfad_prueft_voll(repo: Path) -> None:  # noqa: F811
    basis = _git(repo, "rev-parse", "HEAD")
    (repo / "app" / "werk.py").write_text("def eins() -> int:\n    return 1  # neu\n", encoding="utf-8")
    _symlink(os.path.join("..", "app", "werk.py"), repo / "docs" / "link.md")
    _git(repo, "add", "app/werk.py", "docs/link.md")
    _git(repo, "commit", "-qm", "code plus symlink unter docs")

    assert sofort.erklaerte_doku(repo, basis, ["app/werk.py", "docs/link.md"]) == frozenset()
    stufe = _pruef_stufe(leiter.stufen(repo, (NUR_WERK,), basis))
    assert stufe.auswahl is not None
    assert (stufe.name, stufe.auswahl["sicher"]) == ("voll", False), stufe.grund
    assert stufe.grund.startswith("R1"), stufe.grund


def test_erklaerte_doku_symlink_auf_erklaerte_doku_mit_code_prueft_voll_und_rot(repo: Path) -> None:  # noqa: F811
    _symlink(os.path.join("docs", "handbuch.md"), repo / "vorlage.txt")
    (repo / "tests" / "test_vorlage.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_vorlage() -> None:\n"
        '    assert (Path(__file__).parents[1] / "vorlage.txt").read_text(encoding="utf-8") == "# Handbuch\\n"\n',
        encoding="utf-8",
    )
    _git(repo, "add", "vorlage.txt", "tests/test_vorlage.py")
    _git(repo, "commit", "-qm", "vorlage-symlink plus test")
    basis = _git(repo, "rev-parse", "HEAD")

    (repo / "app" / "werk.py").write_text("def eins() -> int:\n    return 1  # neu\n", encoding="utf-8")
    (repo / "docs" / "handbuch.md").write_text("# Verändert\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "code plus verlinkte doku")

    assert sofort.erklaerte_doku(repo, basis, ["app/werk.py", "docs/handbuch.md"]) == frozenset()
    liste = leiter.stufen(repo, (NUR_WERK,), basis)
    stufe = _pruef_stufe(liste)
    assert stufe.name == "voll" and stufe.grund.startswith("R1"), stufe.grund
    assert leiter.ergebnis(liste)[0] == leiter.ROT


def test_erklaerte_doku_ohne_symlink_laesst_auswahl_zu(repo: Path) -> None:  # noqa: F811
    basis = _git(repo, "rev-parse", "HEAD")
    (repo / "app" / "werk.py").write_text("def eins() -> int:\n    return 1  # neu\n", encoding="utf-8")
    (repo / "docs" / "handbuch.md").write_text("# Handbuch v2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "code plus echte doku")

    assert sofort.erklaerte_doku(repo, basis, ["app/werk.py", "docs/handbuch.md"]) == frozenset({"docs/handbuch.md"})
    stufe = _pruef_stufe(leiter.stufen(repo, (NUR_WERK,), basis))
    assert stufe.auswahl is not None
    assert (stufe.name, stufe.auswahl["sicher"]) == ("auswahl", True), stufe.grund
