"""L1 Etappe 1 · P0 Konfig-Kern (#46): eine Tür für Repo-Konfig und Maschinen-Liste.

Läuft gleich auf Windows und Linux: nur ``tmp_path``, kein ``fcntl``, keine festen Home-Pfade.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, vorgaben

SKILL = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path, name: str = "app", origin: str = "") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
    return repo


@pytest.fixture()
def maschine_datei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    datei = tmp_path / "konfig" / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(datei))
    return datei


# --- Repo-Konfig: neue Gruppen = heutiges Verhalten -------------------------------


def test_vorgaben_entsprechen_heutigem_verhalten() -> None:
    d = config.DEFAULTS
    assert d["pfade"] == {
        "manifeste": "docs/agents/manifests",
        "bau_log": "docs/agents/bau_log",
        "agenten": "docs/agents",
    }
    assert d["staging"]["schalter"] == "scripts/schalter.py"
    assert d["befehle"]["wirkungskreis"] == "scripts/wirkungskreis.py"
    assert d["vps"] == {"staging_log": "/home/bau/staging/deploys.jsonl"}
    skripte = {s["name"]: s for s in d["deploy"]["skripte"]}
    assert set(skripte) == {"safe_deploy_vps.sh", "staging_deploy.sh", "deploy_schlange", "server_tuer.sh"}
    assert skripte["safe_deploy_vps.sh"]["pruef_schalter"] == "--nur-gate"
    assert skripte["deploy_schlange"]["art"] == "pruef"
    assert skripte["server_tuer.sh"]["fern"] is True
    assert all(set(s) == {"name", "art", "pruef_schalter", "fern"} for s in skripte.values())


def test_eigene_werte_ueberschreiben_einzeln(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"pfade": {"manifeste": "plan/m"}, "staging": {"url": "https://x"}}), encoding="utf-8"
    )
    k = config.lade(repo)
    assert k["pfade"]["manifeste"] == "plan/m"
    assert k["pfade"]["bau_log"] == "docs/agents/bau_log"
    assert k["staging"]["schalter"] == "scripts/schalter.py"
    assert k["staging"]["url"] == "https://x"


def test_pfad_relativ_zur_repo_wurzel(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    k = config.lade(repo)
    assert config.pfad(k, "manifeste") == repo.resolve() / "docs" / "agents" / "manifests"
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"pfade": {"bau_log": "logs/bau", "agenten": "", "manifeste": 5}}), encoding="utf-8"
    )
    k = config.lade(repo)
    assert config.pfad(k, "bau_log") == repo.resolve() / "logs" / "bau"
    assert config.pfad(k, "agenten") == repo.resolve() / "docs" / "agents"  # leer = Vorgabe
    assert config.pfad(k, "manifeste") == repo.resolve() / "docs" / "agents" / "manifests"  # kein Text


def test_pfad_absolut_und_einfaches_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    ziel = tmp_path / "anderswo"
    assert config.pfad({"pfade": {"agenten": str(ziel)}}, "agenten") == ziel
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    assert config.pfad({}, "agenten") == repo.resolve() / "docs" / "agents"
    with pytest.raises(KeyError):
        config.pfad({}, "gibt_es_nicht")


def test_sicherstellen_schreibt_neue_gruppen_mit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    daten = json.loads(config.sicherstellen(repo).read_text(encoding="utf-8"))
    assert daten["pfade"] == config.DEFAULTS["pfade"]
    assert daten["deploy"] == config.DEFAULTS["deploy"]


def test_nur_config_importiert_vorgaben() -> None:
    """Die DuoPlus-Altwerte haben genau eine Tür: ``config.py``."""
    treffer = []
    for datei in [*SKILL.glob("to_spawn/**/*.py"), *SKILL.glob("skripte/**/*.py"), SKILL / "to_spawn.py"]:
        if datei.name in ("config.py", "vorgaben.py") and datei.parent.name == "to_spawn":
            continue
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            namen = []
            if isinstance(knoten, ast.Import):
                namen = [a.name for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom):
                namen = [knoten.module or ""] + [a.name for a in knoten.names]
            if any(n.split(".")[-1] == "vorgaben" for n in namen):
                treffer.append(str(datei.relative_to(SKILL)))
    assert treffer == []


# --- Maschinen-Liste -----------------------------------------------------------


def test_maschine_ohne_datei_gibt_vorgaben(maschine_datei: Path) -> None:
    m = config.maschine()
    assert [r["slug"] for r in m["repos"]] == ["Shavy72/duoplus-management", "Shavy72/claude-skill-to-spawn"]
    assert m["repos"][1]["ticket_repo"] == "Shavy72/duoplus-management"
    assert m["staging_sperren"] == ["/home/bau/staging/.deploy.lock"]
    assert m["hausmeister"]["ordner_praefixe"] == ["wt", "skill", "duoplus"]
    assert m["hausmeister"]["wurzeln"] == vorgaben.HAUSMEISTER_WURZELN
    m["repos"].clear()  # Aufrufer darf das Ergebnis ändern, die Vorgabe bleibt
    assert len(config.maschine()["repos"]) == 2


def test_maschine_standardort_unter_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TO_SPAWN_MASCHINE", raising=False)  # conftest lenkt sonst in tmp_path um
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert config.maschine_datei() == tmp_path / ".config" / "to-spawn" / "maschine.json"


@pytest.mark.parametrize("inhalt", ["{kaputt", "[1, 2]", ""])
def test_maschine_kaputt_gibt_vorgaben(maschine_datei: Path, inhalt: str) -> None:
    maschine_datei.parent.mkdir(parents=True)
    maschine_datei.write_text(inhalt, encoding="utf-8")
    assert config.maschine() == config.MASCHINE_DEFAULTS


def test_maschine_fehlende_schluessel_einzeln_vorgabe(maschine_datei: Path) -> None:
    maschine_datei.parent.mkdir(parents=True)
    maschine_datei.write_text(
        json.dumps(
            {
                "repos": [{"slug": "o/x", "pfad": "/r/x"}, "müll"],
                "staging_sperren": "kein-liste",
                "hausmeister": {"ordner_praefixe": ["wt"]},
            }
        ),
        encoding="utf-8",
    )
    m = config.maschine()
    assert m["repos"] == [{"slug": "o/x", "pfad": "/r/x", "ticket_repo": "", "worktree_basis": ""}]
    assert m["staging_sperren"] == vorgaben.STAGING_SPERREN
    assert m["hausmeister"] == {"ordner_praefixe": ["wt"], "wurzeln": vorgaben.HAUSMEISTER_WURZELN}


def test_maschine_eintragen_neu_und_wiederholbar(tmp_path: Path, maschine_datei: Path) -> None:
    repo = _repo(tmp_path, origin="https://github.com/o/app.git")
    eintrag = config.maschine_eintragen(repo)
    assert eintrag == {"slug": "o/app", "pfad": repo.resolve().as_posix(), "ticket_repo": "", "worktree_basis": ""}
    m = config.maschine()
    assert len(m["repos"]) == 3  # Vorgabe-Repos bleiben erhalten
    assert m["repos"][-1] == eintrag
    vorher = maschine_datei.read_bytes()
    os.utime(maschine_datei, (1, 1))  # alte Zeit: grobe Dateisystem-Uhr macht den Vergleich nicht zufällig grün
    mtime = maschine_datei.stat().st_mtime_ns
    assert config.maschine_eintragen(repo) == eintrag
    assert maschine_datei.read_bytes() == vorher
    assert maschine_datei.stat().st_mtime_ns == mtime  # nichts geschrieben
    assert not list(maschine_datei.parent.glob(".maschine-*"))  # keine Temp-Reste



def test_maschine_eintragen_schreibfehler_raeumt_auf(
    tmp_path: Path, maschine_datei: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``OSError`` beim Ersetzen geht an den Aufrufer, Temp-Datei weg, alte Datei bytegleich."""
    repo = _repo(tmp_path, origin="https://github.com/o/app.git")
    maschine_datei.parent.mkdir(parents=True)
    maschine_datei.write_text(json.dumps({"repos": []}), encoding="utf-8")
    vorher = maschine_datei.read_bytes()

    def wirft(*_args: object, **_kwargs: object) -> None:
        raise OSError("Platte voll")

    monkeypatch.setattr(config.os, "replace", wirft)
    with pytest.raises(OSError, match="Platte voll"):
        config.maschine_eintragen(repo)
    assert maschine_datei.read_bytes() == vorher
    assert not list(maschine_datei.parent.glob(".maschine-*"))  # keine Temp-Reste


def test_maschine_eintragen_ergaenzt_nur_leere_felder(tmp_path: Path, maschine_datei: Path) -> None:
    repo = _repo(tmp_path, origin="git@github.com:o/app.git")
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(json.dumps({"worktree_basis": "~/wt/app"}), encoding="utf-8")
    maschine_datei.parent.mkdir(parents=True)
    maschine_datei.write_text(
        json.dumps({"repos": [{"slug": "", "pfad": str(repo), "ticket_repo": "o/tickets"}], "eigenes": 1}),
        encoding="utf-8",
    )
    eintrag = config.maschine_eintragen(repo)
    assert eintrag == {"slug": "o/app", "pfad": str(repo), "ticket_repo": "o/tickets", "worktree_basis": "~/wt/app"}
    roh = json.loads(maschine_datei.read_text(encoding="utf-8"))
    assert roh["eigenes"] == 1  # fremde Schlüssel bleiben
    assert len(roh["repos"]) == 1


def test_maschine_eintragen_ueberschreibt_kaputte_datei_nie(tmp_path: Path, maschine_datei: Path) -> None:
    repo = _repo(tmp_path, origin="https://github.com/o/app")
    maschine_datei.parent.mkdir(parents=True)
    maschine_datei.write_text("{halb", encoding="utf-8")
    assert config.maschine_eintragen(repo)["slug"] == "o/app"
    assert maschine_datei.read_text(encoding="utf-8") == "{halb"


def test_conftest_lenkt_maschine_liste_in_tmp_path(tmp_path: Path) -> None:
    """Ohne eigenes ``setenv`` liest und schreibt kein Test die echte Maschinen-Liste."""
    assert config.maschine_datei().is_relative_to(tmp_path)
    echt = Path.home() / ".config" / "to-spawn" / "maschine.json"  # nur ``stat``, nie schreiben
    vorher = echt.stat().st_mtime_ns if echt.exists() else None
    config.maschine_eintragen(_repo(tmp_path, origin="https://github.com/o/app.git"))
    assert config.maschine_datei().is_file()
    assert (echt.stat().st_mtime_ns if echt.exists() else None) == vorher
