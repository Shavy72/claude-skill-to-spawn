"""#1504: capo urteilt über die Live-Deckung aus ``origin/master``, nicht aus dem Arbeitsbaum.

Echter Git-Weg (Repo mit Bare-Origin); nur das Deckungs-Skript ist eine Rechen-Attrappe:
gedeckt genau dann, wenn im übergebenen ``--manifeste``-Ordner ``spec-900.json`` liegt.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn import capo

_SKRIPT = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "ordner = Path(sys.argv[sys.argv.index('--manifeste') + 1])\n"
    "ok = (ordner / 'spec-900.json').is_file()\n"
    "print(json.dumps({'gedeckt': ok, 'offen': []}))\n"
    "sys.exit(0 if ok else 1)\n"
)
_MANIFEST = "docs/agents/manifests/spec-900.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _welt(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Repo mit Origin; Deckungs-Skript auf origin/master gepusht, eigener Zettel liegt."""
    fern = tmp_path / "fern.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(fern)],
        check=True,
        capture_output=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(fern))
    (repo / "scripts").mkdir()
    (repo / "scripts" / "schlange_deckung.py").write_text(_SKRIPT, encoding="utf-8")
    _git(repo, "add", "scripts")
    _git(repo, "commit", "-q", "-m", "chore: Deckung (#1)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    ordner = tmp_path / "zettel"
    ordner.mkdir()
    (ordner / "901").write_text("ok\n", encoding="utf-8")
    return repo, {"freigabe_ordner": str(ordner)}


def _manifest_schreiben(repo: Path) -> None:
    pfad = repo / _MANIFEST
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text('{"tickets": [901]}\n', encoding="utf-8")


def _gedeckt(repo: Path, vps: dict[str, str]) -> bool:
    kopf = _git(repo, "rev-parse", "HEAD")
    return capo.live_gedeckt(repo, vps, 901, kopf, kopf)


def test_1504_ungepushtes_manifest_im_arbeitsbaum_deckt_nicht(tmp_path: Path) -> None:
    """Manifest nur im Arbeitsbaum (die Schlange sieht es nicht) → nicht gedeckt."""
    repo, vps = _welt(tmp_path)
    _manifest_schreiben(repo)
    assert _gedeckt(repo, vps) is False


def test_1504_manifest_auf_origin_master_deckt(tmp_path: Path) -> None:
    """Gegenprobe: Manifest auf origin/master → gedeckt wie bisher."""
    repo, vps = _welt(tmp_path)
    _manifest_schreiben(repo)
    _git(repo, "add", _MANIFEST)
    _git(repo, "commit", "-q", "-m", "docs: Manifest (#900)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    assert _gedeckt(repo, vps) is True


def test_1504_skript_nur_im_arbeitsbaum_aendert_urteil_nicht(tmp_path: Path) -> None:
    """Auch das Skript gilt aus origin/master: eine lokale „immer gedeckt“-Fassung zählt nicht."""
    repo, vps = _welt(tmp_path)
    (repo / "scripts" / "schlange_deckung.py").write_text(
        "import sys\nprint('{\"gedeckt\": true}')\nsys.exit(0)\n", encoding="utf-8"
    )
    assert _gedeckt(repo, vps) is False


def test_1504_origin_master_unlesbar_fail_closed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Ohne lesbares origin/master: nicht gedeckt mit Warnzeile — trotz Manifest und eigenem Zettel."""
    repo, vps = _welt(tmp_path)
    _manifest_schreiben(repo)
    _git(repo, "add", _MANIFEST)
    _git(repo, "commit", "-q", "-m", "docs: Manifest (#900)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/master")
    with caplog.at_level("WARNING"):
        assert _gedeckt(repo, vps) is False
    assert "nicht prüfbar" in caplog.text


def _gepusht_mit_manifest(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo, vps = _welt(tmp_path)
    _manifest_schreiben(repo)
    _git(repo, "add", _MANIFEST)
    _git(repo, "commit", "-q", "-m", "docs: Manifest (#900)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    return repo, vps


@pytest.mark.parametrize("code", [124, 127])
def test_1504_baum_abfrage_gescheitert_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Git-Abfrage nach dem Skript scheitert kurz (Zeitablauf/OSError): nicht „fehlt“, also kein Zettel-Rückfall."""
    repo, vps = _gepusht_mit_manifest(tmp_path)
    echt = capo._git

    def wackelig(r: Path, *args: str) -> tuple[int, str]:
        if args[:1] in (("cat-file",), ("ls-tree",)):
            return code, ""
        return echt(r, *args)

    monkeypatch.setattr(capo, "_git", wackelig)
    assert _gedeckt(repo, vps) is False  # Zettel 901 liegt — „gedeckt“ wäre fail-open


def test_1504_skript_fehlt_auf_origin_master_zaehlt_zettel(tmp_path: Path) -> None:
    """Gegenprobe: Skript gibt es auf origin/master wirklich nicht → wie früher nur der eigene Zettel."""
    repo, vps = _welt(tmp_path)
    _git(repo, "rm", "-q", "scripts/schlange_deckung.py")
    _git(repo, "commit", "-q", "-m", "chore: ohne Deckung (#2)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    assert _gedeckt(repo, vps) is True


def test_1504_entpackt_nur_gebrauchte_dateien(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``git archive`` holt nur die Deckungs-Dateien und Manifeste, nicht ganz ``scripts/``."""
    repo, vps = _gepusht_mit_manifest(tmp_path)
    (repo / "scripts" / "fremd.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "scripts")
    _git(repo, "commit", "-q", "-m", "chore: fremd (#3)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    echt = subprocess.run
    archiv_pfade: list[list[str]] = []

    def lauf(cmd: list[str], *a: object, **k: object) -> subprocess.CompletedProcess:
        if cmd[:2] == ["git", "archive"]:
            archiv_pfade.append(cmd[cmd.index("--") + 1 :])
        return echt(cmd, *a, **k)

    monkeypatch.setattr(capo.subprocess, "run", lauf)
    assert _gedeckt(repo, vps) is True
    assert archiv_pfade == [["scripts/schlange_deckung.py", "docs/agents/manifests"]]


def test_1504_manifest_ordner_aus_repo_konfig(tmp_path: Path) -> None:
    """``pfade.manifeste`` aus ``.to-spawn/config.json`` steuert den entpackten Manifest-Ordner (#860)."""
    repo, vps = _welt(tmp_path)
    konfig = repo / ".to-spawn" / "config.json"
    konfig.parent.mkdir(parents=True, exist_ok=True)
    konfig.write_text('{"pfade": {"manifeste": "eigene/manifests"}}\n', encoding="utf-8")
    manifest = repo / "eigene" / "manifests" / "spec-900.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"tickets": [901]}\n', encoding="utf-8")
    _git(repo, "add", ".to-spawn/config.json", "eigene/manifests/spec-900.json")
    _git(repo, "commit", "-q", "-m", "docs: eigenes Manifest (#900)")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    assert _gedeckt(repo, vps) is True
