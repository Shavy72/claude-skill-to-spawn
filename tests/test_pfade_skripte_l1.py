"""L1 Etappe 1 P3b: Skripte lesen Pfade und Befehle aus der Repo-Konfig (#46).

Fundstellen 13, 14, 16, 17 des Schnitts: ``pfade.manifeste``, ``pfade.agenten``,
``pfade.bau_log``, ``befehle.wirkungskreis`` und ``origin/<hauptzweig>`` als Vorgabe
für ``thermo_lauf.py plan --kopf``. Fehlt der Schlüssel, gilt der heutige Wert.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
for _pfad in (str(SKILL), str(SKRIPTE)):
    if _pfad not in sys.path:
        sys.path.insert(0, _pfad)

import abschluss_paket  # noqa: E402
import rueckblick  # noqa: E402
import test_uebersicht  # noqa: E402
import thermo_lauf  # noqa: E402

EIGEN = {
    "manifeste": "eigen/manifeste",
    "agenten": "eigen/agenten",
    "bau_log": "eigen/bau_log",
}


def _repo(tmp_path: Path, **werte: object) -> Path:
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(werte), encoding="utf-8")
    return repo


def _manifest(ordner: Path, spec: int) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    daten = {"tickets": {"11": {"title": "Erstes"}}}
    (ordner / f"spec-{spec}.json").write_text(json.dumps(daten), encoding="utf-8")


def _lade_skript(name: str, repo: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(f"{name}_pfade_l1", SKRIPTE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, modul)  # dataclasses suchen ihr Modul dort
    spec.loader.exec_module(modul)
    return modul


def test_marker_folgen_pfade_agenten(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    agenten = repo / "eigen" / "agenten"
    assert thermo_lauf.marker_pfad(repo, 7) == agenten / "thermo_7.md"
    assert rueckblick.marker_pfad(repo, 7) == agenten / "rueckblick_7.md"
    assert abschluss_paket.rueckblick_pfad(repo, 7) == agenten / "rueckblick_7.md"
    paket = abschluss_paket.Paket(spec=7, stage="", rundschau="", tests="", saetze=[], stand="live")
    assert abschluss_paket._md_datei(repo, paket) == agenten / "abschluss_7_live.md"


def test_manifeste_folgen_pfade_manifeste(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    _manifest(repo / "eigen" / "manifeste", 7)
    assert thermo_lauf.lade_manifest(repo, 7)[0] == repo / "eigen" / "manifeste" / "spec-7.json"
    assert rueckblick.lade_tickets(repo, 7) == ["11"]
    assert test_uebersicht.lade_tickets(repo, 7) == [("11", "Erstes")]


def test_bau_und_sessions_stand_lesen_manifeste_aus_konfig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    erwartet = repo.resolve() / "eigen" / "manifeste"
    assert _lade_skript("bau", repo, monkeypatch).MANIFEST_DIR == erwartet
    assert _lade_skript("sessions_stand", repo, monkeypatch).MANIFESTE == erwartet


def test_rueckblick_liest_bau_log_aus_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, pfade=EIGEN)
    ordner = repo / "eigen" / "bau_log"
    ordner.mkdir(parents=True)
    zeile = json.dumps({"typ": "zusammenfassung", "ticket": "11"})
    (ordner / "11.jsonl").write_text(zeile + "\n", encoding="utf-8")
    logs, orte = rueckblick.lokale_logs(repo, ["11"])
    assert len(logs["11"]) == 1
    assert orte[0][0].startswith(f"Repo {ordner.as_posix()}")


def test_wirkungskreis_folgt_befehle(tmp_path: Path) -> None:
    repo = _repo(tmp_path, befehle={"wirkungskreis": "werkzeug/wk.py"})
    assert "werkzeug/wk.py fehlt" in abschluss_paket.wirkungskreis(repo, 7)
    skript = repo / "werkzeug" / "wk.py"
    skript.parent.mkdir()
    skript.write_text("import sys\nprint('Kreis ' + sys.argv[1])\n", encoding="utf-8")
    assert abschluss_paket.wirkungskreis(repo, 7) == "Kreis 7"


def test_thermo_kopf_vorgabe_ist_origin_hauptzweig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, hauptzweig="trunk")
    gesehen: list[str] = []
    monkeypatch.setattr(thermo_lauf, "plan", lambda _r, _s, _b, kopf: gesehen.append(kopf) or 0)
    assert thermo_lauf.main(["plan", "7", "--repo", str(repo)]) == 0
    assert thermo_lauf.main(["plan", "7", "--repo", str(repo), "--kopf", "HEAD"]) == 0
    assert gesehen == ["origin/trunk", "HEAD"]


def test_ohne_konfig_gelten_die_alten_pfade(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert thermo_lauf.marker_pfad(repo, 7) == repo / "docs" / "agents" / "thermo_7.md"
    assert abschluss_paket.rueckblick_pfad(repo, 7) == repo / "docs" / "agents" / "rueckblick_7.md"
    _manifest(repo / "docs" / "agents" / "manifests", 7)
    assert rueckblick.lade_tickets(repo, 7) == ["11"]
    assert "scripts/wirkungskreis.py fehlt" in abschluss_paket.wirkungskreis(repo, 7)
