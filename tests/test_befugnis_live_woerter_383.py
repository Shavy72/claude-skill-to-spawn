"""befugnis: deploy, push und merge als Klasse-2-Signale mit Ganzwort-Grenzen (#383).

Prüft:
- freie Fragen (:func:`befugnis.ist_klasse_2`) erkennen ``deploy`` (ohne Staging), ``merge``
  (ohne ``merge-base``) und ``push`` (außerhalb eigener ``ticket/``- bzw. ``wip/``-Zweige),
- Bau-Alltag (Staging-Deploy, Ticket-/WIP-Push, ``tests/test_deploy_x.py``, ``DEPLOY_WORKERS=8``,
  ``git merge-base``, Wortteile wie ``Deployment``) bleibt Klasse 3,
- Befehle (:func:`befugnis.handlung_klasse_2`) erkennen jede gefährliche ``git push``-Form
  (bloßes ``git push``, ``HEAD``, ``--tags``, ``:ticket``, ``+ticket``, ``--no-pager``, ``-P``,
  ``-C "/pfad mit leer"``), während normale ``ticket/``- und ``wip/``-Pushes Klasse 3 bleiben,
- :func:`befugnis.einordnen` behält seine bestehenden Klassen-Regeln unverändert,
- Briefkasten und Leiter reichen solche Fragen an den Menschen statt an den Entscheider-Helfer.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, befugnis, briefkasten, leiter


@pytest.mark.parametrize(
    "frage",
    [
        "Soll ich scripts/deploy.sh ausführen?",
        "Soll ich den Stand jetzt deployen?",
        "Do you want to proceed? bash scripts/deploy.sh",
        "Soll ich den PR mergen?",
        "Soll ich gh pr merge 374 ausführen?",
        "Soll ich den Zweig in main mergen?",
        "Soll ich git push origin main ausführen?",
        "Soll ich git push ausführen?",
        "Soll ich direkt auf main pushen?",
        "Soll ich git push origin +ticket/12-kurz ausführen?",
        "Soll ich git push origin :ticket/12-kurz ausführen?",
        "Soll ich git push origin ticket/12-x:main ausführen?",
        "Soll ich git push --tags ausführen?",
    ],
)
def test_frage_mit_deploy_push_merge_ist_klasse_2(frage: str) -> None:
    assert befugnis.ist_klasse_2(frage) is True, frage


@pytest.mark.parametrize(
    "frage",
    [
        "Soll ich auf Staging deployen?",
        "Soll ich bash scripts/deploy_staging.sh starten?",
        "Soll ich bash scripts/deploy-staging.sh ausführen?",
        "Soll ich tests/test_deploy_x.py umbenennen?",
        "Soll ich DEPLOY_WORKERS=8 setzen?",
        "Soll ich die Deployment-Doku aktualisieren?",
        "Soll ich git merge-base prüfen?",
        "Soll ich auf ticket/12-fix pushen?",
        "Soll ich git push origin ticket/12-fix ausführen?",
        "Soll ich git push origin ticket/372-main-fix ausführen?",
        "Soll ich git push --force-with-lease origin ticket/12-fix ausführen?",
        "Soll ich wip/12-sicherung pushen?",
    ],
)
def test_alltag_und_wortteile_bleiben_klasse_3(frage: str) -> None:
    assert befugnis.ist_klasse_2(frage) is False, frage


def test_ist_klasse_2_kombiniert_ziel_und_art_fuer_staging_deploy() -> None:
    assert befugnis.ist_klasse_2("Ausrollen?", "ja", "staging", "deploy") is False
    assert befugnis.ist_klasse_2("Ausrollen?", "ja", "staging;live", "deploy") is True
    assert befugnis.ist_klasse_2("Ausrollen?", "ja", "", "deploy") is True


@pytest.mark.parametrize(
    "befehl",
    [
        "git push",
        "git push origin HEAD",
        "git push --tags",
        "git --no-pager push origin master",
        "git -P push origin master",
        'git -C "/pfad mit leer" push origin master',
        "git push origin :ticket/12-kurz",
        "git push origin +ticket/12-kurz",
        "git push origin --delete ticket/12-kurz",
        "git push -d origin ticket/12-kurz",
        "git push origin ticket/12-x:master",
    ],
)
def test_handlung_gefaehrliche_push_befehle_sind_klasse_2(befehl: str) -> None:
    grund = befugnis.handlung_klasse_2(befehle=[befehl])
    assert grund is not None, befehl
    assert befugnis.ist_klasse_2(grund), grund


@pytest.mark.parametrize(
    "befehl",
    [
        "git push -u origin ticket/12-kurz",
        "git -C /home/bau/wt/x push origin ticket/12-kurz",
        "git push -f origin HEAD:refs/heads/wip/12-x",
        "git push --force-with-lease origin ticket/12-x",
        "git push origin ticket/372-main-fix",
    ],
)
def test_handlung_eigene_zweig_pushes_bleiben_klasse_3(befehl: str) -> None:
    assert befugnis.handlung_klasse_2(befehle=[befehl]) is None, befehl


@pytest.mark.parametrize(
    "text",
    [
        "deploy()",
        "bash $(ls scripts/*deploy*)",
        "Skill deploy",
        "gh pr merge 374",
    ],
)
def test_wortanfang_erkennt_ganzwort_deploy_und_merge(text: str) -> None:
    assert befugnis.ist_klasse_2_am_wortanfang(text) is True, text


@pytest.mark.parametrize(
    "text",
    [
        "DEPLOY_WORKERS=8 python -m pytest tests -q",
        "bash scripts/deploy_staging.sh",
        "pytest tests/test_deploy_x.py",
        "git merge-base main HEAD",
        "Deployment-Doku aktualisiert",
    ],
)
def test_wortanfang_kein_fehlalarm_fuer_alltag_und_wortteile(text: str) -> None:
    assert befugnis.ist_klasse_2_am_wortanfang(text) is False, text


def test_einordnen_unveraendert() -> None:
    assert befugnis.einordnen("staging", "deploy", True) == 1
    assert befugnis.einordnen("arbeitsordner", "deploy", True) == 3
    assert befugnis.einordnen("hauptzweig", "push") == 3
    assert befugnis.einordnen("hauptzweig", "merge") == 3


def test_briefkasten_und_leiter_reichen_deploy_merge_push_an_menschen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    for frage in (
        "Soll ich scripts/deploy.sh ausführen?",
        "Soll ich den PR mergen?",
        "Soll ich git push origin main ausführen?",
    ):
        schritt = leiter.entscheide(
            leiter.Lage(
                stufe="offen",
                fenster=aufseher_stand.RUECKFRAGE,
                still_min=5,
                kontext_k=None,
                prompt_datei=False,
                frage=frage,
            ),
            leiter.Gemerkt(0, None),
            time.time(),
            250.0,
        )
        assert schritt.aktion == "mensch", frage
        fid = briefkasten.einwerfen("7", frage, "ja", 3, [], hauptbaum=tmp_path)
        assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
