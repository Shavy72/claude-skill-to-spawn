"""Review 393, Fix-Runde 3: Projektregeln je Abschnitt übernehmen und dem Prüfer maschinell mitgeben.

- Gleiche Regelzeile in zwei Alt-Vorlagen landet in beiden Abschnitten; ein vorhandener Abschnitt wächst,
  statt dass ein zweiter gleichen Namens entsteht; Handänderungen bleiben.
- Alt-Vorlagen, die frühere Installer-Läufe unter ``_alt/review-*/auftraege`` gesichert haben, zählen mit.
- Die Route gibt je Prüfer die passenden Regelzeilen mit (``pruefer[].regeln``), gefiltert auf ``Falls Repo <name>``.
- Bricht das Einspielen ab, ist die lokale Config noch da (erst einspielen, dann entschlacken).
- ``main(['--klon', …])`` spielt mit Test-HOME nur Versioniertes ein.

Alle Regel-Texte hier sind Kunsttext.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import review_hook

QUELLE = review_hook.WERKZEUG_QUELLE
LUCHS = "Kunstregel Luchs: Kunst-Daten nur über `kunst_tor()` schreiben."
ZEBRA = "Falls Repo kunst-app: Kunstregel Zebra nur über `kunst_modul.tor()`."
WOMBAT = "Falls Repo andere-app: Kunstregel Wombat nie im Kunst-Pfad."
OKAPI = "Kunstregel Okapi: Abläufe nur über den Kunst-Weg."
IBIS = "Kunstregel Ibis: nur für Kunst-Daten."


def _abschnitt(text: str, name: str) -> str:
    return text.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]


def _alt_vorlage(ziel: Path, name: str, zeile: str) -> None:
    (ziel / "auftraege").mkdir(parents=True, exist_ok=True)
    shutil.copy2(QUELLE / "auftraege" / f"{name}.md", ziel / "auftraege" / f"{name}.md")
    with (ziel / "auftraege" / f"{name}.md").open("a", encoding="utf-8") as datei:
        datei.write(f"\n{zeile}\n")


def test_gleiche_zeile_in_zwei_vorlagen_landet_in_beiden_abschnitten(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    _alt_vorlage(ziel, "python", LUCHS)
    _alt_vorlage(ziel, "datenbank", LUCHS)

    review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t")

    text = (ziel / "regeln.md").read_text(encoding="utf-8")
    assert LUCHS in _abschnitt(text, "python") and LUCHS in _abschnitt(text, "datenbank"), text


def test_vorhandener_abschnitt_waechst_statt_zweitem(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    ziel.mkdir()
    hand = "# Meine Regeln\n\n## python\n\nHandzeile Kunst eins.\n\n## spec\n\nHandzeile Kunst zwei.\n"
    (ziel / "regeln.md").write_text(hand, encoding="utf-8")
    _alt_vorlage(ziel, "python", ZEBRA)

    review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t")

    text = (ziel / "regeln.md").read_text(encoding="utf-8")
    assert text.count("## python") == 1, text
    python_teil = _abschnitt(text, "python")
    assert ZEBRA in python_teil and "Handzeile Kunst eins." in python_teil
    assert _abschnitt(text, "spec").strip() == "Handzeile Kunst zwei."
    assert text.startswith("# Meine Regeln\n")


def test_alt_sicherungen_frueher_laeufe_zaehlen_mit(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    review_hook.installiere(ziel, QUELLE, zeit="t")  # aktueller Stand, Vorlagen unverändert
    _alt_vorlage(tmp_path / "_alt" / "review-20250101-000000", "python", OKAPI)

    review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t2")

    text = (ziel / "regeln.md").read_text(encoding="utf-8")
    assert OKAPI in _abschnitt(text, "python"), text
    review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t3")
    assert (ziel / "regeln.md").read_text(encoding="utf-8") == text  # nie doppelt


def test_abbruch_beim_einspielen_laesst_config_stehen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ziel = tmp_path / "review"
    ziel.mkdir()
    standard = (QUELLE / "config.standard.json").read_text(encoding="utf-8")
    (ziel / "config.json").write_text(standard, encoding="utf-8")  # gleicht dem Standard → würde entfernt

    def kaputt(*_args: object, **_kwargs: object) -> list[str]:
        raise OSError("Kunst-Abbruch")

    monkeypatch.setattr(review_hook, "installiere", kaputt)
    with pytest.raises(OSError):
        review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t")
    assert (ziel / "config.json").read_text(encoding="utf-8") == standard


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(ordner), *args],
        check=True,
        capture_output=True,
    )


def _repo_mit_python_aenderung(ordner: Path, origin: str | None = None) -> Path:
    ordner.mkdir()
    _git(ordner, "init", "-q")
    (ordner / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(ordner, "add", "a.py")
    _git(ordner, "commit", "-q", "-m", "a")
    (ordner / "a.py").write_text("x = 2\n", encoding="utf-8")
    if origin:
        _git(ordner, "remote", "add", "origin", origin)
    return ordner


def _route(ziel: Path, repo: Path) -> tuple[dict, str]:
    lauf = subprocess.run(
        [sys.executable, str(ziel / "review_route.py"), "--moment", "commit", "--repo", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(lauf.stdout), lauf.stderr


def _python_regeln(route: dict) -> list[str]:
    pruefer = [p for p in route["pruefer"] if "python" in p["auftraege"]]
    assert pruefer, route["pruefer"]
    return pruefer[0].get("regeln", [])


def test_route_gibt_pruefer_die_passenden_regelzeilen_mit(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    review_hook.installiere(ziel, QUELLE, zeit="t")
    (ziel / "regeln.md").write_text(
        f"# Kunst-Kopf\n\nKopfzeile gilt für keinen Auftrag.\n\n## python\n\n{OKAPI}\n{ZEBRA}\n{WOMBAT}\n\n"
        f"## datenbank\n\n{IBIS}\n",
        encoding="utf-8",
    )

    kunst, _ = _route(ziel, _repo_mit_python_aenderung(tmp_path / "kunst-app"))
    regeln = _python_regeln(kunst)
    assert OKAPI in regeln and ZEBRA in regeln, regeln
    assert WOMBAT not in regeln and IBIS not in regeln and not any("Kopfzeile" in z for z in regeln)

    andere, _ = _route(ziel, _repo_mit_python_aenderung(tmp_path / "andere-app"))
    assert ZEBRA not in _python_regeln(andere) and WOMBAT in _python_regeln(andere)

    # Worktree mit fremdem Ordnernamen: der Name aus origin zählt.
    wt, _ = _route(ziel, _repo_mit_python_aenderung(tmp_path / "wt-1", "https://example.invalid/k/kunst-app.git"))
    assert ZEBRA in _python_regeln(wt)


def test_route_ohne_regeln_bleibt_laut(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    review_hook.installiere(ziel, QUELLE, zeit="t")

    route, fehler = _route(ziel, _repo_mit_python_aenderung(tmp_path / "kunst-app"))

    assert route["projektregeln"]["vorhanden"] is False
    assert "Projektregeln fehlen" in fehler
    assert _python_regeln(route) == []


def test_main_mit_klon_spielt_nur_versioniertes_ein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    klon = tmp_path / "klon"
    for teil in ("review-hook", "review"):
        (klon / teil).mkdir(parents=True)
        (klon / teil / "a.py").write_text("x = 1\n", encoding="utf-8")
    (klon / "review" / "config.standard.json").write_text("{}\n", encoding="utf-8")
    _git(klon, "init", "-q")
    _git(klon, "add", ".")
    _git(klon, "commit", "-q", "-m", "a")
    for teil in ("review-hook", "review"):
        (klon / teil / "lose.txt").write_text("nicht versioniert\n", encoding="utf-8")
    heim = tmp_path / "heim"
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    hook = heim / ".claude" / "hooks" / "review"

    assert review_hook.main(["--ziel", str(hook), "--klon", str(klon)]) == 0

    werkzeug = heim / ".claude" / "review"
    assert sorted(p.name for p in hook.iterdir()) == sorted(["a.py", review_hook.STAND_DATEI])
    assert sorted(p.name for p in werkzeug.iterdir()) == sorted(
        ["a.py", "config.standard.json", review_hook.STAND_DATEI]
    )
