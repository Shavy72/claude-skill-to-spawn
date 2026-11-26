"""Stufe 3 nur betroffene Tests + Rückfall-Regeln R1–R3 (L3 3b-2a, #101, #218).

Regel-Tests und Abnahme-Proben (M11 Fall 2 + 3, M12 a, Rot-Beweis R1) laufen überall ohne
Umgebungsvariable: fehlt ``TO_SPAWN_PROBE``, erzeugt die Fixture echte Git-Repos direkt aus
``messung/gen_py.py`` und ``messung/gen_node.py`` (mit lokalem AST-Import-Graph-Adapter bzw.
lokalem ESM-Vitest-Läufer, falls ``pytest-impacted``/``vitest`` nicht installiert sind). Mit
gesetztem ``TO_SPAWN_PROBE`` laufen sie unverändert gegen den Probe-Ordner aus
``messung/README.md``. Die alte Reihe 34 kommt aus ``messung/fehler_reihe.py`` (eine Quelle, per
``ast`` gelesen, nicht kopiert).
"""

from __future__ import annotations

import ast
import functools
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pruef_probe_helfer as probe_helfer
import pytest

from to_spawn import pruef
from to_spawn.pruef import adapter_node, cache, protokoll, regeln

REIHE = Path(__file__).resolve().parents[1] / "messung" / "fehler_reihe.py"
PROBE = Path(os.environ["TO_SPAWN_PROBE"]) if os.environ.get("TO_SPAWN_PROBE") else None
#: Fehler der alten Reihe, die ein neues Paket bräuchten (``uv pip install``/``npm install``): hier
#: zählt nur, dass R3 voll prüft; dass voll sie rot sieht, belegt ``messung/ergebnis_*.json``.
NEU_INSTALLIEREN = {"P19", "N10"}


# --- Regeln (überall) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pfad", "adapter", "anfang"),
    [
        ("app\\data\\catalog.json", "py", "R1"),
        ("app\\templates\\welcome.txt", "py", "R1"),
        ("app\\settings.toml", "py", "R1"),
        ("src\\data\\stock.json", "node", "R1"),
        ("src\\math.js", "py", "R1"),
        ("tests\\conftest.py", "py", "R3"),
        ("requirements.txt", "py", "R3"),
        ("sub\\requirements-dev.txt", "py", "R3"),
        ("pytest.ini", "py", "R3"),
        ("package.json", "node", "R3"),
        ("vitest.config.js", "node", "R3"),
        ("test\\setup.js", "node", "R3"),
        ("app\\util\\text.py", "py", ""),
        ("src\\format.js", "node", ""),
        # Fix-Runde 1: ohne erklärte Doku (sofort.erklaerte_doku) ist auch .md/.txt/Bild kein Doku.
        ("docs\\handbuch.txt", "py", "R1"),
        ("README.md", "node", "R1"),
        (".\\bild.PNG", "py", "R1"),
    ],
)
def test_regeln_mit_windows_pfaden(pfad: str, adapter: str, anfang: str) -> None:
    grund = regeln.vorab([pfad], adapter, [])
    assert grund.startswith(anfang) if anfang else grund == ""


def test_txt_ausserhalb_docs_ist_keine_doku() -> None:
    assert regeln.vorab(["notes.txt"], "py", []).startswith("R1")


def test_risiko_pfade_und_unklare_lagen_pruefen_voll() -> None:
    assert regeln.vorab(["app\\kern\\x.py"], "py", ["app/kern/*"]).startswith("R3")
    assert regeln.vorab(["app/x.py"], "py", None).startswith("R3")
    assert regeln.vorab(None, "py", []) != ""
    assert regeln.vorab([], "py", []) != ""
    assert regeln.vorab(["app/x.py"], "py", [], "rot").startswith("LF")
    assert regeln.vorab(["app/x.py"], "py", [], "grün") == ""


def test_r2_nur_bei_code_aenderung() -> None:
    assert regeln.nach_auswahl(0, ["app\\x.py"], "py").startswith("R2")
    assert regeln.nach_auswahl(None, ["README.md"], "py").startswith("R2")
    assert regeln.nach_auswahl(0, ["README.md"], "py", {"README.md"}) == ""
    assert regeln.nach_auswahl(0, ["README.md"], "py").startswith("R2")  # Fix-Runde 1: nicht erklärt
    assert regeln.nach_auswahl(3, ["app/x.py"], "py") == ""


def test_erklaerte_doku_laesst_auswahl_zu_windows_pfade() -> None:
    assert regeln.vorab(["docs\\handbuch.txt", "app\\x.py"], "py", [], doku={"docs/handbuch.txt"}) == ""
    assert regeln.vorab([".\\bild.PNG"], "py", [], doku={"bild.PNG"}) == ""
    assert regeln.vorab(["app/x.py"], "py", [], offen=["app/y.py"]).startswith("AB")
    assert regeln.vorab(["app/x.py"], "py", [], offen=None).startswith("AB")


def test_zaehle_alles_abgewaehlt_ist_null_tests() -> None:
    from to_spawn.pruef import adapter_node, adapter_py

    assert adapter_py.ADAPTER.zaehle(0, "pytest-impacted: 122 tests not impacted\n122 deselected in 0.17s\n") == (
        0,
        122,
    )
    assert adapter_py.ADAPTER.zaehle(5, "no tests ran") == (0, None)
    assert adapter_py.ADAPTER.zaehle(0, "..  [ 2/2]\n2 passed, 120 deselected in 0.2s\n") == (2, 122)
    assert adapter_node.ADAPTER.zaehle(0, "      Tests  3 passed (3)\n") == (3, None)
    assert adapter_node.ADAPTER.zaehle(0, "No test files found, exiting with code 0") == (0, None)


def test_risiko_pfade_aus_konfig(tmp_path: Path) -> None:
    assert regeln.risiko_pfade(tmp_path) == []
    (tmp_path / ".to-spawn").mkdir()
    datei = tmp_path / ".to-spawn" / "config.json"
    datei.write_text('{"pruefen": {"risiko_pfade": ["web/*.py"]}}', encoding="utf-8")
    assert regeln.risiko_pfade(tmp_path) == ["web/*.py"]
    datei.write_text("{kaputt", encoding="utf-8")
    assert regeln.risiko_pfade(tmp_path) is None


def test_regeln_stand_ist_git_blob_sha() -> None:
    datei = Path(regeln.__file__)
    erwartet = subprocess.run(["git", "hash-object", str(datei)], capture_output=True, text=True, check=True)
    assert regeln.stand() == "regeln@" + erwartet.stdout.strip()[:7]


# --- Proben (TO_SPAWN_PROBE) ----------------------------------------------------------------------


def _reihe(name: str) -> list[tuple[str, str, str, str, str]]:
    baum = ast.parse(REIHE.read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and any(getattr(z, "id", "") == name for z in knoten.targets):
            return ast.literal_eval(knoten.value)
    raise AssertionError(f"{name} fehlt in {REIHE}")


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ordner, check=True, capture_output=True)


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    ziel = tmp_path_factory.mktemp("probe")
    if PROBE is not None:
        repos = {}
        for name, link in (("py", ".venv"), ("node", "node_modules")):
            repo = ziel / name
            subprocess.run(["git", "clone", "-q", str(PROBE / name), str(repo)], check=True)
            quelle = PROBE / ".venv" if name == "py" else PROBE / name / "node_modules"
            (repo / link).symlink_to(quelle, target_is_directory=True)
            with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as datei:
                datei.write(f"/{link}\n")
            _git(repo, "config", "user.email", "probe@example.org")
            _git(repo, "config", "user.name", "probe")
            repos[name] = repo
        yield repos
        return

    repos = probe_helfer.baue_probe_repos(ziel)
    py_repo = repos["py"]
    alt_pypath = os.environ.get("PYTHONPATH", "")
    pypath = os.pathsep.join(teil for teil in (str(py_repo), str(py_repo / "deps" / "greetlib-1"), alt_pypath) if teil)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PYTHONPATH", pypath)
        if not probe_helfer.hat_pytest_impacted():
            mp.setattr(pruef, "_ADAPTER", (probe_helfer.AstAuswahlAdapter(), adapter_node.ADAPTER))
        yield repos


def _fall(repo: Path, pfad: str, alt: str, neu: str) -> dict:
    """Fehler auf ``base`` einbauen, committen, prüfen; Protokoll vorher leer (kein LF-Übertrag)."""
    _git(repo, "checkout", "-qf", "--detach", "base")
    _git(repo, "clean", "-fdq")
    datei = repo / pfad
    text = datei.read_text(encoding="utf-8")
    assert alt in text, f"Patch passt nicht: {pfad}"
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    _git(repo, "commit", "-qam", f"fehler {pfad}")
    (repo / protokoll.ORDNER / protokoll.DATEI).unlink(missing_ok=True)
    urteil = pruef.pruefen(repo, "base", "HEAD")
    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    return {"exit": urteil.exit, "stufe": stufe, "zeile": urteil.zeile}


@pytest.fixture(autouse=True)
def _kein_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "treffer", lambda _schluessel: False)
    monkeypatch.setattr(cache, "merke", lambda *_a, **_k: None)


@pytest.mark.parametrize(
    ("name", "pfad", "alt", "neu"),
    [
        ("py", "app/util/dates.py", "d.weekday() >= 5", "d.weekday() in (5, 6)"),
        ("node", "src/format.js", 'toFixed(2) + " €"', 'toFixed(2) + " " + "€"'),
    ],
)
def test_m11_fall2_kleine_aenderung_waehlt_aus(
    probe: dict[str, Path], name: str, pfad: str, alt: str, neu: str
) -> None:
    erg = _fall(probe[name], pfad, alt, neu)
    wahl = erg["zeile"]["auswahl"][0]
    assert (erg["exit"], erg["stufe"]["name"], wahl["sicher"]) == (0, "auswahl", True)
    assert wahl["n"] > 0
    if wahl["gesamt"] is not None:
        assert wahl["n"] < wahl["gesamt"]
    assert any(w.startswith("regeln@") for w in erg["zeile"]["werkzeug"])


@pytest.mark.parametrize(
    ("name", "pfad", "alt", "neu"),
    [
        ("py", "requirements.txt", "pytest\n", "pytest>=8\n"),
        ("node", "package.json", '"private": true', '"private": true,\n  "description": "x"'),
    ],
)
def test_m11_fall3_abhaengigkeit_prueft_voll_r3(
    probe: dict[str, Path], name: str, pfad: str, alt: str, neu: str
) -> None:
    erg = _fall(probe[name], pfad, alt, neu)
    assert (erg["exit"], erg["stufe"]["name"]) == (0, "voll")
    assert erg["stufe"]["grund"].startswith("R3")
    assert erg["zeile"]["auswahl"][0]["sicher"] is False


def _uebersehen(probe: dict[str, Path], faelle: list[tuple[str, str, str, str, str, str]]) -> list[str]:
    fehlt = []
    for name, fid, _kat, pfad, alt, neu in faelle:
        erg = _fall(probe[name], pfad, alt, neu)
        if fid in NEU_INSTALLIEREN:
            ok = erg["stufe"]["name"] == "voll" and erg["stufe"]["grund"].startswith("R3")
        else:
            ok = erg["exit"] == 1
        if not ok:
            fehlt.append(fid)
    return fehlt


def _alle() -> list[tuple[str, str, str, str, str, str]]:
    return [("py", *f) for f in _reihe("PY_FEHLER")] + [("node", *f) for f in _reihe("NODE_FEHLER")]


def test_m12a_alte_reihe_34_nichts_uebersehen(probe: dict[str, Path]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    faelle = _alle()
    assert len(faelle) == 34
    # #353: Python- und Node-Probe-Repos sind getrennte Arbeitsordner — parallel halbiert die Laufzeit.
    py_faelle = [f for f in faelle if f[0] == "py"]
    node_faelle = [f for f in faelle if f[0] == "node"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_py = pool.submit(_uebersehen, probe, py_faelle)
        fut_node = pool.submit(_uebersehen, probe, node_faelle)
        assert fut_py.result() + fut_node.result() == []


def test_rot_beweis_ohne_r1_wird_bekannter_fall_uebersehen(
    probe: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(regeln, "vorab", functools.partial(regeln.vorab, r1=False))
    # Fix-Runde 1: auch das 0-Tests-Netz aus (alles gilt als erklärte Doku), sonst fängt es R1-Fälle.
    nach = regeln.nach_auswahl
    monkeypatch.setattr(regeln, "nach_auswahl", lambda n, pfade, adapter, doku=(): nach(n, pfade, adapter, pfade))
    erwartet = {"P09", "P11", "P18", "N05"}
    r1_faelle = [f for f in _alle() if f[1] in erwartet]
    assert set(_uebersehen(probe, r1_faelle)) == erwartet


def test_regeln_stand_unabhaengig_von_zeilenende(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"a = 1\nb = 2\n")
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    monkeypatch.setattr(regeln, "__file__", str(lf))
    stand_lf = regeln.stand()
    monkeypatch.setattr(regeln, "__file__", str(crlf))
    assert regeln.stand() == stand_lf
