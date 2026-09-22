"""Melder des Wächters: eine Mail an David, nur wenn es wirklich zählt (#213).

``melden(repo, art, betreff, text, schluessel)`` schickt über den Befehl aus der
Repo-Konfig ``mail.befehl`` (argv-Liste; stdin = JSON ``{art, betreff, text, an}``,
``an`` = ``mail.ziel``, darf leer sein — dann sucht der Befehl die Adresse selbst).
Exit 0 des Befehls = gesendet.

Politik:
* ``mail.nur_kritisch`` (Vorgabe an): nur die Arten aus :data:`KRITISCH` und
  ``spec_fertig`` gehen raus, alles andere steht nur im Log. Keine Mail je Ticket-Schluss.
* Doppel-Schutz: jeder ``schluessel`` geht höchstens einmal raus (Zustandsdatei
  unter ``~/.claude/to-spawn/waechter/``). Ein gescheiterter Versand merkt sich
  nichts — der nächste Tick versucht es erneut.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("to_spawn.melder")

#: Arten, die immer rausgehen (auch bei ``mail.nur_kritisch``).
KRITISCH = frozenset(
    {
        "gate_rot",
        "session_tot",
        "live_beweis_blockiert",
        "waechter_ausweich",
        "waechter_pause",  # #254: „Wächter pausiert bis 5:40“ darf nicht im Log versanden
    }
)
#: ``probesitz_gruen`` (#214): die eine Erfolgsmeldung des Probesitz geht auch bei ``nur_kritisch`` raus.
IMMER = KRITISCH | {"spec_fertig", "probesitz_gruen"}
ZEITLIMIT_S = 120


def zustand_ordner() -> Path:
    """``$TO_SPAWN_WAECHTER_ORDNER`` oder ``~/.claude/to-spawn/waechter``."""
    eigen = os.environ.get("TO_SPAWN_WAECHTER_ORDNER")
    return (
        Path(eigen).expanduser()
        if eigen
        else Path.home() / ".claude" / "to-spawn" / "waechter"
    )


def repo_kennung(repo: Path, gh_repo: str = "") -> str:
    """``owner_name`` für Dateinamen (aus ``gh_repo`` oder dem Origin des Repos)."""
    from . import gh

    slug = gh_repo or gh.repo_aus_origin(repo, fallback=repo.name)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", slug.replace("/", "_")).strip("_") or "repo"


def lade_json(datei: Path) -> dict[str, Any]:
    if not datei.is_file():
        return {}
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Zustandsdatei unlesbar (%s) — beginne leer: %s", datei, fehler)
        return {}
    return daten if isinstance(daten, dict) else {}


def speichere_json(datei: Path, daten: dict[str, Any]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_suffix(datei.suffix + ".neu")
    zwischen.write_text(
        json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    zwischen.replace(datei)


def _zustand_datei(repo: Path, gh_repo: str = "") -> Path:
    return zustand_ordner() / f"{repo_kennung(repo, gh_repo)}_meldungen.json"


def schon_gesendet(repo: Path, schluessel: str, gh_repo: str = "") -> bool:
    """Wurde die Meldung mit diesem Schlüssel schon erfolgreich verschickt?"""
    zustand = lade_json(_zustand_datei(repo, gh_repo))
    return schluessel in set(zustand.get("gesendet") or [])


def darf_raus(art: str, konfig: dict[str, Any]) -> bool:
    """Politik: kritische Arten und ``spec_fertig`` immer, andere nur ohne ``nur_kritisch``."""
    nur_kritisch = bool(konfig.get("mail", {}).get("nur_kritisch", True))
    return art in IMMER or not nur_kritisch


def mail_befehl(konfig: dict[str, Any]) -> list[str]:
    """``mail.befehl`` als argv-Liste; leer = kein Versand eingerichtet (bewusste Wahl)."""
    mail = konfig.get("mail")
    befehl = (mail.get("befehl") if isinstance(mail, dict) else None) or []
    if isinstance(befehl, str):
        befehl = befehl.split()
    befehl = [str(teil) for teil in befehl if str(teil).strip()]
    if befehl and befehl[0] in ("python", "python3", "py"):
        befehl[0] = sys.executable  # auf dem Bau-Server gibt es kein „python“ (#213)
    return befehl


def mail_eingerichtet(konfig: dict[str, Any]) -> bool:
    """Hat das Repo einen Mail-Befehl? Ohne ihn ist „keine Mail“ kein Fehler."""
    return bool(mail_befehl(konfig))


def melden(
    repo: Path,
    art: str,
    betreff: str,
    text: str,
    schluessel: str,
    *,
    konfig: dict[str, Any] | None = None,
    gh_repo: str = "",
) -> bool:
    """Eine Meldung verschicken; ``True`` nur, wenn der Befehl sie mit Exit 0 angenommen hat."""
    konfig = konfig if konfig is not None else config.lade(repo)
    if not darf_raus(art, konfig):
        log.info(
            "Meldung %s nur im Log (mail.nur_kritisch): %s — %s", art, betreff, text
        )
        return False
    datei = _zustand_datei(repo, gh_repo)
    zustand = lade_json(datei)
    gesendet = set(zustand.get("gesendet") or [])
    if schluessel in gesendet:
        log.info(
            "Meldung %s schon verschickt (%s) — kein zweites Mal.", art, schluessel
        )
        return False
    befehl = mail_befehl(konfig)
    if not befehl:
        log.warning(
            "Keine Mail möglich: mail.befehl fehlt in .to-spawn/config.json — %s: %s",
            art,
            betreff,
        )
        return False
    nutzlast = {
        "art": art,
        "betreff": betreff,
        "text": text,
        "an": str(konfig.get("mail", {}).get("ziel") or ""),
    }
    try:
        fertig = subprocess.run(
            befehl,
            input=json.dumps(nutzlast, ensure_ascii=False),
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ZEITLIMIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("Mail-Befehl gescheitert (%s): %s", art, fehler)
        return False
    if fertig.returncode != 0:
        log.warning(
            "Mail-Befehl Exit %s (%s): %s",
            fertig.returncode,
            art,
            (fertig.stderr or fertig.stdout)[-300:],
        )
        return False
    gesendet.add(schluessel)
    zustand["gesendet"] = sorted(gesendet)
    speichere_json(datei, zustand)
    log.info("Meldung %s verschickt: %s", art, betreff)
    return True
