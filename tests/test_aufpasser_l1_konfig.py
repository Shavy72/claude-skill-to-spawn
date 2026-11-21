"""L1 Etappe 1 P2a: Deploy-Muster und Einordnung live/pruef kommen aus ``deploy.skripte``.

(a) Ohne Schlüssel gilt exakt das alte Muster und die alte Einordnung.
(b) Mit eigener ``deploy.skripte``-Liste greift nur die neue Liste, die alten Namen nicht mehr.
Repo-Kennzeichen ist nur noch ``.to-spawn/config.json`` (Fundstelle 9).
"""

from __future__ import annotations

import json
from pathlib import Path

from to_spawn import aufpasser
from to_spawn.aufpasser import Aufpasser, Einstellungen, Fenster, deploy_art

ALT_MUSTER = r"safe_deploy_vps\.sh|deploy_schlange|staging_deploy\.sh|server_tuer\.sh"

#: (argv, alte Einordnung) — Stand vor L1, aufpasser.deploy_art.
ALT_FAELLE: list[tuple[list[str], str | None]] = [
    (["bash", "scripts/safe_deploy_vps.sh", "--skip-ci"], "live"),
    (["bash", "scripts/safe_deploy_vps.sh", "--nur-gate"], "pruef"),
    (["bash", "scripts/safe_deploy_vps.sh", "--to", "abc1234"], "live"),
    (["bash", "scripts/staging/staging_deploy.sh", "HEAD"], "live"),
    (["python3", "scripts/deploy_schlange.py"], "pruef"),
    (["ssh", "clawy-vps", "bash /opt/x/server_tuer.sh live"], "live"),
    (["ssh", "clawy-vps", "uptime"], None),
    (["bash", "server_tuer.sh"], None),
    (["python3", "-m", "pytest", "tests"], "pruef"),
    (["claude", "--print", "-c", "nenne safe_deploy_vps.sh nur als Text"], None),
]

EIGENE_SKRIPTE = [
    {"name": "rollout.sh", "art": "live", "pruef_schalter": "--probe", "fern": False},
    {"name": "tor.sh", "art": "live", "pruef_schalter": "", "fern": True},
    {"name": "ohne-art.sh"},  # ungültig: art fehlt ⇒ fällt weg
    "kein-objekt",  # ungültig ⇒ fällt weg
]


def _alternativen(muster: str) -> set[str]:
    return set(muster.split("|"))


def _repo(tmp_path: Path, konfig: dict | None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    if konfig is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(
            json.dumps(konfig), encoding="utf-8"
        )
    return repo


def _aufpasser(tmp_path: Path, **kw: object) -> Aufpasser:
    return Aufpasser(Einstellungen(zustand=tmp_path / "zustand", **kw))  # type: ignore[arg-type]


def test_ohne_schluessel_altes_muster_und_einordnung(tmp_path: Path) -> None:
    assert _alternativen(aufpasser.DEPLOY_MUSTER) == _alternativen(ALT_MUSTER)
    for argv, alt in ALT_FAELLE:
        assert deploy_art(argv) == alt, argv
    a = _aufpasser(tmp_path)
    muster = a.deploy_muster_fuer({_repo(tmp_path, {"effort": "high"})})
    assert _alternativen(muster) == _alternativen(ALT_MUSTER)
    for argv, alt in ALT_FAELLE:
        assert deploy_art(argv, a.skripte) == alt, argv


def test_eigene_liste_ersetzt_alte_namen(tmp_path: Path) -> None:
    a = _aufpasser(tmp_path)
    repo = _repo(tmp_path, {"deploy": {"skripte": EIGENE_SKRIPTE}})
    muster = a.deploy_muster_fuer({repo})
    assert _alternativen(muster) == {r"rollout\.sh", r"tor\.sh"}
    assert deploy_art(["bash", "rollout.sh"], a.skripte) == "live"
    assert deploy_art(["bash", "rollout.sh", "--probe"], a.skripte) == "pruef"
    assert deploy_art(["ssh", "h", "bash tor.sh"], a.skripte) == "live"
    assert deploy_art(["bash", "tor.sh"], a.skripte) is None
    assert deploy_art(["bash", "scripts/safe_deploy_vps.sh"], a.skripte) is None
    assert deploy_art(["ssh", "h", "bash server_tuer.sh"], a.skripte) is None
    assert deploy_art(["python3", "scripts/deploy_schlange.py"], a.skripte) is None


def test_leere_liste_trifft_nichts(tmp_path: Path) -> None:
    a = _aufpasser(tmp_path)
    muster = a.deploy_muster_fuer({_repo(tmp_path, {"deploy": {"skripte": []}})})
    assert muster and "safe_deploy" not in muster
    assert aufpasser.re.search(muster, "irgendein prozess") is None


def test_explizites_muster_bleibt_wie_bisher(tmp_path: Path) -> None:
    a = _aufpasser(tmp_path, deploy_muster="probe-niemals")
    repo = _repo(tmp_path, {"deploy": {"skripte": EIGENE_SKRIPTE}})
    assert a.deploy_muster_fuer({repo}) == "probe-niemals"


def test_repo_kennzeichen_nur_konfig(tmp_path: Path) -> None:
    nur_bau = tmp_path / "nur_bau"
    (nur_bau / "scripts").mkdir(parents=True)
    (nur_bau / "scripts" / "bau.py").write_text("", encoding="utf-8")
    mit_konfig = _repo(tmp_path, {})
    a = _aufpasser(tmp_path)
    assert a.repo_ordner([Fenster("s", "1", "0", "bau 1", str(nur_bau), 0)]) is None
    f = Fenster("s", "1", "0", "bau 1", str(mit_konfig), 0)
    assert a.repo_ordner([f]) == mit_konfig
