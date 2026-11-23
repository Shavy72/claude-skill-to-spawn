"""Runde 5 Fixrunde 1: inhalt_hash muss Position/Modus/Binär berücksichtigen, die Inhalt-Suche
darf an kaputten oder alten Belegen nicht abstürzen."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REVIEW))

from _gemeinsam import DateiDiff, beleg_nach_inhalt, berechne_route, inhalt_hash  # noqa: E402


def _diff(*zeilen: str) -> DateiDiff:
    return DateiDiff("a.py", 1, [], ["diff --git a/a.py b/a.py", "index 1..2 100644", *zeilen])


def test_gleiche_zeilen_andere_stelle_anderer_hash() -> None:
    oben = _diff("@@ -1,3 +1,3 @@", " x", "-a", "+b", " y")
    unten = _diff("@@ -40,3 +40,3 @@", " x", "-a", "+b", " y")
    assert inhalt_hash(oben) != inhalt_hash(unten)


def test_modus_aenderung_zaehlt() -> None:
    ohne = _diff("@@ -1 +1 @@", "-a", "+b")
    mit = DateiDiff("a.py", 1, [], ["diff --git a/a.py b/a.py", "old mode 100644", "new mode 100755",
                                    "index 1..2", "@@ -1 +1 @@", "-a", "+b"])
    assert inhalt_hash(ohne) != inhalt_hash(mit)


def test_untracked_gleich_new_file() -> None:
    untracked = DateiDiff("n.py", 2, [], ["untracked n.py", "+a", "+b"])
    neu = DateiDiff("n.py", 2, [], ["diff --git a/n.py b/n.py", "new file mode 100644", "index 0000000..abc",
                                    "--- /dev/null", "+++ b/n.py", "@@ -0,0 +1,2 @@", "+a", "+b"])
    assert inhalt_hash(untracked) == inhalt_hash(neu) != ""
    eins_u = DateiDiff("n.py", 1, [], ["untracked n.py", "+a"])
    eins_n = DateiDiff("n.py", 1, [], ["diff --git a/n.py b/n.py", "new file mode 100644", "index 0..1",
                                       "--- /dev/null", "+++ b/n.py", "@@ -0,0 +1 @@", "+a"])
    assert inhalt_hash(eins_u) == inhalt_hash(eins_n)


def test_kaputte_und_alte_belege_kein_absturz(repo: Path, umgebung) -> None:  # type: ignore[no-untyped-def]
    umgebung()
    ordner = repo / ".review"
    ordner.mkdir(exist_ok=True)
    (ordner / "beleg-aaaaaaa-sitzung.json").write_text(
        json.dumps({"dateien": None, "inhalt_hashes": {"rechnen.py": "x"}}), encoding="utf-8")
    (ordner / "beleg-bbbbbbb-sitzung.json").write_text(
        json.dumps({"dateien": [["nicht", "hashbar"]], "inhalt_hashes": {"rechnen.py": "x"}}), encoding="utf-8")
    (ordner / "beleg-ccccccc-sitzung.json").write_text(
        json.dumps({"dateien": ["rechnen.py"], "datei_hashes": {"rechnen.py": "x"}}), encoding="utf-8")
    (ordner / "beleg-ddddddd-sitzung.json").write_text("[1, 2]", encoding="utf-8")
    assert beleg_nach_inhalt(repo, "sitzung", ["rechnen.py"], {"rechnen.py": "x"}) is None
    (repo / "rechnen.py").write_text("def eins():\n    return 5\n", encoding="utf-8")
    r = berechne_route(repo.as_posix(), "sitzung", files=["rechnen.py"])
    assert r["beleg_gueltig"] is False
