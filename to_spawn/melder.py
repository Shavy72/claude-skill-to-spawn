"""Melder des Aufsehers: eine Mail an den Menschen, nur wenn es wirklich zählt (#213).

``melden(repo, art, betreff, text, schluessel)`` schickt über den Befehl aus der
Repo-Konfig ``mail.befehl`` (argv-Liste; stdin = JSON ``{art, betreff, text, an}``,
``an`` = ``mail.ziel``, darf leer sein — dann sucht der Befehl die Adresse selbst).
Exit 0 des Befehls = gesendet.

Politik (#213, #885):
* ``mail.nacht`` (z. B. ``"22:00-07:00"``, Ortszeit): im Nacht-Fenster geht nur
  :data:`NACHTS` (``server_tot``) raus. Alles andere bleibt stumm, wird im Zustand
  unter ``nachts_unterdrueckt`` gemerkt und kommt später nicht als Nachhol-Schwall nach.
* Tagsüber: Arten aus :data:`NUR_LOG` / :data:`NUR_LOG_PRAEFIXE` (keine Mail je Spec,
  keine Aufpasser- oder Leitstand-Mail) gehen nur ins Log.
* ``mail.nur_kritisch`` (Vorgabe an): tagsüber gehen nur :data:`IMMER` (Kritisch-Mails,
  bei denen der Nutzer sofort handeln muss, sowie der eine ``morgen_bericht``) raus,
  alles andere steht nur im Log.
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
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, prozessbaum

log = logging.getLogger("to_spawn.melder")

#: Kritische Arten, bei denen der Nutzer sofort handeln muss (tagsüber immer erlaubt, #885).
KRITISCH = frozenset(
    {
        "gate_rot",
        "session_tot",
        "live_beweis_blockiert",
        "waechter_ausweich",
        "waechter_pause",  # #254: „Aufseher pausiert bis 5:40“ darf nicht im Log versanden
        # Nacht-Checkpoints (#285): angenommene Entscheidung und offene Frage ohne
        # Vorschlag muss der Mensch am Morgen sehen können.
        "checkpoint_annahme",
        "checkpoint_offen",
        "server_tot",  # #885: einzige Art, die auch nachts rausgeht
    }
)
#: Arten, die auch tagsüber nie als Mail rausgehen — nur ins Log (#885, Entscheidung 27.11.).
NUR_LOG = frozenset(
    {
        "spec_fertig",
        "leitstand_start",
        "aufpasser_weg",
        "aufpasser_da",
    }
)
#: Präfixe für Arten, die tagsüber nur ins Log gehören (#885).
NUR_LOG_PRAEFIXE = ("aufpasser_", "leitstand_")
#: Arten, die tagsüber auch bei ``mail.nur_kritisch`` rausgehen (#214, #885).
IMMER = KRITISCH | {"morgen_bericht", "probesitz_gruen"}
#: Einzige Art, die im Nacht-Fenster ``mail.nacht`` rausgeht (#885).
NACHTS = frozenset({"server_tot"})
#: Standard-Nachtfenster laut Entscheidung 27.11. (#885).
NACHT_VORGABE = "22:00-07:00"
ZEITLIMIT_S = 120


def zustand_ordner() -> Path:
    """``$TO_SPAWN_WAECHTER_ORDNER`` oder ``~/.claude/to-spawn/waechter``."""
    eigen = os.environ.get("TO_SPAWN_WAECHTER_ORDNER")
    return Path(eigen).expanduser() if eigen else Path.home() / ".claude" / "to-spawn" / "waechter"


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
    zwischen.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    zwischen.replace(datei)


def _zustand_datei(repo: Path, gh_repo: str = "") -> Path:
    return zustand_ordner() / f"{repo_kennung(repo, gh_repo)}_meldungen.json"


def schon_gesendet(repo: Path, schluessel: str, gh_repo: str = "") -> bool:
    """Wurde die Meldung mit diesem Schlüssel schon erfolgreich verschickt?"""
    zustand = lade_json(_zustand_datei(repo, gh_repo))
    return schluessel in set(zustand.get("gesendet") or [])


def war_nachts_stumm(repo: Path, schluessel: str, gh_repo: str = "") -> bool:
    """Wurde dieser Schlüssel nachts unterdrückt (kein Nachhol-Schwall, #885)?"""
    zustand = lade_json(_zustand_datei(repo, gh_repo))
    return schluessel in set(zustand.get("nachts_unterdrueckt") or [])


def ist_nur_log(art: str) -> bool:
    """``True`` für Arten, die laut Mail-Regel (#885) auch tagsüber nur ins Log gehen."""
    return art in NUR_LOG or art.startswith(NUR_LOG_PRAEFIXE)


def kritisch_liste() -> tuple[str, ...]:
    """Sortierte Liste der Kritisch-Ausnahmen für die Anzeige im Morgen-Bericht (#885)."""
    return tuple(sorted(KRITISCH))


def _minuten(text: str) -> int:
    teile = text.strip().split(":")
    if len(teile) != 2:
        raise ValueError(text)
    stunde, minute = int(teile[0]), int(teile[1])
    if not (0 <= stunde < 24 and 0 <= minute < 60):
        raise ValueError(text)
    return stunde * 60 + minute


def ist_nacht(konfig: dict[str, Any], jetzt: datetime | None = None) -> bool:
    """Liegt ``jetzt`` (Ortszeit) im Fenster ``mail.nacht`` „HH:MM-HH:MM“? Leer/kaputt = nein."""
    mail = konfig.get("mail")
    roh = mail.get("nacht") if isinstance(mail, dict) else ""
    fenster = NACHT_VORGABE if roh is True else str(roh or "").strip()
    if not fenster:
        return False
    try:
        von_text, bis_text = fenster.split("-")
        von, bis = _minuten(von_text), _minuten(bis_text)
    except ValueError:
        log.warning("mail.nacht unlesbar (%r, erwartet „HH:MM-HH:MM“) — keine Nachtruhe.", fenster)
        return False
    zeit = (jetzt or datetime.now()).astimezone()
    minute = zeit.hour * 60 + zeit.minute
    if von <= bis:
        return von <= minute < bis
    return minute >= von or minute < bis  # über Mitternacht


def darf_raus(art: str, konfig: dict[str, Any], *, jetzt: datetime | None = None) -> bool:
    """Politik (#885): nachts nur :data:`NACHTS`; tagsüber keine :func:`ist_nur_log`-Arten,
    sonst :data:`IMMER` immer, andere nur ohne ``nur_kritisch``."""
    if ist_nacht(konfig, jetzt):
        return art in NACHTS
    if ist_nur_log(art):
        return False
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
    jetzt: datetime | None = None,
) -> bool:
    """Eine Meldung verschicken; ``True`` nur, wenn der Befehl sie mit Exit 0 angenommen hat."""
    konfig = konfig if konfig is not None else config.lade(repo)
    datei = _zustand_datei(repo, gh_repo)
    if ist_nacht(konfig, jetzt) and art not in NACHTS:
        zustand = lade_json(datei)
        unterdrueckt = set(zustand.get("nachts_unterdrueckt") or [])
        if schluessel not in unterdrueckt:
            unterdrueckt.add(schluessel)
            zustand["nachts_unterdrueckt"] = sorted(unterdrueckt)
            speichere_json(datei, zustand)
        log.info("Meldung %s nur im Log (Nachtruhe mail.nacht): %s — %s", art, betreff, text)
        return False
    if not darf_raus(art, konfig, jetzt=jetzt):
        grund = "nur Log (Mail-Regel)" if ist_nur_log(art) else "mail.nur_kritisch"
        log.info("Meldung %s nur im Log (%s): %s — %s", art, grund, betreff, text)
        return False
    zustand = lade_json(datei)
    gesendet = set(zustand.get("gesendet") or [])
    if schluessel in gesendet:
        log.info("Meldung %s schon verschickt (%s) — kein zweites Mal.", art, schluessel)
        return False
    unterdrueckt = set(zustand.get("nachts_unterdrueckt") or [])
    if schluessel in unterdrueckt:
        log.info("Meldung %s war nachts stumm (%s) — kein Nachhol-Schwall.", art, schluessel)
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
            **prozessbaum.fensterlos(),
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
