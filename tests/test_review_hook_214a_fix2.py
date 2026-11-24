"""#214 Teil A, Fix-Runde 2: Sammel-Ausnahme für ``review-hook/`` und Installer-Restfälle.

- H1: ``review-hook/tests`` suchen ``review/config.json`` relativ zum Installations-Ort und
  laufen nur dort. Die Wurzel-``conftest.py`` nimmt genau diesen einen Ordner aus der Sammlung;
  eine Sammlung ohne Pfad aus der Repo-Wurzel hat dann 0 Fehler.
- M-a: die Temp-Datei hat keinen festen Namen mehr; ein Link an der alten Stelle wird nie
  durchgeschrieben.
- M-c: ein Ordner an der Stelle einer Ziel-Datei wird gemeldet und übersprungen.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import review_hook  # noqa: E402

QUELLE = SKILL / "review-hook"


def _wurzel_conftest() -> dict[str, object]:
    namen: dict[str, object] = {}
    exec((SKILL / "conftest.py").read_text(encoding="utf-8"), namen)  # noqa: S102 — eigene Datei
    return namen


def test_ausnahme_trifft_genau_review_hook() -> None:
    namen = _wurzel_conftest()
    assert namen.get("collect_ignore") == ["review-hook", "probe"]  # probe: #426
    assert "collect_ignore_glob" not in namen  # kein Muster, das andere Ordner miterfasst


def test_sammlung_ohne_pfad_ohne_fehler(tmp_path: Path) -> None:
    lauf = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "TMPDIR": str(tmp_path)},
    )
    schluss = lauf.stdout.strip().splitlines()[-1] if lauf.stdout.strip() else lauf.stderr
    assert lauf.returncode == 0, schluss
    assert not re.search(r"\d+ errors?", schluss), schluss
    assert "review-hook/" not in lauf.stdout  # nichts aus dem Hook-Ordner gesammelt
    assert re.search(r"\d+ tests? collected", schluss), schluss


def test_link_an_alter_temp_stelle_wird_nicht_durchgeschrieben(tmp_path: Path) -> None:
    ziel = tmp_path / "heim" / "review"
    ziel.mkdir(parents=True)
    aussen = tmp_path / "aussen.txt"
    aussen.write_text("bleibt", encoding="utf-8")
    (ziel / ".stop.py.neu").symlink_to(aussen)
    review_hook.installiere(ziel, zeit="t")
    assert aussen.read_text(encoding="utf-8") == "bleibt"
    stop = ziel / "stop.py"
    assert stop.is_file() and not stop.is_symlink()
    assert stop.read_bytes() == (QUELLE / "stop.py").read_bytes()


def test_schreibfehler_laesst_keinen_temp_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    datei = tmp_path / "x.py"

    def kaputt(_quelle: object, _ziel: object) -> None:
        raise OSError("ersetzen gescheitert")

    monkeypatch.setattr(review_hook.os, "replace", kaputt)
    with pytest.raises(OSError):
        review_hook._schreibe_atomar(datei, b"neu")
    assert list(tmp_path.iterdir()) == []


def test_ordner_statt_ziel_datei_wird_gemeldet_und_uebersprungen(tmp_path: Path) -> None:
    ziel = tmp_path / "heim" / "review"
    (ziel / "stop.py").mkdir(parents=True)
    bericht = review_hook.installiere(ziel, zeit="t")
    assert "abweichend, Ordner nicht angefasst: stop.py" in bericht
    assert (ziel / "stop.py").is_dir()
    assert (ziel / "commit.py").read_bytes() == (QUELLE / "commit.py").read_bytes()
    assert "stop.py" not in (ziel / review_hook.STAND_DATEI).read_text(encoding="utf-8")
