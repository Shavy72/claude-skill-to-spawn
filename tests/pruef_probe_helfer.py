"""Eigenständige Probe-Repos (Python + Node) für Prüf-Auswahl-Tests ohne ``TO_SPAWN_PROBE`` (#218).

Erzeugt echte Git-Repos direkt aus ``messung/gen_py.py`` und ``messung/gen_node.py`` (eine Quelle,
keine Kopie). Wenn die externen Pakete ``pytest-impacted`` bzw. ``vitest`` nicht installiert sind,
stellt dieses Modul schlanke lokale Läufer mit derselben statischen Import-Graph-Semantik bereit:

- Python: ``AstAuswahlAdapter`` (Unterklasse von ``adapter_py.PythonAdapter``) ermittelt betroffene
  Testdateien über ``git diff`` + ``ast``-Import-Graph und ruft echtes ``pytest`` mit ``--deselect``
  auf — blind für Daten-Dateien, dynamische Importe und ``mock.patch``-Strings wie pytest-impacted.
- Node: lokaler ESM-Läufer in ``node_modules/vitest`` + ``node_modules/.bin/vitest`` (und
  ``vitest.cmd`` unter Windows), der ``vitest.config.js`` (``setupFiles``) lädt, bei ``--changed``
  über ``git diff`` + statischen ES-Import-Graph auswählt und echte Tests in ``node`` ausführt;
  ``adapter_node.ADAPTER`` läuft unverändert über echtes ``npm test``.
"""

from __future__ import annotations

import ast
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import attrappe
from to_spawn.pruef import adapter_py
from to_spawn.pruef.leiter import Plan

WURZEL = Path(__file__).resolve().parents[1]
MESSUNG = WURZEL / "messung"

#: Ordner im Probe-Repo, die nicht zum statischen Python-Import-Graph gehören.
_IGNORIERT = frozenset({".git", ".venv", "venv", "__pycache__", ".to-spawn", "deps", "node_modules"})


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.org", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return erg.stdout


def hat_pytest_impacted() -> bool:
    """True, wenn das echte Paket ``pytest-impacted`` im laufenden Interpreter installiert ist."""
    try:
        importlib.metadata.version("pytest-impacted")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _modulname(rel: Path) -> str:
    teile = list(rel.with_suffix("").parts)
    if teile and teile[-1] == "__init__":
        teile = teile[:-1]
    return ".".join(teile)


def _statische_importe(datei: Path, modul: str, bekannt: set[str]) -> set[str]:
    """Statische Python-Importe einer Datei (AST); dynamische Importe und Strings bleiben unsichtbar."""
    try:
        baum = ast.parse(datei.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    ist_paket = datei.name == "__init__.py"
    basis_teile = modul.split(".") if ist_paket else modul.split(".")[:-1]
    ziele: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for eintrag in knoten.names:
                if eintrag.name in bekannt:
                    ziele.add(eintrag.name)
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.level:
                hoch = len(basis_teile) - (knoten.level - 1)
                vorsatz = basis_teile[: max(hoch, 0)]
                mod = ".".join([*vorsatz, knoten.module]) if knoten.module else ".".join(vorsatz)
            else:
                mod = knoten.module or ""
            if mod in bekannt:
                ziele.add(mod)
            for eintrag in knoten.names:
                kandidat = f"{mod}.{eintrag.name}" if mod else eintrag.name
                if kandidat in bekannt:
                    ziele.add(kandidat)
    return ziele


def _betroffene_py_tests(repo: Path, basis: str) -> set[str]:
    """Betroffene ``tests/test_*.py``-Pfade (POSIX) aus ``git diff basis..HEAD`` + AST-Import-Graph."""
    raus = _git(repo, "diff", "--name-only", "--no-renames", "-z", basis, "--")
    geaendert = {p for p in raus.split("\0") if p}
    py_dateien = [p for p in repo.rglob("*.py") if not any(teil in _IGNORIERT for teil in p.relative_to(repo).parts)]
    pfad_zu_modul = {p.relative_to(repo).as_posix(): _modulname(p.relative_to(repo)) for p in py_dateien}
    modul_zu_pfad = {mod: pfad for pfad, mod in pfad_zu_modul.items() if mod}
    bekannt = set(modul_zu_pfad)

    # Rückwärts-Kanten: modul -> alle Module, die es statisch importieren.
    nutzer: dict[str, set[str]] = {m: set() for m in bekannt}
    for pfad, mod in pfad_zu_modul.items():
        if not mod:
            continue
        for ziel in _statische_importe(repo / pfad, mod, bekannt):
            nutzer[ziel].add(mod)

    betroffen = {pfad_zu_modul[p] for p in geaendert if pfad_zu_modul.get(p)}
    warteschlange = list(betroffen)
    while warteschlange:
        aktuell = warteschlange.pop()
        for abhaengig in nutzer.get(aktuell, ()):
            if abhaengig not in betroffen:
                betroffen.add(abhaengig)
                warteschlange.append(abhaengig)
    return {
        pfad
        for pfad, mod in pfad_zu_modul.items()
        if mod in betroffen and pfad.startswith("tests/") and Path(pfad).name.startswith("test_")
    }


class AstAuswahlAdapter(adapter_py.PythonAdapter):
    """Echter ``PythonAdapter``, der ohne ``pytest-impacted`` über AST-Import-Graph + ``--deselect`` wählt."""

    def auswahl(self, repo: Path, basis: str) -> Plan:
        plan = self.voll(repo)
        if plan.fehler:
            return plan
        alle_tests = sorted(p.relative_to(repo).as_posix() for p in (repo / "tests").rglob("test_*.py") if p.is_file())
        betroffen = _betroffene_py_tests(repo, basis)
        abgewaehlt = [p for p in alle_tests if p not in betroffen]
        return Plan(
            werkzeug=f"{plan.werkzeug}+ast-graph",
            befehl=[*plan.befehl, *(f"--deselect={p}" for p in abgewaehlt)],
            fehler="",
        )


# --- Lokaler ESM-Vitest-Läufer für Node-Repos ohne ``npm install`` ------------------------------

_VITEST_PKG = """{
  "name": "vitest",
  "version": "0.0.0-lokal",
  "type": "module",
  "exports": {
    ".": "./index.js",
    "./config": "./config.js"
  }
}
"""

_VITEST_CONFIG_JS = "export const defineConfig = (cfg) => cfg;\n"

_VITEST_INDEX_JS = """import assert from "node:assert/strict";

const liste = () => {
  if (!globalThis.__TO_SPAWN_VITEST_TESTS__) {
    globalThis.__TO_SPAWN_VITEST_TESTS__ = [];
  }
  return globalThis.__TO_SPAWN_VITEST_TESTS__;
};

export function test(name, fn) {
  liste().push({ name, fn });
}

test.each = (rows) => (tpl, fn) => {
  for (const row of rows) {
    const args = Array.isArray(row) ? row : [row];
    test(`${tpl} ${JSON.stringify(args)}`, () => fn(...args));
  }
};

export function expect(actual) {
  return {
    toBe(expected) {
      assert.strictEqual(actual, expected);
    },
    toEqual(expected) {
      assert.deepStrictEqual(actual, expected);
    },
    rejects: {
      async toThrow() {
        let geworfen = false;
        try {
          await actual;
        } catch {
          geworfen = true;
        }
        if (!geworfen) {
          throw new Error("Promise hat nicht geworfen");
        }
      },
    },
  };
}
"""

_VITEST_CLI_MJS = r"""import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const wurzel = process.cwd();
const args = process.argv.slice(2);
const idx = args.indexOf("--changed");
const basis = idx >= 0 && args[idx + 1] ? args[idx + 1] : null;
const cliExcludes = [];
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === "--exclude" && args[i + 1]) {
    cliExcludes.push(args[i + 1]);
    i += 1;
  } else if (args[i].startsWith("--exclude=")) {
    cliExcludes.push(args[i].slice("--exclude=".length));
  }
}

const posix = (p) => p.split(path.sep).join("/");

function globRegExp(muster) {
  let re = "^";
  for (let i = 0; i < muster.length; i += 1) {
    if (muster.startsWith("**/", i)) {
      re += "(?:.*/)?";
      i += 2;
    } else if (muster.startsWith("**", i)) {
      re += ".*";
      i += 1;
    } else if (muster[i] === "*") {
      re += "[^/]*";
    } else if (muster[i] === "?") {
      re += "[^/]";
    } else {
      re += muster[i].replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(re + "$");
}

const konfigPfad = path.join(wurzel, "vitest.config.js");
const konfig = fs.existsSync(konfigPfad)
  ? (await import(pathToFileURL(konfigPfad).href)).default ?? {}
  : {};
const konfigExcludes = Array.isArray(konfig?.test?.exclude)
  ? konfig.test.exclude
  : ["**/node_modules/**", "**/.git/**"];
const ausschluss = [...konfigExcludes, ...cliExcludes].map(globRegExp);
const istAusgeschlossen = (relPfad) => ausschluss.some((re) => re.test(relPfad));

function sammleDateien(ordner, endung, ziel = []) {
  if (!fs.existsSync(ordner)) return ziel;
  for (const eintrag of fs.readdirSync(ordner, { withFileTypes: true })) {
    if (eintrag.name === "node_modules" || eintrag.name === ".git") {
      continue;
    }
    const voll = path.join(ordner, eintrag.name);
    const rel = posix(path.relative(wurzel, voll));
    if (eintrag.isDirectory()) {
      if (!istAusgeschlossen(`${rel}/`)) {
        sammleDateien(voll, endung, ziel);
      }
    } else if (eintrag.name.endsWith(endung) && !istAusgeschlossen(rel)) {
      ziel.push(rel);
    }
  }
  return ziel;
}

function statischeImporte(relPfad) {
  const text = fs.readFileSync(path.join(wurzel, relPfad), "utf8");
  const muster = /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?["'](\.[^"']+)["']/g;
  const ziele = new Set();
  const basisOrdner = path.posix.dirname(relPfad);
  for (const treffer of text.matchAll(muster)) {
    ziele.add(path.posix.normalize(path.posix.join(basisOrdner, treffer[1])));
  }
  return ziele;
}

const alleTests = sammleDateien(wurzel, ".test.js").sort();
let gewaehlt = alleTests;

if (basis !== null) {
  const diff = execFileSync(
    "git",
    ["diff", "--name-only", "--no-renames", "-z", basis, "--"],
    { cwd: wurzel, encoding: "utf8" },
  );
  const geaendert = new Set(diff.split("\0").filter(Boolean).map(posix));
  const alleJs = sammleDateien(wurzel, ".js");
  const nutzer = new Map();
  for (const datei of alleJs) {
    for (const ziel of statischeImporte(datei)) {
      if (!nutzer.has(ziel)) nutzer.set(ziel, new Set());
      nutzer.get(ziel).add(datei);
    }
  }
  const betroffen = new Set(geaendert);
  const schlange = [...betroffen];
  while (schlange.length > 0) {
    const aktuell = schlange.pop();
    for (const abhaengig of nutzer.get(aktuell) ?? []) {
      if (!betroffen.has(abhaengig)) {
        betroffen.add(abhaengig);
        schlange.push(abhaengig);
      }
    }
  }
  gewaehlt = alleTests.filter((t) => betroffen.has(t));
  if (gewaehlt.length === 0) {
    console.log("No test files found, exiting with code 0");
    process.exit(0);
  }
}

for (const setup of konfig?.test?.setupFiles ?? []) {
  await import(pathToFileURL(path.resolve(wurzel, setup)).href);
}

let bestanden = 0;
let rot = 0;
for (const testDatei of gewaehlt) {
  globalThis.__TO_SPAWN_VITEST_TESTS__ = [];
  await import(pathToFileURL(path.join(wurzel, testDatei)).href);
  for (const fall of globalThis.__TO_SPAWN_VITEST_TESTS__) {
    try {
      await fall.fn();
      bestanden += 1;
    } catch (fehler) {
      rot += 1;
      console.error(`FAIL ${testDatei} > ${fall.name}: ${fehler?.message ?? fehler}`);
    }
  }
}

const gesamt = bestanden + rot;
if (rot > 0) {
  console.log(`      Tests  ${rot} failed | ${bestanden} passed (${gesamt})`);
  process.exit(1);
}
console.log(`      Tests  ${bestanden} passed (${gesamt})`);
"""


def installiere_lokales_vitest(repo: Path) -> None:
    """Schreibt den lokalen ESM-Vitest-Läufer unter ``repo/node_modules/`` (Linux + Windows)."""
    vitest_dir = repo / "node_modules" / "vitest"
    bin_dir = repo / "node_modules" / ".bin"
    vitest_dir.mkdir(parents=True, exist_ok=True)
    (vitest_dir / "package.json").write_text(_VITEST_PKG, encoding="utf-8")
    (vitest_dir / "config.js").write_text(_VITEST_CONFIG_JS, encoding="utf-8")
    (vitest_dir / "index.js").write_text(_VITEST_INDEX_JS, encoding="utf-8")
    (vitest_dir / "cli.mjs").write_text(_VITEST_CLI_MJS, encoding="utf-8")
    attrappe(
        bin_dir,
        "vitest",
        """
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        cli = Path(__file__).resolve().parents[1] / "vitest" / "cli.mjs"
        node = shutil.which("node") or "node"
        raise SystemExit(subprocess.call([node, str(cli), *sys.argv[1:]]))
        """,
    )


def baue_probe_repos(ziel: Path) -> dict[str, Path]:
    """Erzeugt echte Probe-Git-Repos ``py`` + ``node`` aus ``messung/gen_*.py`` (Tag ``base``)."""
    umgebung = {**os.environ, "PYTHONUTF8": "1"}
    for skript in ("gen_py.py", "gen_node.py"):
        subprocess.run(
            [sys.executable, str(MESSUNG / skript), "--ziel", str(ziel)],
            check=True,
            capture_output=True,
            env=umgebung,
        )
    repos: dict[str, Path] = {}
    for name in ("py", "node"):
        repo = ziel / name
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "probe@example.org")
        _git(repo, "config", "user.name", "probe")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "tag", "base")
        repos[name] = repo

    node_repo = repos["node"]
    installiere_lokales_vitest(node_repo)
    greetlib_ziel = node_repo / "node_modules" / "greetlib"
    shutil.copytree(node_repo / "deps" / "greetlib-1", greetlib_ziel)
    return repos
