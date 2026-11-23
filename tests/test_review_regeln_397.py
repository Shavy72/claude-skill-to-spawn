"""Issue 397, Punkt 1: Handänderungen an ``regeln.md`` überleben jeden weiteren Installer-Lauf.

- Eine von Hand mit ``Falls Repo <name>:`` versehene Zeile kommt nicht ohne Präfix aus ``_alt`` zurück.
- Eine von Hand gelöschte Zeile kommt nicht aus ``_alt`` zurück.
- Erste Übernahme aus ``_alt`` klappt weiter, wenn ``regeln.md`` oder ihr Abschnitt fehlt.
- Neue Zeilen einer lokal geänderten Vorlage werden weiter übernommen.

Alle Regel-Texte hier sind Kunsttext.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from to_spawn import review_hook

QUELLE = review_hook.WERKZEUG_QUELLE
GNU = "Kunstregel Gnu: Abläufe nur über das Kunst-Tor."
QUAGGA = "Kunstregel Quagga: Zählungen nur über `kunst_zaehler()`."


def _einspielen(ziel: Path) -> list[str]:
    return review_hook.installiere_werkzeug(ziel, QUELLE, zeit="t")


def _vorlage_mit(ziel: Path, name: str, zeile: str) -> None:
    """Lokal geänderte Vorlage: ausgelieferter Text plus eine eigene Zeile."""
    datei = ziel / "auftraege" / name
    datei.parent.mkdir(parents=True, exist_ok=True)
    text = (QUELLE / "auftraege" / name).read_text(encoding="utf-8")
    datei.write_text(text + f"\n{zeile}\n", encoding="utf-8")


def _nach_erster_uebernahme(tmp_path: Path) -> Path:
    """Installiert mit lokal geänderter ``python.md``: ``GNU`` steht in ``regeln.md`` und in ``_alt``."""
    ziel = tmp_path / "review"
    _vorlage_mit(ziel, "python.md", GNU)
    _einspielen(ziel)
    assert GNU in (ziel / "regeln.md").read_text(encoding="utf-8")
    assert any(GNU in p.read_text(encoding="utf-8") for p in tmp_path.glob("_alt/review-*/auftraege/python.md"))
    return ziel


def _abschnitt(text: str, name: str) -> str:
    return text.split(f"## {name}", 1)[1].split("\n## ", 1)[0]


def test_von_hand_mit_praefix_bleibt(tmp_path: Path) -> None:
    ziel = _nach_erster_uebernahme(tmp_path)
    regeln = ziel / "regeln.md"
    von_hand = regeln.read_text(encoding="utf-8").replace(GNU, f"Falls Repo kunst-app: {GNU}")
    regeln.write_text(von_hand, encoding="utf-8")

    bericht = _einspielen(ziel)

    assert regeln.read_text(encoding="utf-8") == von_hand, bericht


def test_von_hand_geloescht_bleibt_weg(tmp_path: Path) -> None:
    ziel = _nach_erster_uebernahme(tmp_path)
    regeln = ziel / "regeln.md"
    von_hand = "\n".join(z for z in regeln.read_text(encoding="utf-8").splitlines() if z != GNU) + "\n"
    regeln.write_text(von_hand, encoding="utf-8")

    bericht = _einspielen(ziel)

    assert regeln.read_text(encoding="utf-8") == von_hand, bericht


@pytest.mark.parametrize("vorher", [None, "# Kopf\n\n## spec\n\nKunstregel Elch.\n"])
def test_erste_uebernahme_aus_alt(tmp_path: Path, vorher: str | None) -> None:
    """Früher installierter Stand: Regel liegt nur in ``_alt``, ``regeln.md`` fehlt bzw. ohne Abschnitt."""
    ziel = tmp_path / "review"
    _einspielen(ziel)
    alt = tmp_path / "_alt" / "review-frueher"
    _vorlage_mit(alt, "python.md", GNU)
    regeln = ziel / "regeln.md"
    if vorher is not None:
        regeln.write_text(vorher, encoding="utf-8")

    _einspielen(ziel)

    text = regeln.read_text(encoding="utf-8")
    assert GNU in _abschnitt(text, "python")
    if vorher is not None:
        assert "Kunstregel Elch." in _abschnitt(text, "spec")


def test_neue_zeile_aus_lokaler_vorlage_kommt_weiter(tmp_path: Path) -> None:
    ziel = _nach_erster_uebernahme(tmp_path)
    regeln = ziel / "regeln.md"
    regeln.write_text(
        "\n".join(z for z in regeln.read_text(encoding="utf-8").splitlines() if z != GNU) + "\n",
        encoding="utf-8",
    )
    _vorlage_mit(ziel, "python.md", QUAGGA)

    _einspielen(ziel)

    teil = _abschnitt(regeln.read_text(encoding="utf-8"), "python")
    assert QUAGGA in teil and GNU not in teil


def test_lokale_vorlage_zeile_mit_praefix_gilt_als_vorhanden(tmp_path: Path) -> None:
    ziel = _nach_erster_uebernahme(tmp_path)
    regeln = ziel / "regeln.md"
    von_hand = regeln.read_text(encoding="utf-8").replace(GNU, f"Falls Repo kunst-app: {GNU}")
    regeln.write_text(von_hand, encoding="utf-8")
    _vorlage_mit(ziel, "python.md", GNU)

    _einspielen(ziel)

    assert regeln.read_text(encoding="utf-8") == von_hand


def test_falls_repo_muster_gleich_wie_im_werkzeug() -> None:
    werkzeug = (QUELLE / "_gemeinsam.py").read_text(encoding="utf-8")
    treffer = re.search(r'^_FALLS_REPO = re\.compile\(r"(.*)"\)$', werkzeug, re.MULTILINE)
    assert treffer and treffer.group(1) == review_hook._FALLS_REPO.pattern
