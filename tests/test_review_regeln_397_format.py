"""Issue 397, Punkte 2–5: ``regeln.md`` robust lesen (Route) und schreiben (Installer).

- Punkt 2: alter ausgelieferter Vorlagentext aus ``_alt`` kommt nicht als Regel an.
- Punkt 3: eine ``# ``-Überschrift beendet den Abschnitt auch beim Schreiben.
- Punkt 4: ``Falls repo`` (Groß/Klein egal) und Backticks um den Repo-Namen gelten.
- Punkt 5: Überschrift ohne Groß/Klein und mit doppelten Leerzeichen ist derselbe Abschnitt; CRLF bleibt.

Alle Regel-Texte und Repo-Namen hier sind Kunsttext.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from to_spawn import review_hook

QUELLE = review_hook.WERKZEUG_QUELLE
GNU = "Kunstregel Gnu: Abläufe nur über das Kunst-Tor."
OKAPI = "Kunstregel Okapi: Abläufe nur über den Kunst-Weg."
ALT_TEXT = "Alter ausgelieferter Kunst-Vorlagentext, längst entfernt."


def _einspielen(ziel: Path, quelle: Path = QUELLE) -> list[str]:
    return review_hook.installiere_werkzeug(ziel, quelle, zeit="t")


def _vorlage_mit(ziel: Path, name: str, zeile: str) -> None:
    datei = ziel / "auftraege" / name
    datei.parent.mkdir(parents=True, exist_ok=True)
    text = (QUELLE / "auftraege" / name).read_text(encoding="utf-8")
    datei.write_text(text + f"\n{zeile}\n", encoding="utf-8")


def _route_regeln(ziel: Path, repo: Path) -> dict[str, list[str]]:
    """``regeln_je_auftrag`` aus dem eingespielten Werkzeug (``HIER`` = ``ziel``)."""
    name = f"_gemeinsam_397_{ziel.parent.name}"
    spec = importlib.util.spec_from_file_location(name, ziel / "_gemeinsam.py")
    assert spec and spec.loader
    modul: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    repo.mkdir(exist_ok=True)
    return modul.regeln_je_auftrag(repo)


def _mit_regeln(tmp_path: Path, regeln: str) -> Path:
    ziel = tmp_path / "review"
    _einspielen(ziel)
    (ziel / "regeln.md").write_text(regeln, encoding="utf-8")
    return ziel


def test_punkt3_neue_regel_landet_vor_einer_rauten_ueberschrift(tmp_path: Path) -> None:
    ziel = _mit_regeln(tmp_path, f"# Kopf\n\n## python\n\n{OKAPI}\n\n# Anhang\n\nFreitext.\n")
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    assert GNU in _route_regeln(ziel, tmp_path / "kunst-app").get("python", [])


def test_punkt4_falls_repo_klein_geschrieben_gilt_nur_dort(tmp_path: Path) -> None:
    ziel = _mit_regeln(tmp_path, f"## python\n\nFalls repo andere-app: {OKAPI}\n")

    assert _route_regeln(ziel, tmp_path / "kunst-app").get("python") == []
    assert _route_regeln(ziel, tmp_path / "andere-app").get("python") == [f"Falls repo andere-app: {OKAPI}"]


def test_punkt4_backticks_um_den_repo_namen(tmp_path: Path) -> None:
    zeile = f"Falls Repo `kunst-app`: {OKAPI}"
    ziel = _mit_regeln(tmp_path, f"## python\n\n{zeile}\n")

    assert _route_regeln(ziel, tmp_path / "kunst-app").get("python") == [zeile]
    assert _route_regeln(ziel, tmp_path / "andere-app").get("python") == []


def test_punkt4_installer_kennt_backtick_praefix_als_dublette(tmp_path: Path) -> None:
    von_hand = f"## python\n\nFalls repo `kunst-app`: {GNU}\n"
    ziel = _mit_regeln(tmp_path, von_hand)
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    assert (ziel / "regeln.md").read_text(encoding="utf-8") == von_hand


def _python_koepfe(text: str) -> int:
    return sum(1 for z in text.splitlines() if z.startswith("## ") and z[3:].strip().lower() == "python")


def test_punkt5_ueberschrift_gross_geschrieben_ist_derselbe_abschnitt(tmp_path: Path) -> None:
    ziel = _mit_regeln(tmp_path, f"## Python\n\n{OKAPI}\n")
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    assert _python_koepfe((ziel / "regeln.md").read_text(encoding="utf-8")) == 1
    assert _route_regeln(ziel, tmp_path / "kunst-app").get("python") == [OKAPI, GNU]


def test_punkt5_ueberschrift_mit_doppelten_leerzeichen_ist_derselbe_abschnitt(tmp_path: Path) -> None:
    ziel = _mit_regeln(tmp_path, f"##  python \n\n{OKAPI}\n")
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    assert _python_koepfe((ziel / "regeln.md").read_text(encoding="utf-8")) == 1


def test_punkt5_crlf_datei_bleibt_crlf(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    _einspielen(ziel)
    (ziel / "regeln.md").write_bytes(f"# Kopf\r\n\r\n## python\r\n\r\n{OKAPI}\r\n".encode())
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    daten = (ziel / "regeln.md").read_bytes()
    assert GNU.encode() in daten
    assert b"\n" not in daten.replace(b"\r\n", b""), daten


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(ordner), *args],
        check=True,
        capture_output=True,
    )


def test_punkt2_alter_ausgelieferter_vorlagentext_ist_keine_regel(tmp_path: Path) -> None:
    """Quelle mit Verlauf: eine frühere Fassung von ``python.md`` hatte ``ALT_TEXT``, die heutige nicht."""
    quelle = tmp_path / "quelle"
    shutil.copytree(QUELLE, quelle, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    vorlage = quelle / "auftraege" / "python.md"
    heute = vorlage.read_text(encoding="utf-8")
    _git(quelle, "init", "-q")
    vorlage.write_text(heute + f"\n{ALT_TEXT}\n", encoding="utf-8")
    _git(quelle, "add", "-A")
    _git(quelle, "commit", "-q", "-m", "alt")
    vorlage.write_text(heute, encoding="utf-8")
    _git(quelle, "commit", "-q", "-am", "neu")
    ziel = tmp_path / "review"
    _einspielen(ziel, quelle)
    alt = tmp_path / "_alt" / "review-frueher" / "auftraege"
    alt.mkdir(parents=True)
    (alt / "python.md").write_text(heute + f"\n{ALT_TEXT}\n\n{GNU}\n", encoding="utf-8")

    _einspielen(ziel, quelle)

    text = (ziel / "regeln.md").read_text(encoding="utf-8")
    assert GNU in text and ALT_TEXT not in text, text
