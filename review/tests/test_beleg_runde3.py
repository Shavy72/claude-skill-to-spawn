"""schreiben(): datei_hashes nur nachtragen, wenn Baum unverändert seit Entwurf (VERTRAG.md §5)."""

from __future__ import annotations

import json
from pathlib import Path

from review_hilfen import lauf, route


def _entwurf_schreiben(repo: Path, r: dict) -> Path:
    entwurf = {k: r[k] for k in ("moment", "stufe", "sha", "fingerprint", "dateien")}
    entwurf["befunde"] = []
    entwurf["richter"] = None
    datei = repo.parent / "entwurf.json"
    datei.write_text(json.dumps(entwurf), encoding="utf-8")
    return datei


def test_baum_geaendert_seit_entwurf_keine_datei_hashes(repo: Path) -> None:
    """sha (HEAD) bleibt gleich, aber Arbeitsbaum ändert sich nach dem Entwurf -> keine Hashes eintragen."""
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")
    r = route(repo, "--moment", "sitzung")
    entwurf = _entwurf_schreiben(repo, r)

    # Baum ändert sich NACH dem Entwurf, VOR schreiben()
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def drei():\n    return 3\n")

    ergebnis = lauf(
        "review_beleg.py", "schreiben", "--datei", str(entwurf), "--repo", str(repo)
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    beleg = json.loads(
        Path(ergebnis.stdout.splitlines()[0]).read_text(encoding="utf-8")
    )
    assert not beleg.get("datei_hashes")


def test_baum_unveraendert_datei_hashes_vorhanden(repo: Path) -> None:
    with (repo / "rechnen.py").open("a", encoding="utf-8") as f:
        f.write("def zwei():\n    return 2\n")
    r = route(repo, "--moment", "sitzung")
    entwurf = _entwurf_schreiben(repo, r)

    ergebnis = lauf(
        "review_beleg.py", "schreiben", "--datei", str(entwurf), "--repo", str(repo)
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    beleg = json.loads(
        Path(ergebnis.stdout.splitlines()[0]).read_text(encoding="utf-8")
    )
    assert beleg.get("datei_hashes")
