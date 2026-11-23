"""Review 393, Fix-Runde 2: kein Install verliert Projektregeln still; lokale Config verdeckt den Standard nicht;
nur versionierte Dateien werden eingespielt.

- Zeilen, die nur in einer lokal geänderten Auftrags-Vorlage stehen (bisher fest eingebaute Projektregeln),
  übernimmt der Installer in die private ``regeln.md`` im Werkzeug-Ordner (VERTRAG.md §2a); die Vorlagen nennen sie.
- Fehlt ``regeln.md``, meldet die Route es laut (JSON-Feld + Kurzzeile + Warnung).
- Eine lokale ``config.json``, die einem ausgelieferten Stand gleicht, kommt nach ``_alt/``; Schlüssel gleich dem
  Standard fallen weg; echte Abweichungen bleiben.
- Eingespielt werden nur versionierte Dateien; ohne git gilt die ``.gitignore`` der Quelle.

Alle Regel-Texte hier sind Kunsttext.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import review_hook

SKILL = Path(__file__).resolve().parents[1]
QUELLE = review_hook.WERKZEUG_QUELLE
ZEBRA = "Falls Repo kunst-app: Kunstregel Zebra nur über `kunst_modul.tor()`."
OKAPI = "Kunstregel Okapi: Abläufe nur über den Kunst-Weg."


def _einspielen(ziel: Path, quelle: Path = QUELLE) -> list[str]:
    """Werkzeug-Install wie ``main``; vor dem Fix gab es nur ``installiere``."""
    return getattr(review_hook, "installiere_werkzeug", review_hook.installiere)(ziel, quelle, zeit="t")


def _modul(pfad: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, pfad)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # dataclasses im Modul brauchen den Eintrag
    spec.loader.exec_module(modul)
    return modul


def _alt_stand(ziel: Path) -> None:
    """Heutiger installierter Stand: Vorlagen mit fest eingebauten Projektregeln, kein ``.installiert.json``."""
    (ziel / "auftraege").mkdir(parents=True)
    for name in ("python.md", "spec.md", "basis.md"):
        shutil.copy2(QUELLE / "auftraege" / name, ziel / "auftraege" / name)
    python = (ziel / "auftraege" / "python.md").read_text(encoding="utf-8").splitlines()
    zeile = next(i for i, z in enumerate(python) if z.startswith("Projekt-eigene Regeln:"))
    python[zeile] = ZEBRA
    (ziel / "auftraege" / "python.md").write_text("\n".join(python) + "\n", encoding="utf-8")
    with (ziel / "auftraege" / "spec.md").open("a", encoding="utf-8") as datei:
        datei.write(f"\n{OKAPI}\n")


def test_alt_regeln_bleiben_erreichbar_und_landen_im_auftrag(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    _alt_stand(ziel)

    bericht = _einspielen(ziel)

    regeln = ziel / "regeln.md"
    assert regeln.is_file(), bericht
    text = regeln.read_text(encoding="utf-8")
    python_teil = text.split("## python", 1)[1].split("\n## ", 1)[0]
    spec_teil = text.split("## spec", 1)[1].split("\n## ", 1)[0]
    assert ZEBRA in python_teil and OKAPI in spec_teil
    assert any("regeln.md" in z for z in bericht)
    # Vorlagen sind wieder ausgeliefert und schicken den Prüfer in seinen Abschnitt der regeln.md.
    for name in ("python", "spec"):
        vorlage = (ziel / "auftraege" / f"{name}.md").read_text(encoding="utf-8")
        assert vorlage == (QUELLE / "auftraege" / f"{name}.md").read_text(encoding="utf-8")
        assert "~/.claude/review/regeln.md" in vorlage and f"## {name}" in vorlage
    # Das eingespielte Werkzeug findet die Datei selbst.
    werkzeug = _modul(ziel / "_gemeinsam.py", "_gemeinsam_regeln_test")
    regeln_info = getattr(werkzeug, "projektregeln", lambda: {})()
    assert regeln_info == {"datei": regeln.as_posix(), "vorhanden": True}
    # Zweiter Install: nichts doppelt, nichts verloren.
    _einspielen(ziel)
    assert regeln.read_text(encoding="utf-8") == text


def test_ohne_regeln_meldet_route_laut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ziel = tmp_path / "review"
    _einspielen(ziel)
    assert not (ziel / "regeln.md").exists()
    werkzeug = _modul(ziel / "_gemeinsam.py", "_gemeinsam_ohne_regeln_test")
    with caplog.at_level(logging.WARNING):
        regeln_info = getattr(werkzeug, "projektregeln", lambda: {})()
    assert regeln_info.get("vorhanden") is False
    assert "Projektregeln fehlen" in caplog.text

    monkeypatch.syspath_prepend(str(QUELLE))
    route_modul = _modul(QUELLE / "review_route.py", "review_route_regeln_test")
    route = {"stufe": "klein", "pruefer": [], "builtin": [], "diff_zeilen": 3, "dateien": ["a.py"]}
    route["projektregeln"] = {"datei": "x/regeln.md", "vorhanden": False}
    assert "Projektregeln fehlen" in route_modul.kurzzeile(route)
    route["projektregeln"] = {"datei": "x/regeln.md", "vorhanden": True}
    assert "Projektregeln" not in route_modul.kurzzeile(route)


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(ordner), *args],
        check=True,
        capture_output=True,
    )


def _quelle_mit_verlauf(tmp_path: Path) -> tuple[Path, dict, dict]:
    """Git-Quelle: erst ``config.json`` v1 ausgeliefert, dann umbenannt in ``config.standard.json`` v2."""
    quelle = tmp_path / "quelle"
    quelle.mkdir()
    v1 = {"version": 1, "modus": "hinweis", "stufen": {"klein": 50}}
    v2 = {"version": 1, "modus": "hinweis", "stufen": {"klein": 60}, "neu": True}
    _git(quelle, "init", "-q")
    (quelle / "config.json").write_text(json.dumps(v1), encoding="utf-8")
    _git(quelle, "add", "config.json")
    _git(quelle, "commit", "-q", "-m", "v1")
    _git(quelle, "mv", "config.json", "config.standard.json")
    (quelle / "config.standard.json").write_text(json.dumps(v2, indent=2), encoding="utf-8")
    _git(quelle, "add", "-A")
    _git(quelle, "commit", "-q", "-m", "v2")
    return quelle, v1, v2


def test_config_gleich_altem_standard_kommt_weg(tmp_path: Path) -> None:
    quelle, v1, v2 = _quelle_mit_verlauf(tmp_path)
    ziel = tmp_path / "review"
    ziel.mkdir()
    (ziel / "config.json").write_text(json.dumps(v1, indent=4), encoding="utf-8")

    bericht = _einspielen(ziel, quelle)

    assert not (ziel / "config.json").exists(), bericht
    assert json.loads((tmp_path / "_alt" / "review-t" / "config.json").read_text(encoding="utf-8")) == v1
    assert any("config.json" in z for z in bericht)


def test_config_mit_abweichung_wird_entschlackt(tmp_path: Path) -> None:
    quelle, _v1, v2 = _quelle_mit_verlauf(tmp_path)
    ziel = tmp_path / "review"
    ziel.mkdir()
    lokal = {**v2, "modus": "scharf"}
    (ziel / "config.json").write_text(json.dumps(lokal), encoding="utf-8")

    _einspielen(ziel, quelle)

    assert json.loads((ziel / "config.json").read_text(encoding="utf-8")) == {"modus": "scharf"}
    assert json.loads((tmp_path / "_alt" / "review-t" / "config.json").read_text(encoding="utf-8")) == lokal


def test_config_nur_abweichung_bleibt_unberuehrt(tmp_path: Path) -> None:
    quelle, _v1, _v2 = _quelle_mit_verlauf(tmp_path)
    ziel = tmp_path / "review"
    ziel.mkdir()
    lokal = '{"modus": "scharf"}\n'
    (ziel / "config.json").write_text(lokal, encoding="utf-8")

    _einspielen(ziel, quelle)

    assert (ziel / "config.json").read_text(encoding="utf-8") == lokal
    assert not (tmp_path / "_alt").exists()


def test_nur_versionierte_dateien(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle"
    quelle.mkdir()
    _git(quelle, "init", "-q")
    (quelle / ".gitignore").write_text("config.json\n", encoding="utf-8")
    (quelle / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(quelle, "add", ".gitignore", "a.py")
    _git(quelle, "commit", "-q", "-m", "a")
    (quelle / "notiz.txt").write_text("nicht versioniert\n", encoding="utf-8")
    (quelle / "config.json").write_text("{}", encoding="utf-8")
    ziel = tmp_path / "ziel"

    review_hook.installiere(ziel, quelle, zeit="t")

    assert sorted(p.name for p in ziel.iterdir()) == sorted([".gitignore", "a.py", review_hook.STAND_DATEI])


def test_ohne_git_gilt_gitignore_der_quelle(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle"
    (quelle / "sitzungen").mkdir(parents=True)
    (quelle / ".gitignore").write_text("# Laufzeit\nsitzungen/\n*.jsonl\nconfig.json\n", encoding="utf-8")
    (quelle / "a.py").write_text("x = 1\n", encoding="utf-8")
    (quelle / "config.json").write_text("{}", encoding="utf-8")
    (quelle / "ausloeser.jsonl").write_text("{}\n", encoding="utf-8")
    (quelle / "sitzungen" / "s.json").write_text("{}", encoding="utf-8")
    ziel = tmp_path / "ziel"

    review_hook.installiere(ziel, quelle, zeit="t")

    assert sorted(p.name for p in ziel.iterdir()) == sorted([".gitignore", "a.py", review_hook.STAND_DATEI])


@pytest.mark.parametrize("skript", ["install.sh", "install.ps1"])
def test_installer_geben_klon_mit(skript: str) -> None:
    """Der eingespielte Skill-Ordner hat kein git: die Installer nennen den Klon als Quelle der Dateiliste."""
    zeilen = [z for z in (SKILL / skript).read_text(encoding="utf-8").splitlines() if "review_hook" in z]
    assert any("--klon" in z and not z.lstrip().startswith("#") for z in zeilen), zeilen
