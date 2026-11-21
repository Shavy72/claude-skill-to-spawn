"""DuoPlus-Altwerte: das heutige Verhalten des Skills als Vorgabe (L1 Etappe 1, #46).

Einziger Ort, an dem Namen und Pfade des ersten App-Repos (DuoPlus) stehen.
Nur ``to_spawn/config.py`` importiert diese Datei; jeder andere Code liest die
Werte über ``config.lade`` / ``config.pfad`` / ``config.maschine``. Fehlt ein
Schlüssel in der Konfig, gilt der Wert von hier — so bleibt das Verhalten gleich.
Etappe 5 leert diese Datei, sobald jedes Repo seine Werte selbst einträgt.
"""

from __future__ import annotations

from typing import Any

#: Ordner im App-Repo (relativ zur Repo-Wurzel), Fundstellen #14–#16.
PFADE: dict[str, str] = {
    "manifeste": "docs/agents/manifests",
    "bau_log": "docs/agents/bau_log",
    "agenten": "docs/agents",
}

#: Deploy-Skripte, die der Aufpasser erkennt und einordnet (Fundstellen #7, #8).
#: ``art``: "live" oder "pruef"; ``pruef_schalter``: steht dieser Schalter im Aufruf,
#: zählt ein Live-Skript als "pruef" ("" = kein Schalter); ``fern``: nur als
#: Argument eines ``ssh``-Aufrufs erkannt.
DEPLOY_SKRIPTE: list[dict[str, Any]] = [
    {"name": "safe_deploy_vps.sh", "art": "live", "pruef_schalter": "--nur-gate", "fern": False},
    {"name": "staging_deploy.sh", "art": "live", "pruef_schalter": "", "fern": False},
    {"name": "deploy_schlange", "art": "pruef", "pruef_schalter": "", "fern": False},
    {"name": "server_tuer.sh", "art": "live", "pruef_schalter": "", "fern": True},
]

#: Schalter-Skript im App-Repo (Fundstelle #12, Ticket #560).
STAGING_SCHALTER = "scripts/schalter.py"
#: Wirkungskreis-Skript im App-Repo (Fundstelle #13, #583).
BEFEHL_WIRKUNGSKREIS = "scripts/wirkungskreis.py"
#: Deploy-Log der Staging-Umgebung auf dem Bau-Server (Fundstelle #10).
STAGING_LOG = "/home/bau/staging/deploys.jsonl"

#: Maschinen-Liste (Fundstellen #1–#4, #6): die zwei eigenen Repos dieser Maschine.
#: ``ticket_repo`` "" = Slug von ``slug``; der Skill-Ordner fragt Tickets in DuoPlus ab.
REPOS: list[dict[str, str]] = [
    {
        "slug": "Shavy72/duoplus-management",
        "pfad": "~/duoplus-management",
        "ticket_repo": "",
        "worktree_basis": "",
    },
    {
        "slug": "Shavy72/claude-skill-to-spawn",
        "pfad": "~/claude-skill-to-spawn",
        "ticket_repo": "Shavy72/duoplus-management",
        "worktree_basis": "",
    },
]
#: Sperr-Dateien laufender Staging-Deploys (Fundstelle #11).
STAGING_SPERREN: list[str] = ["/home/bau/staging/.deploy.lock"]
#: Hausmeister: Such-Muster der Worktree-Wurzeln (#4) und Ordner-Präfixe (#5).
HAUSMEISTER_WURZELN: list[str] = ["~/wt/*", "~/wt-*", "~/duoplus-[0-9]*", "C:/dev/wt-*"]
HAUSMEISTER_ORDNER_PRAEFIXE: list[str] = ["wt", "skill", "duoplus"]
#: Hausmeister: Ordnernamen, die immer bleiben (#1536), zusätzlich zu den Hauptrepos.
HAUSMEISTER_GESCHUETZTE_ORDNER: list[str] = ["duoplus-leit", "deploy-schlange", "obsidian"]
#: Temp-Ordner auf der Platte statt RAM-Platte /tmp (#136); Maschinen-Liste ``temp_ordner``.
TEMP_ORDNER: str = "~/tmp-bau"
#: Altbestand-Muster der Temp-Reste (#136); Maschinen-Liste ``temp_reste`` ersetzt die Liste.
TEMP_RESTE: list[str] = ["/tmp/*", "/tmp/claude-*/*", "~/AppData/Local/Temp/*"]
