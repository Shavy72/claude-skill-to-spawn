"""Review-Fix 1: Signale in Code-Literalen, Pfad-Präfixe, diff_modus beim Hash-Nachtrag."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
if str(REVIEW) not in sys.path:
    sys.path.insert(0, str(REVIEW))

from _gemeinsam import SIGNALE, DateiDiff, berechne_route, signal_trifft  # noqa: E402
from review_beleg import schreiben  # noqa: E402


def treffer(pfad: str, hinzu: list[str], tmp_path: Path) -> set[str]:
    d = DateiDiff(pfad=pfad, zeilen=len(hinzu), hinzu=hinzu)
    return {s.id for s in SIGNALE if signal_trifft(s, tmp_path, d)}


# ---------------------------------------------------------------- Befund 1


def test_bearer_im_fstring_ist_sicherheit(tmp_path: Path) -> None:
    ids = treffer(
        "app.py", ['headers["Authorization"] = f"Bearer {token_wert}"'], tmp_path
    )
    assert "sicherheit" in ids


def test_create_table_im_execute_ist_datenbank(tmp_path: Path) -> None:
    assert "datenbank" in treffer("app.py", ['db.execute("CREATE TABLE x")'], tmp_path)


def test_boot_in_befehlsliste_mit_variable_ist_kosten(tmp_path: Path) -> None:
    assert "kosten" in treffer("app.py", ['cmd = ["boot", dev]'], tmp_path)


def test_secret_in_einzelzuweisung_ist_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("app.py", ['s = "my_secret_value"'], tmp_path)


def test_katalog_mit_typ_feuert_nicht(tmp_path: Path) -> None:
    ids = treffer(
        "kat.py", ['_KOSTEN: tuple[str, ...] = ("shutdown", "boot")'], tmp_path
    )
    assert "kosten" not in ids


def test_katalog_fortsetzungszeile_feuert_nicht(tmp_path: Path) -> None:
    assert "sicherheit" not in treffer("kat.py", ['    "auth", "login",'], tmp_path)


def test_kommentar_bleibt_entfernt(tmp_path: Path) -> None:
    assert "kosten" not in treffer("app.py", ['x = f(y)  # "boot"'], tmp_path)


# ---------------------------------------------------------------- Befund 2


def test_pfad_praefix_authentication_ist_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("services/authentication.py", ["x = 1"], tmp_path)


def test_pfad_praefix_passwordless_ist_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("web/passwordless.py", ["x = 1"], tmp_path)


def test_pfad_kontrolle_bleibt_ohne_rolle(tmp_path: Path) -> None:
    assert "sicherheit" not in treffer("web/kontrolle.py", ["x = 1"], tmp_path)


# ---------------------------------------------------------------- Befund 3


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _staged_und_anders_im_baum(repo: Path) -> dict:
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "rechnen.py")
    (repo / "rechnen.py").write_text("def eins():\n    return 99\n", encoding="utf-8")
    return berechne_route(repo.as_posix(), "commit", staged=True, files=["rechnen.py"])


def _schreiben(repo: Path, tmp_path: Path, route: dict, mit_modus: bool) -> dict:
    entwurf = {k: route[k] for k in ("sha", "fingerprint", "stufe", "dateien")}
    entwurf["moment"] = "commit"
    if mit_modus:
        entwurf["diff_modus"] = route["diff_modus"]
    pfad = tmp_path / "entwurf.json"
    pfad.write_text(json.dumps(entwurf), encoding="utf-8")
    assert schreiben(pfad.as_posix(), repo.as_posix()) == 0
    neu = berechne_route(repo.as_posix(), "commit", staged=True, files=["rechnen.py"])
    return json.loads(Path(neu["beleg"]).read_text(encoding="utf-8"))


def test_route_meldet_diff_modus(repo: Path) -> None:
    route = _staged_und_anders_im_baum(repo)
    assert route["diff_modus"] == {"staged": True, "base": None}


def test_nachtrag_hasht_staged_diff(repo: Path, tmp_path: Path) -> None:
    route = _staged_und_anders_im_baum(repo)
    beleg = _schreiben(repo, tmp_path, route, mit_modus=True)
    assert beleg["datei_hashes"] == route["datei_hashes"]


def test_commit_ohne_diff_modus_kein_nachtrag(repo: Path, tmp_path: Path) -> None:
    route = _staged_und_anders_im_baum(repo)
    beleg = _schreiben(repo, tmp_path, route, mit_modus=False)
    assert "datei_hashes" not in beleg
