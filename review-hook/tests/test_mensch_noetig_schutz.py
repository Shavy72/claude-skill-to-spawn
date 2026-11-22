# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Befund 1: Hook-Tests dürfen nie in die echte ~/.claude/.review/mensch_noetig.jsonl schreiben."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hooks import ROT, Umgebung, py_aendern, repo, u  # noqa: F401, E402


def test_umgebung_lenkt_mensch_noetig_in_tmp(u: Umgebung, repo: Path) -> None:
    # Fremdes HOME: fiele stop.py auf den Default zurück, landete die Zeile dort statt in der echten Datei.
    heim = u.tmp / "heim"
    heim.mkdir()
    u.env["HOME"] = u.env["USERPROFILE"] = str(heim)
    u.modus("scharf", max_fix_runden=2)
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    u.beleg(repo, u.route(repo, "--moment", "sitzung", "--files", "rechnen.py"), ROT)
    for _ in range(3):
        aus = u.stop(repo, aktiv=True)
    assert aus is not None and "Mensch nötig" in aus["systemMessage"]

    assert not (heim / ".claude" / ".review" / "mensch_noetig.jsonl").exists()
    assert Path(u.env["REVIEW_MENSCH_NOETIG"]).is_relative_to(u.tmp)
    assert len(Path(u.env["REVIEW_MENSCH_NOETIG"]).read_text(encoding="utf-8").splitlines()) == 1
