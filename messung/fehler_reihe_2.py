"""M12-Probe: zweite, vorher unbekannte Fehlerreihe (≥ 20 Fehler, Py + Node, #104).

Gegenprobe gegen Überanpassung der eingefrorenen Rückfall-Regeln (``to_spawn/pruef/regeln.py``):
Alle 26 Fehler (16 Python, 10 Node) wurden von einem frischen, unabhängigen Helfer allein auf Basis
von ``messung/gen_py.py`` und ``messung/gen_node.py`` ohne Kenntnis von ``regeln.py``, R1–R4 oder
``messung/fehler_reihe.py`` entworfen.

Für jeden Fehler wird ausschließlich in einem isolierten Temp-/Probe-Repo geprüft:
1. Voller Testlauf (Referenz: muss rot sein).
2. Rohe statische Auswahl ohne R1–R3 (Gegenprobe: zeigt Lücken bei Daten, Template, dyn. Import,
   ``mock.patch``-String).
3. Vollständige Prüf-Tür ``to_spawn.pruef.pruefen`` mit eingefrorenem Regel-Stand (``regeln@<sha>``):
   Abnahme verlangt ``voll rot ⇒ pruefen rot`` mit **0 übersehen**.

Aufruf:
  python messung/fehler_reihe_2.py --ort <probe-ordner> [--nur py|node] [--aus ergebnis.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_WURZEL = Path(__file__).resolve().parents[1]
if str(_WURZEL) not in sys.path:
    sys.path.insert(0, str(_WURZEL))

from to_spawn import prozessbaum
from to_spawn.pruef import adapter_node, adapter_py, ausfuehren, cache, leiter, protokoll, regeln

log = logging.getLogger("fehler_reihe_2")

#: Eingefrorener Stand von ``to_spawn/pruef/regeln.py`` vor dieser Messreihe (#104).
EINGEFROREN_REGELN_COMMIT = "6427e5eb6c5e31a19a3c637b2c17f57a570af160"
EINGEFROREN_REGELN_STAND = "regeln@fd7e49f"

#: Vier Pflicht-Fehlerklassen laut Issue #104 / Schnitt 3b-2b.
PFLICHT_KLASSEN: tuple[str, ...] = ("daten", "template", "dyn_import", "mock_patch")

#: Last-Grenze für Voll-Läufe auf dem Bau-Server (Linux; auf Windows ohne loadavg nicht anwendbar).
STANDARD_MAX_LAST = 12.0


@dataclass(frozen=True)
class FehlerFall:
    """Einzelner, reproduzierbarer Fehler-Einbau auf Basis des Probe-Repos."""

    id: str
    repo: Literal["py", "node"]
    kategorie: str
    klassen: tuple[str, ...]
    datei: str
    alt: str
    neu: str
    erwarteter_test: str


#: 16 Python-Fehler (Q01–Q16), entworfen vom frischen Helfer ohne Kenntnis von regeln.py/R1–R4.
PY_FEHLER: tuple[FehlerFall, ...] = (
    FehlerFall(
        id="Q01",
        repo="py",
        kategorie="Daten-Datei (JSON, per Dateisystem gelesen)",
        klassen=("daten",),
        datei="app/data/catalog.json",
        alt='"B2": 25.5',
        neu='"B2": 26.0',
        erwarteter_test="tests/test_catalog.py::test_price, tests/test_orders.py::test_total_de",
    ),
    FehlerFall(
        id="Q02",
        repo="py",
        kategorie="Template (Textvorlage)",
        klassen=("template",),
        datei="app/templates/welcome.txt",
        alt="{greeting}! Willkommen bei {shop}, {name}.",
        neu="{greeting}! Willkommen bei {shop}: {name}!",
        erwarteter_test="tests/test_render.py::test_welcome_contains_name, tests/test_render.py::test_welcome_full",
    ),
    FehlerFall(
        id="Q03",
        repo="py",
        kategorie="App-Konfiguration (TOML)",
        klassen=("konfig",),
        datei="app/settings.toml",
        alt="max_items = 5",
        neu="max_items = 4",
        erwarteter_test="tests/test_config.py::test_max_items, tests/test_orders.py::test_max_ok",
    ),
    FehlerFall(
        id="Q04",
        repo="py",
        kategorie="Prüf-Konfiguration (pytest.ini)",
        klassen=("pruefkonfig",),
        datei="pytest.ini",
        alt="addopts = -p no:cacheprovider",
        neu="addopts = -p no:cacheprovider\nfilterwarnings = error::DeprecationWarning",
        erwarteter_test="tests/test_legacy.py::test_old_total, tests/test_legacy.py::test_old_total_empty",
    ),
    FehlerFall(
        id="Q05",
        repo="py",
        kategorie="Test-Setup (conftest.py-Fixture)",
        klassen=("conftest",),
        datei="tests/conftest.py",
        alt='return User("Anna Muster", "anna@example.org", "DE")',
        neu='return User("Anna Muster", "anna@example.org", "AT")',
        erwarteter_test="tests/test_orders.py::test_country_de, tests/test_orders.py::test_total_de",
    ),
    FehlerFall(
        id="Q06",
        repo="py",
        kategorie="Dynamischer Import (Plugin-Modul CSV)",
        klassen=("dyn_import",),
        datei="app/plugins/csv_export.py",
        alt='DELIM = ","',
        neu='DELIM = ";"',
        erwarteter_test="tests/test_plugins.py::test_csv",
    ),
    FehlerFall(
        id="Q07",
        repo="py",
        kategorie="mock.patch-String & dynamisches Plugin (Attribut umbenannt)",
        klassen=("mock_patch", "dyn_import"),
        datei="app/plugins/json_export.py",
        alt=(
            "INDENT = None\n\n\n"
            "def render(rows: list[list[str]]) -> str:\n"
            "    return json.dumps(rows, indent=INDENT, sort_keys=True)"
        ),
        neu=(
            "SPACES = None\n\n\n"
            "def render(rows: list[list[str]]) -> str:\n"
            "    return json.dumps(rows, indent=SPACES, sort_keys=True)"
        ),
        erwarteter_test="tests/test_plugins.py::test_json_patched_indent",
    ),
    FehlerFall(
        id="Q08",
        repo="py",
        kategorie="Dynamischer Import (Plugin-Lader)",
        klassen=("dyn_import",),
        datei="app/plugins/__init__.py",
        alt='return importlib.import_module(f"app.plugins.{name}")',
        neu='return importlib.import_module(f"app.plugins.{name}_plugin")',
        erwarteter_test="tests/test_plugins.py::test_csv, tests/test_plugins.py::test_json",
    ),
    FehlerFall(
        id="Q09",
        repo="py",
        kategorie="mock.patch-String & Benachrichtigung (direkte Symbol-Bindung)",
        klassen=("mock_patch",),
        datei="app/notify.py",
        alt=(
            "from app import mailer\n"
            "from app.render import render_welcome\n\n\n"
            "def welcome(name: str, email: str) -> dict:\n"
            '    return mailer.send(email, "Willkommen", render_welcome(name))'
        ),
        neu=(
            "from app.mailer import send\n"
            "from app.render import render_welcome\n\n\n"
            "def welcome(name: str, email: str) -> dict:\n"
            '    return send(email, "Willkommen", render_welcome(name))'
        ),
        erwarteter_test="tests/test_notify.py::test_welcome_patched",
    ),
    FehlerFall(
        id="Q10",
        repo="py",
        kategorie="Blatt-Hilfsfunktion (Textkürzung, Grenzfall)",
        klassen=("code",),
        datei="app/util/text.py",
        alt='return s if len(s) <= n else s[: n - 1] + "…"',
        neu='return s if len(s) < n else s[: n - 1] + "…"',
        erwarteter_test="tests/test_text.py::test_truncate",
    ),
    FehlerFall(
        id="Q11",
        repo="py",
        kategorie="Fachlogik (Steuersatz)",
        klassen=("code",),
        datei="app/tax.py",
        alt='"CH": 0.081',
        neu='"CH": 0.077',
        erwarteter_test="tests/test_tax.py::test_vat",
    ),
    FehlerFall(
        id="Q12",
        repo="py",
        kategorie="Fachlogik (Preis- und Rabattberechnung)",
        klassen=("code",),
        datei="app/pricing.py",
        alt="    net = discount(sum(prices), pct)\n    return round(net + vat(net, country), 2)",
        neu="    gross = sum(prices)\n    return round(discount(gross, pct) + vat(gross, country), 2)",
        erwarteter_test="tests/test_pricing.py::test_total, tests/test_orders.py::test_discount",
    ),
    FehlerFall(
        id="Q13",
        repo="py",
        kategorie="Datenmodell (Dataclass-Feldreihenfolge)",
        klassen=("code",),
        datei="app/models.py",
        alt="    name: str\n    email: str\n    country: str",
        neu="    email: str\n    name: str\n    country: str",
        erwarteter_test="tests/test_orders.py::test_user_kept, tests/test_users.py::test_register_ok",
    ),
    FehlerFall(
        id="Q14",
        repo="py",
        kategorie="Validator (Ländercode-Groß-/Kleinschreibung)",
        klassen=("code",),
        datei="app/validators.py",
        alt="return c in RATES",
        neu="return c.upper() in RATES",
        erwarteter_test="tests/test_validators.py::test_country",
    ),
    FehlerFall(
        id="Q15",
        repo="py",
        kategorie="Service (Bestellgrenze, Off-by-one)",
        klassen=("code",),
        datei="app/services/orders.py",
        alt='if len(skus) > config.get("max_items"):',
        neu='if len(skus) >= config.get("max_items"):',
        erwarteter_test="tests/test_orders.py::test_max_ok",
    ),
    FehlerFall(
        id="Q16",
        repo="py",
        kategorie="Re-Export (Paket-Schnittstelle)",
        klassen=("reexport",),
        datei="app/__init__.py",
        alt="from app.util.text import slug",
        neu="from app.util.text import title_case as slug",
        erwarteter_test="tests/test_init.py::test_slug_reexport",
    ),
)

#: 10 Node-Fehler (M01–M10), entworfen vom frischen Helfer ohne Kenntnis von regeln.py/R1–R4.
NODE_FEHLER: tuple[FehlerFall, ...] = (
    FehlerFall(
        id="M01",
        repo="node",
        kategorie="Daten-Datei (JSON, statisch importiert)",
        klassen=("daten",),
        datei="src/data/prices.json",
        alt='"A1": 10',
        neu='"A1": 12',
        erwarteter_test="test/cart.test.js, test/index.test.js",
    ),
    FehlerFall(
        id="M02",
        repo="node",
        kategorie="Daten-Datei (JSON, per Dateisystem gelesen)",
        klassen=("daten",),
        datei="src/data/stock.json",
        alt='"B2": 0',
        neu='"B2": 2',
        erwarteter_test="test/stock.test.js",
    ),
    FehlerFall(
        id="M03",
        repo="node",
        kategorie="Prüf-Konfiguration (vitest.config.js)",
        klassen=("pruefkonfig",),
        datei="vitest.config.js",
        alt='setupFiles: ["./test/setup.js"]',
        neu="setupFiles: []",
        erwarteter_test="test/setup.test.js, test/greet.test.js",
    ),
    FehlerFall(
        id="M04",
        repo="node",
        kategorie="Test-Setup (globale Umgebung)",
        klassen=("setup",),
        datei="test/setup.js",
        alt="globalThis.SHOP = 'Probe-Shop';",
        neu="globalThis.SHOP = 'Test-Shop';",
        erwarteter_test="test/setup.test.js, test/greet.test.js",
    ),
    FehlerFall(
        id="M05",
        repo="node",
        kategorie="Dynamischer Import (Plugin-Modul reverse)",
        klassen=("dyn_import",),
        datei="src/plugins/reverse.js",
        alt='s.split("").reverse().join("")',
        neu='s.split("").reverse().join("-")',
        erwarteter_test="test/plugins.test.js",
    ),
    FehlerFall(
        id="M06",
        repo="node",
        kategorie="Dynamischer Import (Plugin-Lader)",
        klassen=("dyn_import",),
        datei="src/plugins/index.js",
        alt="return import(`./${name}.js`);",
        neu="return import(`./plugin_${name}.js`);",
        erwarteter_test="test/plugins.test.js",
    ),
    FehlerFall(
        id="M07",
        repo="node",
        kategorie="Blatt-Hilfsfunktion (Rundungslogik)",
        klassen=("code",),
        datei="src/math.js",
        alt="Math.round(x * 100) / 100",
        neu="Math.floor(x * 100) / 100",
        erwarteter_test="test/math.test.js",
    ),
    FehlerFall(
        id="M08",
        repo="node",
        kategorie="Blatt-Hilfsfunktion (Währungsformatierung)",
        klassen=("code",),
        datei="src/format.js",
        alt='x.toFixed(2) + " €"',
        neu='x.toFixed(2) + " EUR"',
        erwarteter_test="test/format.test.js",
    ),
    FehlerFall(
        id="M09",
        repo="node",
        kategorie="Fachlogik (Lagerbestandsprüfung, Grenzfall)",
        klassen=("code",),
        datei="src/stock.js",
        alt="export const inStock = (s) => stockOf(s) > 0;",
        neu="export const inStock = (s) => stockOf(s) >= 0;",
        erwarteter_test="test/stock.test.js",
    ),
    FehlerFall(
        id="M10",
        repo="node",
        kategorie="Re-Export (Modul-Schnittstelle)",
        klassen=("reexport",),
        datei="src/index.js",
        alt='export { total } from "./cart.js";',
        neu='export { priceOf as total } from "./cart.js";',
        erwarteter_test="test/index.test.js",
    ),
)

ALLE_FEHLER: tuple[FehlerFall, ...] = PY_FEHLER + NODE_FEHLER


def aktuelle_last() -> float | None:
    """1-Minuten-Systemlast unter POSIX; ``None`` auf Plattformen ohne ``os.getloadavg`` (Windows)."""
    if not hasattr(os, "getloadavg"):
        return None
    try:
        return float(os.getloadavg()[0])
    except OSError:
        return None


def _git(repo: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.org", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    return erg.stdout


def auf_basis_zuruecksetzen(repo: Path, basis: str = "base") -> None:
    """Setzt das isolierte Probe-Repo sauber auf ``basis`` zurück."""
    _git(repo, "checkout", "-qf", "--detach", basis)
    _git(repo, "clean", "-fdq")


def baue_fehler_ein(repo: Path, fall: FehlerFall, basis: str = "base") -> None:
    """Baut ``fall`` als einzelnen Commit auf ``basis`` ein und leert alte Prüf-Protokolle."""
    auf_basis_zuruecksetzen(repo, basis)
    datei = repo / fall.datei
    text = datei.read_text(encoding="utf-8")
    anzahl = text.count(fall.alt)
    if anzahl != 1:
        raise ValueError(f"Patch {fall.id} ({fall.datei}): erwartet 1 Treffer, gefunden {anzahl}")
    datei.write_text(text.replace(fall.alt, fall.neu, 1), encoding="utf-8")
    _git(repo, "commit", "-qam", f"fehler {fall.id}: {fall.datei}")
    (repo / protokoll.ORDNER / protokoll.DATEI).unlink(missing_ok=True)


def _adapter_fuer(art: Literal["py", "node"]) -> leiter.Adapter:
    return adapter_py.ADAPTER if art == "py" else adapter_node.ADAPTER


def voll_lauf(repo: Path, art: Literal["py", "node"]) -> dict[str, Any]:
    """Führt den vollständigen Testlauf über den echten Adapter aus (Referenz-Wahrheit)."""
    adapter = _adapter_fuer(art)
    plan = adapter.voll(repo)
    if plan.fehler:
        return {"exit": leiter.GATE_FEHLER, "rot": False, "n": None, "dauer_s": 0.0, "fehler": plan.fehler}
    erg = ausfuehren.lauf(plan.befehl, repo)
    code = adapter.deute(erg.code)
    n, _gesamt = adapter.zaehle(erg.code, erg.ausgabe)
    return {
        "exit": code,
        "rot": code == leiter.ROT,
        "n": n,
        "dauer_s": round(erg.dauer_s, 2),
        "fehler": "",
    }


def roh_auswahl_lauf(repo: Path, art: Literal["py", "node"], basis: str = "base") -> dict[str, Any]:
    """Führt die rohe statische Auswahl ohne R1–R3 aus (Gegenprobe)."""
    adapter = _adapter_fuer(art)
    plan = adapter.auswahl(repo, basis)
    if plan.fehler:
        return {"exit": leiter.GATE_FEHLER, "rot": False, "n": None, "dauer_s": 0.0, "fehler": plan.fehler}
    if not plan.befehl:
        return {"exit": leiter.GRUEN, "rot": False, "n": 0, "dauer_s": 0.0, "fehler": ""}
    erg = ausfuehren.lauf(plan.befehl, repo)
    code = adapter.deute(erg.code)
    n, _gesamt = adapter.zaehle(erg.code, erg.ausgabe)
    return {
        "exit": code,
        "rot": code == leiter.ROT,
        "n": n,
        "dauer_s": round(erg.dauer_s, 2),
        "fehler": "",
    }


@contextmanager
def _ohne_cache() -> Iterator[None]:
    """Deaktiviert Stufe-0-Cache während der Messung, damit jeder Fehler real geprüft wird."""
    alt_treffer = cache.treffer
    alt_merke = cache.merke
    cache.treffer = lambda _schluessel: False  # type: ignore[assignment]
    cache.merke = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    try:
        yield
    finally:
        cache.treffer = alt_treffer  # type: ignore[assignment]
        cache.merke = alt_merke  # type: ignore[assignment]


def pruefe_fall(repo: Path, fall: FehlerFall, basis: str = "base") -> dict[str, Any]:
    """Misst einen einzelnen Fehler gegen voll, rohe Auswahl und ``pruef.pruefen``."""
    from to_spawn import pruef

    baue_fehler_ein(repo, fall, basis)
    voll = voll_lauf(repo, fall.repo)
    roh = roh_auswahl_lauf(repo, fall.repo, basis)
    (repo / protokoll.ORDNER / protokoll.DATEI).unlink(missing_ok=True)
    urteil = pruef.pruefen(repo, basis, "HEAD")

    stufen = urteil.zeile.get("stufen", [])
    pruef_stufe = next(
        (s for s in stufen if s.get("name") in ("auswahl", "voll")),
        stufen[-1] if stufen else {},
    )
    auswahl_liste = urteil.zeile.get("auswahl") or []
    auswahl = auswahl_liste[0] if auswahl_liste else {}
    werkzeug = urteil.zeile.get("werkzeug") or []
    regeln_eintrag = next((w for w in werkzeug if isinstance(w, str) and w.startswith("regeln@")), "")

    return {
        "id": fall.id,
        "repo": fall.repo,
        "kategorie": fall.kategorie,
        "klassen": list(fall.klassen),
        "datei": fall.datei,
        "voll_exit": voll["exit"],
        "voll_rot": voll["rot"],
        "voll_n": voll["n"],
        "voll_s": voll["dauer_s"],
        "roh_exit": roh["exit"],
        "roh_rot": roh["rot"],
        "roh_n": roh["n"],
        "roh_s": roh["dauer_s"],
        "pruefen_exit": urteil.exit,
        "pruefen_rot": urteil.exit == leiter.ROT,
        "urteil": urteil.zeile.get("urteil", ""),
        "stufe": pruef_stufe.get("name", ""),
        "grund": pruef_stufe.get("grund", ""),
        "auswahl_n": auswahl.get("n"),
        "auswahl_sicher": auswahl.get("sicher"),
        "pruefen_s": urteil.zeile.get("gesamt_s", 0.0),
        "regeln_stand": regeln_eintrag,
    }


def messe_reihe(
    repos: dict[str, Path],
    faelle: Sequence[FehlerFall] = ALLE_FEHLER,
    *,
    basis: str = "base",
    max_last: float = STANDARD_MAX_LAST,
) -> dict[str, Any]:
    """Führt alle ``faelle`` in den isolierten Probe-Repos aus und aggregiert die M12-Auswertung."""
    if max_last > 0:
        last = aktuelle_last()
        if last is not None and last >= max_last:
            raise RuntimeError(
                f"System-Last {last:.2f} >= Grenze {max_last:.2f} — Voll-Läufe der M12-Messung abgebrochen"
            )

    stand_jetzt = regeln.stand()
    start = time.monotonic()

    py_faelle = [f for f in faelle if f.repo == "py"]
    node_faelle = [f for f in faelle if f.repo == "node"]
    reihenfolge = {f.id: idx for idx, f in enumerate(faelle)}

    def _arbeite(gruppe: list[FehlerFall]) -> list[dict[str, Any]]:
        if not gruppe:
            return []
        art = gruppe[0].repo
        repo = repos[art]
        zeilen: list[dict[str, Any]] = []
        try:
            for fall in gruppe:
                eintrag = pruefe_fall(repo, fall, basis)
                log.info(
                    "%s [%s] voll=%s roh=%s pruefen=%s (%s: %s)",
                    eintrag["id"],
                    eintrag["repo"],
                    "ROT" if eintrag["voll_rot"] else "grün",
                    "ROT" if eintrag["roh_rot"] else "grün",
                    "ROT" if eintrag["pruefen_rot"] else "grün",
                    eintrag["stufe"],
                    eintrag["grund"],
                )
                zeilen.append(eintrag)
        finally:
            auf_basis_zuruecksetzen(repo, basis)
        return zeilen

    with _ohne_cache():
        if py_faelle and node_faelle:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_py = pool.submit(_arbeite, py_faelle)
                fut_node = pool.submit(_arbeite, node_faelle)
                roh_ergebnisse = fut_py.result() + fut_node.result()
        else:
            roh_ergebnisse = _arbeite(py_faelle) + _arbeite(node_faelle)

    ergebnisse = sorted(roh_ergebnisse, key=lambda r: reihenfolge[r["id"]])
    uebersehen = [r["id"] for r in ergebnisse if r["voll_rot"] and not r["pruefen_rot"]]
    roh_uebersehen = [r["id"] for r in ergebnisse if r["voll_rot"] and not r["roh_rot"]]

    return {
        "regeln_stand": stand_jetzt,
        "regeln_commit": EINGEFROREN_REGELN_COMMIT,
        "erwartet_regeln_stand": EINGEFROREN_REGELN_STAND,
        "dauer_s": round(time.monotonic() - start, 2),
        "gesamt": len(ergebnisse),
        "py_gesamt": sum(1 for r in ergebnisse if r["repo"] == "py"),
        "node_gesamt": sum(1 for r in ergebnisse if r["repo"] == "node"),
        "voll_rot": sum(1 for r in ergebnisse if r["voll_rot"]),
        "pruefen_rot": sum(1 for r in ergebnisse if r["pruefen_rot"]),
        "uebersehen": uebersehen,
        "roh_uebersehen": roh_uebersehen,
        "faelle": ergebnisse,
    }


def formatiere_markdown(ergebnis: dict[str, Any]) -> str:
    """Formatiert das Messergebnis als kompakte Markdown-Tabelle (ohne Benutzerpfade/Namen)."""
    zeilen = [
        f"Regel-Stand (eingefroren): `{ergebnis['regeln_stand']}` (Commit `{ergebnis['regeln_commit'][:7]}`)",
        (
            f"Gesamt: **{ergebnis['gesamt']}** Fehler ({ergebnis['py_gesamt']} Python, "
            f"{ergebnis['node_gesamt']} Node) · voll rot: **{ergebnis['voll_rot']}** · "
            f"`pruefen` rot: **{ergebnis['pruefen_rot']}** · übersehen: **{len(ergebnis['uebersehen'])}** · "
            f"roh übersehen: **{len(ergebnis['roh_uebersehen'])}** ({', '.join(ergebnis['roh_uebersehen']) or '–'})"
        ),
        "",
        "| ID | Repo | Kategorie | Datei | voll | Auswahl roh | `pruefen` | Stufe / Grund |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in ergebnisse_liste(ergebnis):
        voll = "ROT" if r["voll_rot"] else "grün"
        roh = "ROT" if r["roh_rot"] else "grün"
        tuer = "ROT" if r["pruefen_rot"] else "grün"
        grund = r.get("grund", "")
        stufe = r.get("stufe", "")
        zeilen.append(
            f"| {r['id']} | {r['repo']} | {r['kategorie']} | `{r['datei']}` | {voll} | {roh} | {tuer} | "
            f"{stufe}: {grund} |"
        )
    return "\n".join(zeilen) + "\n"


def ergebnisse_liste(ergebnis: dict[str, Any]) -> list[dict[str, Any]]:
    """Liefert die Fall-Liste aus einem Messergebnis-Dict."""
    return list(ergebnis.get("faelle", []))


def _klone_isoliert(probe_ort: Path, ziel: Path, teile: Sequence[str]) -> dict[str, Path]:
    """Klont ``py``/``node`` aus ``probe_ort`` in einen frischen Temp-Ordner (Original bleibt unberührt)."""
    repos: dict[str, Path] = {}
    for name in teile:
        quelle_repo = probe_ort / name
        if not quelle_repo.is_dir():
            raise FileNotFoundError(f"Probe-Repo fehlt: {quelle_repo}")
        repo = ziel / name
        subprocess.run(
            ["git", "clone", "-q", str(quelle_repo), str(repo)],
            check=True,
            capture_output=True,
            **prozessbaum.fensterlos(),
        )
        link_name = ".venv" if name == "py" else "node_modules"
        quelle_link = probe_ort / ".venv" if name == "py" else quelle_repo / "node_modules"
        if quelle_link.is_dir():
            (repo / link_name).symlink_to(quelle_link, target_is_directory=True)
            with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
                datei.write(f"/{link_name}\n")
        repos[name] = repo
    return repos


def main(argv: Sequence[str] | None = None) -> int:
    """CLI für die zweite, unbekannte M12-Fehlerreihe (isoliert im Temp-Ordner)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="M12-Probe: zweite, unbekannte Fehlerreihe (Py + Node).")
    parser.add_argument("--ort", type=Path, required=True, help="Probe-Ordner mit py/ und node/ (wird nur geklont)")
    parser.add_argument("--nur", choices=("py", "node"), default=None, help="Optional nur Python oder nur Node messen")
    parser.add_argument("--aus", type=Path, default=None, help="Optional JSON-Ausgabedatei")
    parser.add_argument(
        "--max-last",
        type=float,
        default=STANDARD_MAX_LAST,
        help=f"Maximale 1-Min-Last für Voll-Läufe (Vorgabe: {STANDARD_MAX_LAST}, 0 = aus)",
    )
    args = parser.parse_args(argv)

    faelle = PY_FEHLER if args.nur == "py" else NODE_FEHLER if args.nur == "node" else ALLE_FEHLER
    teile = ("py",) if args.nur == "py" else ("node",) if args.nur == "node" else ("py", "node")

    with tempfile.TemporaryDirectory(prefix="m12_reihe2_") as tmp:
        repos = _klone_isoliert(args.ort.resolve(), Path(tmp), teile)
        ergebnis = messe_reihe(repos, faelle, max_last=args.max_last)

    if args.aus is not None:
        args.aus.parent.mkdir(parents=True, exist_ok=True)
        args.aus.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    sys.stdout.write(formatiere_markdown(ergebnis))
    return 0 if not ergebnis["uebersehen"] and ergebnis["voll_rot"] == ergebnis["gesamt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
