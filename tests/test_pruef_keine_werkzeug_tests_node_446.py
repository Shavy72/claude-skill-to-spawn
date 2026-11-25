"""Node-Prüfung sammelt keine Werkzeug-Tests ein; eine Konstante für beide Adapter (#446).

Gegenstück zu ``test_pruef_keine_werkzeug_tests_425.py`` für ``adapter_node``:
- Echtes Node-Git-Repo mit grüner App-Testdatei plus absichtlich ROTEN Tests des
  Bau-Werkzeugs unter ``.to-spawn/…`` und ``.claude/…``.
- Beide Wege (``voll`` und ``auswahl`` über ``vitest --changed``), je mit Vorgabe-Konfig
  und eigener ``exclude``-Konfig der App.
- Natives ``node --test`` bleibt lauffähig (kein ungültiges ``--exclude``) und sammelt
  ebenfalls keine Werkzeug-Tests ein.
- Beide Adapter (``adapter_py`` und ``adapter_node``) lesen dieselbe Konstante
  ``leiter.WERKZEUG_ORTE`` — keine doppelte Pflege.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pruef_probe_helfer as probe_helfer
import pytest

from to_spawn import pruef
from to_spawn.pruef import adapter_node, adapter_py, ausfuehren, cache, leiter

braucht_npm = pytest.mark.skipif(shutil.which("npm") is None, reason="npm nicht im PATH")

#: Rote Werkzeug-Tests an den Orten, an denen das Bau-Werkzeug im App-Repo liegt.
WERKZEUG_ROT = (
    ".to-spawn/werkzeug/tests/werkzeug_rot.test.js",
    ".claude/skills/to-spawn/tests/skill_rot.test.js",
)
ROT_VITEST = (
    'import { test } from "vitest";\n'
    'test("werkzeug rot", () => {\n'
    '  throw new Error("Werkzeug-Test darf nie in der App-Prüfung laufen");\n'
    "});\n"
)
ROT_NATIV = (
    'import test from "node:test";\n'
    'test("werkzeug rot", () => {\n'
    '  throw new Error("Werkzeug-Test darf nie in der App-Prüfung laufen");\n'
    "});\n"
)

VITEST_KONFIG = {
    "vorgabe": None,
    "eigene_exclude": (
        'import { defineConfig } from "vitest/config";\n'
        'export default defineConfig({ test: { exclude: ["build/**"] } });\n'
    ),
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.org", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _node_app_repo(ordner: Path, *, konfig: str | None = None, mit_vitest: bool = True) -> Path:
    repo = ordner / "node_app"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    skript = "vitest run" if mit_vitest else "node --test"
    (repo / "package.json").write_text(
        json.dumps(
            {"name": "node-app-446", "private": True, "type": "module", "scripts": {"test": skript}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if konfig is not None:
        (repo / "vitest.config.js").write_text(konfig, encoding="utf-8")
    (repo / "src" / "rechne.js").write_text(
        "export const plus = (a, b) => a + b;\n",
        encoding="utf-8",
    )
    if mit_vitest:
        (repo / "test" / "rechne.test.js").write_text(
            'import { test, expect } from "vitest";\n'
            'import { plus } from "../src/rechne.js";\n'
            'test("plus", () => expect(plus(2, 3)).toBe(5));\n',
            encoding="utf-8",
        )
    else:
        (repo / "test" / "rechne.test.js").write_text(
            'import test from "node:test";\n'
            'import assert from "node:assert/strict";\n'
            'import { plus } from "../src/rechne.js";\n'
            'test("plus", () => assert.strictEqual(plus(2, 3), 5));\n',
            encoding="utf-8",
        )
    rot_text = ROT_VITEST if mit_vitest else ROT_NATIV
    for pfad in WERKZEUG_ROT:
        datei = repo / pfad
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(rot_text, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
        datei.write("/node_modules\n/.to-spawn/pruef/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "basis")
    _git(repo, "tag", "base")
    if mit_vitest:
        probe_helfer.installiere_lokales_vitest(repo)
    return repo


@pytest.fixture(autouse=True)
def _ohne_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "treffer", lambda _schluessel: False)
    monkeypatch.setattr(cache, "merke", lambda *_a, **_k: None)


@braucht_npm
@pytest.mark.parametrize("konfig", sorted(VITEST_KONFIG))
@pytest.mark.parametrize("weg", ["auswahl", "voll"])
def test_node_werkzeug_tests_nie_in_app_pruefung(tmp_path: Path, weg: str, konfig: str) -> None:
    repo = _node_app_repo(tmp_path, konfig=VITEST_KONFIG[konfig], mit_vitest=True)
    if weg == "auswahl":
        # Code-Datei plus Werkzeug-Tests geändert (alles .js → R1 lässt vitest --changed laufen):
        # ohne Ausschluss würde --changed die geänderten Werkzeug-Tests direkt einsammeln.
        datei = repo / "src" / "rechne.js"
        datei.write_text(datei.read_text(encoding="utf-8") + "export const NULL = 0;\n", encoding="utf-8")
        for pfad in WERKZEUG_ROT:
            w = repo / pfad
            w.write_text(w.read_text(encoding="utf-8") + "// berührt\n", encoding="utf-8")
    else:
        paket = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        paket["description"] = "Prüf-Konfig geändert (R3 -> voll)"
        (repo / "package.json").write_text(json.dumps(paket, indent=2) + "\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "änderung")

    urteil = pruef.pruefen(repo, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert stufe["name"] == weg, stufe["grund"]
    assert urteil.exit == 0, urteil.zeile["fehler_kurz"]
    teile = stufe["befehl"].split()
    assert teile.count("--") == 1, stufe["befehl"]
    for ort in leiter.WERKZEUG_ORTE:
        assert f"**/{ort}/**" in teile, stufe["befehl"]
    if weg == "auswahl":
        assert urteil.zeile["auswahl"][0]["n"] == 1


@braucht_npm
def test_node_test_nativ_ignoriert_werkzeug_orte(tmp_path: Path) -> None:
    """``node --test`` versteht kein ``--exclude``, schließt versteckte Werkzeug-Orte aber selbst aus."""
    repo = _node_app_repo(tmp_path, mit_vitest=False)
    datei = repo / "src" / "rechne.js"
    datei.write_text(datei.read_text(encoding="utf-8") + "export const NULL = 0;\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "änderung")

    urteil = pruef.pruefen(repo, "base", "HEAD")

    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert stufe["name"] == "voll"
    assert urteil.exit == 0, urteil.zeile["fehler_kurz"]
    assert "--exclude" not in stufe["befehl"]


def test_beide_adapter_lesen_dieselbe_werkzeug_orte_konstante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine Quelle: ``leiter.WERKZEUG_ORTE`` speist Python- und Node-Adapter ohne Kopie."""
    assert ".to-spawn" in leiter.WERKZEUG_ORTE
    assert ".claude" in leiter.WERKZEUG_ORTE
    assert not hasattr(adapter_py, "_WERKZEUG_ORTE")
    assert not hasattr(adapter_node, "_WERKZEUG_ORTE")

    py_repo = tmp_path / "py"
    py_repo.mkdir()
    (py_repo / "pyproject.toml").write_text('[project]\nname = "a"\n', encoding="utf-8")
    node_repo = tmp_path / "node"
    node_repo.mkdir()
    (node_repo / "package.json").write_text('{"scripts": {"test": "vitest run"}}\n', encoding="utf-8")

    monkeypatch.setattr(adapter_node.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(
        ausfuehren,
        "lauf",
        lambda befehl, _cwd: ausfuehren.Lauf(0, "10.0.0\n", 0.01),
    )
    monkeypatch.setattr(leiter, "WERKZEUG_ORTE", (".werkzeug-probe",))

    py_plan = adapter_py.ADAPTER.voll(py_repo)
    node_plan = adapter_node.ADAPTER.voll(node_repo)
    node_auswahl = adapter_node.ADAPTER.auswahl(node_repo, "base")

    assert "--ignore=.werkzeug-probe" in py_plan.befehl
    assert "--exclude" in node_plan.befehl
    assert "**/.werkzeug-probe/**" in node_plan.befehl
    assert "**/.werkzeug-probe/**" in node_auswahl.befehl
    assert node_auswahl.befehl.count("--") == 1


@pytest.mark.parametrize(
    ("skript", "erwartet_exclude", "auswahl_ok"),
    [
        ("vitest run", True, True),
        ("vitest", True, True),
        ("npx vitest run", True, True),
        ("mocha", True, False),
        ("npx mocha test/**/*.test.js", True, False),
        ("node --test", False, False),
        ("vitest run && eslint .", False, False),
    ],
)
def test_node_adapter_exclude_je_testlaeufer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skript: str,
    erwartet_exclude: bool,
    auswahl_ok: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"scripts": {"test": skript}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter_node.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(
        ausfuehren,
        "lauf",
        lambda _befehl, _cwd: ausfuehren.Lauf(0, "10.0.0\n", 0.01),
    )

    voll = adapter_node.ADAPTER.voll(repo)
    assert voll.fehler == ""
    if erwartet_exclude:
        assert voll.befehl[:3] == ["/usr/bin/npm", "test", "--"]
        for ort in leiter.WERKZEUG_ORTE:
            assert f"**/{ort}/**" in voll.befehl
    else:
        assert voll.befehl == ["/usr/bin/npm", "test"]

    wahl = adapter_node.ADAPTER.auswahl(repo, "base")
    if auswahl_ok:
        assert wahl.fehler == ""
        assert wahl.befehl.count("--") == 1
        assert wahl.befehl[-3:] == ["--changed", "base", "--passWithNoTests"]
        for ort in leiter.WERKZEUG_ORTE:
            assert f"**/{ort}/**" in wahl.befehl
    else:
        assert wahl.fehler != ""
