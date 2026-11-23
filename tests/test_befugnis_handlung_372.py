"""Verlauf nur nach Handlungen prüfen (Skill-Issue #372, PR 374 Runde 9, Variante V-a).

:func:`befugnis.handlung_klasse_2` sucht im Transkript-Verlauf nur noch Handlungen an Live,
Kosten, Konten, Zugang und Löschen (Befehle, eigene Texte, Werkzeug-Namen, Datei-Pfade). Die
Frage selbst bleibt bei der breiten Liste :func:`befugnis.ist_klasse_2`. Jeder Grund trägt
selbst ein Klasse-2-Wort, sonst sähe der Briefkasten hinter der Frage Klasse 3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import befugnis



def h(**teile: object) -> str | None:
    return befugnis.handlung_klasse_2(**teile)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "befehl",
    [
        "bash scripts/safe_deploy_vps.sh --skip-ci",
        "bash scripts/deploy.sh",  # Lücke aus Review R7
        "ssh clawy-vps 'docker restart app'",
        'bash -c "ssh clawy-vps uptime"',
        'eval "bash scripts/safe_deploy_vps.sh"',
        'timeout 600 bash -c "bash scripts/safe_deploy_vps.sh --skip-ci"',
        "cd /wt/x && ./scripts/deploy.sh",
        "gh pr merge 374",
        "git push -f origin master",
        "git push origin HEAD:master",
        "rm -rf /opt/app",
        "curl -X POST https://api.example.com/v1/x",
        "curl -s -d '{\"a\": 1}' https://api.example.com",
        "bw get password x",
        "cat /opt/app/.env",
        "kubectl apply -f k8s.yaml",
        "docker push registry/app:latest",
        "npm run deploy",
        "psql -c 'DROP TABLE kunden'",
        "docker volume rm daten",
        "stripe charges list",
    ],
)
def test_befehl_ist_klasse_2(befehl: str) -> None:
    grund = h(befehle=[befehl])
    assert grund is not None, befehl
    assert befugnis.ist_klasse_2(grund), grund  # Grund trägt selbst ein Klasse-2-Wort


@pytest.mark.parametrize(
    "befehl",
    [
        'git commit -m "fix live"',
        'git commit -m "Kosten und Konto aufräumen"',
        "gh pr create --title 'live-Fix' --body 'prod vps'",
        "git commit -F - <<'EOF'\nfix: vps live prod\nKosten im Konto\nEOF",
        "pytest tests/test_deploy_x.py",
        "python3 -m pytest tests/test_deploy_x.py -q",
        "git add scripts/safe_deploy_vps.sh",
        "grep -rn vps .",
        "git push -f origin HEAD:refs/heads/wip/12-x",
        "git push --force-with-lease origin ticket/12-x",
        "rm -rf /home/bau/wt/wt-1/x",
        "rm -rf /tmp/probe-1",
        "bash scripts/staging/staging_deploy.sh",
        "git push origin ticket/372-main-fix",
        "git status --short",
    ],
)
def test_befehl_ist_klasse_3(befehl: str) -> None:
    assert h(befehle=[befehl]) is None, befehl


@pytest.mark.parametrize(
    "text",
    [
        "Ich deploye den Stand jetzt live …",
        "Jetzt auf LIVE schalten.",
        "Jetzt der Live-Deploy.",
        "Prod-Stand ist aktuell.",
        "Das goLive mache ich jetzt",
        "Ich lege ein Kundenkonto an",
        "Ich kaufe das Paket für 20 €",
        "Deploy auf vps prüfen",  # Task-Prompt
        "Der Stand geht jetzt live.",
        "Ich lösche die Datenbank auf dem Server.",
    ],
)
def test_text_ist_klasse_2(text: str) -> None:
    grund = h(texte=[text])
    assert grund is not None, text
    assert befugnis.ist_klasse_2(grund), grund


@pytest.mark.parametrize(
    "text",
    [
        "Ich deliver die Änderung jetzt.",
        "Fehler reproduziert, Rot-Beweis steht.",
        # Runde 9, absichtlich Klasse 3: Zustand ohne Handlung (vorher Klasse 2 über die Wortliste).
        "Die Produktion läuft wieder.",
        "Das Passwort liegt in Bitwarden.",
        "Der Test prüft live und prod im Text.",
        "Kosten sind im Bericht erwähnt.",
    ],
)
def test_text_ist_klasse_3(text: str) -> None:
    assert h(texte=[text]) is None, text


def test_werkzeug_und_pfad() -> None:
    assert befugnis.ist_klasse_2(h(werkzeuge=["mcp__x__purchase_phone"]) or "")
    assert h(werkzeuge=["mcp__x__list_phones", "Read", "Bash"]) is None
    assert befugnis.ist_klasse_2(h(pfade=["/opt/app/.env"]) or "")
    assert h(pfade=["/wt/x/to_spawn/befugnis.py", "/wt/x/.env.example"]) is None


def test_grund_ist_erste_treffer_zeile_zusammengezogen() -> None:
    grund = h(befehle=["git status\nbash   scripts/deploy.sh   --x\nls"])
    assert grund is not None and grund.endswith("bash scripts/deploy.sh --x")
    assert h() is None and h(texte=["", "  "], befehle=[""]) is None


@pytest.mark.parametrize(
    "frage",
    ["goLive", "neuerVps", "deployProd", "Kundenkonto", "Bankkonto", "Mehrkosten", "Monatsrechnung"],
)
def test_frage_bleibt_breit_keine_durchrutscher(frage: str) -> None:
    """Frage und Rückfall prüfen mit der breiten Liste: Wortteile zählen (Runde 9)."""
    assert befugnis.ist_klasse_2(f"Soll ich {frage} angehen?")


def test_wortanfang_suche_entfaellt() -> None:
    # U: breite Erkennung gilt wieder, Sicherheit vor Quote (#372): die Wortanfang-Suche ist wieder da (Verlauf).
    assert hasattr(befugnis, "ist_klasse_2_am_wortanfang")
