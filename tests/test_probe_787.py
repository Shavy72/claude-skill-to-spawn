"""#787: Probe-Vorlage (Abnahme-Ticket mit ``checkpoint:human``) und ``probe/anlegen.sh``
(Staging-Port-Ableitung aus ``--app-port`` + 1 sowie Port-Frei-Prüfung vor dem Anlegen).

Prüft beide Punkte aus #787 mit echten Prozessen und echten Sockets (keine Attrappen):
1. ``probe/gemeinsam/docs/GRILL_probe.md`` (E9) und ``kette/skills/to-tickets/SKILL.md``
   verlangen das Pflicht-Label ``checkpoint:human`` am Abnahme-Ticket ausdrücklich.
2. ``probe/anlegen.sh`` leitet ohne ``--staging-port`` den Staging-Port aus ``--app-port + 1``
   (und ohne ``--live-port`` den Live-Port aus ``--app-port + 2``) ab, erlaubt weiterhin
   explizites Überschreiben und bricht vor dem Anlegen klar ab, wenn ein benötigter Port
   bereits belegt ist.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
ANLEGEN = SKILL / "probe" / "anlegen.sh"
PROTOKOLL = SKILL / "probe" / "gemeinsam" / "docs" / "GRILL_probe.md"
TO_TICKETS = SKILL / "kette" / "skills" / "to-tickets" / "SKILL.md"

BASH = shutil.which("bash")
GIT = shutil.which("git")
brauche_bash = pytest.mark.skipif(BASH is None or GIT is None, reason="braucht bash und git")


def _freie_port_reihe(anzahl: int) -> list[int]:
    """Findet ``anzahl`` aufeinanderfolgende freie Ports auf ``127.0.0.1``."""
    for _ in range(50):
        basis = random.randint(25000, 55000)
        try:
            with contextlib.ExitStack() as stapel:
                for offset in range(anzahl):
                    sock = stapel.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                    sock.bind(("127.0.0.1", basis + offset))
                return [basis + offset for offset in range(anzahl)]
        except OSError:
            continue
    raise RuntimeError(f"Keine {anzahl} aufeinanderfolgenden freien Ports gefunden")


@contextlib.contextmanager
def _belegter_port(port: int):
    """Hält ``127.0.0.1:<port>`` echt als lauschenden Server-Socket belegt."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        yield port
    finally:
        sock.close()


def _anlegen_ausfuehren(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), str(ANLEGEN), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        env=dict(os.environ),
    )


# --- Punkt 1: Abnahme-Ticket immer mit checkpoint:human -----------------------


def test_probe_vorlage_verlangt_checkpoint_human_am_abnahme_ticket() -> None:
    """#787 Punkt 1: ``GRILL_probe.md`` (E9) nennt ``checkpoint:human`` als Pflicht-Label am Abnahme-Ticket."""
    text = PROTOKOLL.read_text(encoding="utf-8")
    abschnitt_e9 = text[text.index("### E9") : text.index("## Verworfen")]
    assert "Abnahme-Ticket" in abschnitt_e9
    assert "checkpoint:human" in abschnitt_e9, "E9 in probe/gemeinsam/docs/GRILL_probe.md fehlt checkpoint:human"
    assert "ready-for-agent" in abschnitt_e9


@brauche_bash
@pytest.mark.parametrize("variante", ("mit-staging", "ohne-staging"))
def test_angelegtes_lauf_repo_erhaelt_checkpoint_human_vorgabe(tmp_path: Path, variante: str) -> None:
    """#787 Punkt 1: Beide Varianten übernehmen die ``checkpoint:human``-Vorgabe in ``docs/GRILL_<datum>_probe.md``."""
    app_port, staging_port, live_port = _freie_port_reihe(3)
    ziel = tmp_path / variante
    lauf = _anlegen_ausfuehren(
        str(ziel),
        variante,
        "--app-port",
        str(app_port),
        "--staging-port",
        str(staging_port),
        "--live-port",
        str(live_port),
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    heute = time.strftime("%Y-%m-%d")
    grill = (ziel / "docs" / f"GRILL_{heute}_probe.md").read_text(encoding="utf-8")
    assert "checkpoint:human" in grill


def test_to_tickets_skill_setzt_checkpoint_human_am_abnahme_ticket_immer() -> None:
    """#787 Punkt 1: ``to-tickets`` verlangt ``checkpoint:human`` beim Anlegen und prüft es beim Anhängen."""
    text = TO_TICKETS.read_text(encoding="utf-8")
    schritt_5 = text[text.index("### 5.") : text.index("<local-ticket-template>")]
    assert "nie nur `ready-for-agent`" in schritt_5, "Schritt 5.1 muss klarstellen: nie nur ready-for-agent"
    punkt_3 = schritt_5[schritt_5.index("3. Das Abnahme-Ticket") :]
    assert "checkpoint:human" in punkt_3, "Schritt 5.3 muss checkpoint:human beim Abnahme-Ticket prüfen"
    lokal_vorlage = text[text.index("<local-ticket-template>") : text.index("</local-ticket-template>")]
    assert "checkpoint:human" in lokal_vorlage, "Lokale Ticket-Vorlage nennt checkpoint:human für Abnahme-Ticket nicht"


# --- Punkt 2: probe/anlegen.sh Staging-Port ableiten + Port-Frei-Prüfung ------


@brauche_bash
def test_anlegen_leitet_staging_port_aus_app_port_plus_eins_ab(tmp_path: Path) -> None:
    """#787 Punkt 2: ``--app-port P --live-port L`` ohne ``--staging-port`` nutzt ``P + 1`` statt fest 18502."""
    app_port, erwarteter_staging, live_port = _freie_port_reihe(3)
    ziel = tmp_path / "mit_abgeleitet"
    lauf = _anlegen_ausfuehren(
        str(ziel),
        "mit-staging",
        "--app-port",
        str(app_port),
        "--live-port",
        str(live_port),
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    ports = json.loads((ziel / "ports.json").read_text(encoding="utf-8"))
    assert ports == {"app": app_port, "staging": erwarteter_staging, "live": live_port}
    konfig = json.loads((ziel / ".to-spawn" / "config.json").read_text(encoding="utf-8"))
    assert konfig["staging"]["url"] == f"http://127.0.0.1:{erwarteter_staging}/"
    assert str(erwarteter_staging) in (ziel / "README.md").read_text(encoding="utf-8")


@brauche_bash
def test_anlegen_leitet_staging_und_live_port_ab_und_erlaubt_expliziten_staging_port(tmp_path: Path) -> None:
    """#787 Punkt 2: Ohne ``--staging-port``/``--live-port`` greift +1/+2; explizites ``--staging-port`` gewinnt."""
    p1, p2, p3, p4 = _freie_port_reihe(4)
    ziel_auto = tmp_path / "auto"
    lauf_auto = _anlegen_ausfuehren(str(ziel_auto), "mit-staging", "--app-port", str(p1))
    assert lauf_auto.returncode == 0, lauf_auto.stdout + lauf_auto.stderr
    assert json.loads((ziel_auto / "ports.json").read_text(encoding="utf-8")) == {
        "app": p1,
        "staging": p2,
        "live": p3,
    }

    ziel_explizit = tmp_path / "explizit"
    lauf_explizit = _anlegen_ausfuehren(
        str(ziel_explizit),
        "mit-staging",
        "--app-port",
        str(p1),
        "--staging-port",
        str(p4),
        "--live-port",
        str(p3),
    )
    assert lauf_explizit.returncode == 0, lauf_explizit.stdout + lauf_explizit.stderr
    assert json.loads((ziel_explizit / "ports.json").read_text(encoding="utf-8")) == {
        "app": p1,
        "staging": p4,
        "live": p3,
    }


@brauche_bash
def test_anlegen_bricht_bei_belegtem_staging_port_vor_dem_anlegen_ab(tmp_path: Path) -> None:
    """#787 Punkt 2: Belegter Staging-Port (z. B. laufende Vor-Probe) → Abbruch Exit 2, kein Ordner angelegt."""
    app_port, staging_port, live_port = _freie_port_reihe(3)
    ziel = tmp_path / "kollision_staging"
    with _belegter_port(staging_port):
        lauf = _anlegen_ausfuehren(
            str(ziel),
            "mit-staging",
            "--app-port",
            str(app_port),
            "--staging-port",
            str(staging_port),
            "--live-port",
            str(live_port),
        )
    assert lauf.returncode == 2, lauf.stdout + lauf.stderr
    assert str(staging_port) in lauf.stderr and "belegt" in lauf.stderr, lauf.stderr
    assert "--staging-port" in lauf.stderr, lauf.stderr
    assert not ziel.exists(), "Bei belegtem Port darf kein Ziel-Ordner entstehen"


@brauche_bash
@pytest.mark.parametrize(
    ("rolle", "schalter"),
    [
        ("app", "--app-port"),
        ("live", "--live-port"),
    ],
)
def test_anlegen_bricht_bei_belegtem_app_oder_live_port_ab(tmp_path: Path, rolle: str, schalter: str) -> None:
    """#787 Punkt 2: Auch belegter App- oder Live-Port bricht vor dem Anlegen klar ab."""
    app_port, staging_port, live_port = _freie_port_reihe(3)
    belegt = app_port if rolle == "app" else live_port
    ziel = tmp_path / f"kollision_{rolle}"
    with _belegter_port(belegt):
        lauf = _anlegen_ausfuehren(
            str(ziel),
            "mit-staging",
            "--app-port",
            str(app_port),
            "--staging-port",
            str(staging_port),
            "--live-port",
            str(live_port),
        )
    assert lauf.returncode == 2, lauf.stdout + lauf.stderr
    assert str(belegt) in lauf.stderr and "belegt" in lauf.stderr, lauf.stderr
    assert schalter in lauf.stderr, lauf.stderr
    assert not ziel.exists()


@brauche_bash
def test_anlegen_ohne_staging_ignoriert_belegten_nachbarport(tmp_path: Path) -> None:
    """#787 Punkt 2: ``ohne-staging`` nutzt keinen Staging-Port und darf an belegtem ``app_port + 1`` nicht scheitern."""
    app_port, nachbar_port, live_port = _freie_port_reihe(3)
    ziel = tmp_path / "ohne_staging_ok"
    with _belegter_port(nachbar_port):
        lauf = _anlegen_ausfuehren(
            str(ziel),
            "ohne-staging",
            "--app-port",
            str(app_port),
            "--live-port",
            str(live_port),
        )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert json.loads((ziel / "ports.json").read_text(encoding="utf-8")) == {
        "app": app_port,
        "staging": None,
        "live": live_port,
    }
