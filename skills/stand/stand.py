"""Ticket-Stand je Spec für ``/stand`` — lokal und auf dem Bau-Server gleich (Skill ~/.claude/skills/stand).

Was der Aufrufer bekommt: eine fertige Markdown-Ausgabe (Übersicht aller offenen Specs,
je aktiver Spec eine Feature-Tabelle und wenige gezielte Hinweise). Liest nur GitHub
(ein GraphQL-Abruf je 60 Tickets), ``origin/master`` und — falls erreichbar — den VPS-Stand.
Schreibt nichts, spricht keine Session an.

    python ~/.claude/skills/stand/stand.py            # Spec dieser Session (Aufseher/Bau/Klon): 1 Satz + Session-Check
    python ~/.claude/skills/stand/stand.py 1279       # dasselbe für Spec 1279
    python ~/.claude/skills/stand/stand.py 1279 --details   # alte Tabellen-Ansicht einer Spec
    python ~/.claude/skills/stand/stand.py --alle     # alte Übersicht aller offenen Specs

Status-Regeln (eine Stelle, Reihenfolge zählt): zu → ✅/🚀 · Label checkpoint:human → 👤 ·
Problem im letzten Kommentar → 🟥 · „fertig gebaut“ → 📦 · offene Blocker → ⏳ · sonst 🔨.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("stand")

#: Bekannte Ablageorte des Repos, falls /stand außerhalb eines Klons aufgerufen wird.
REPO_FALLBACK = [
    Path.home() / "Desktop/DuoPlus/duoplus-management",
    Path.home() / "duoplus-management",
]


def repo_finden() -> Path:
    """Git-Wurzel des aktuellen Ordners, sonst bekannter Ablageort. Gelesen wird nur origin/master,
    darum taugt auch ein veralteter Arbeitsbaum (Bau-Server ~/duoplus-management)."""
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    kandidaten = ([Path(r.stdout.strip())] if r.returncode == 0 else []) + REPO_FALLBACK
    for k in kandidaten:
        if (k / ".git").exists() and "duoplus" in k.name.lower():
            return k
    raise SystemExit(
        "Kein DuoPlus-Repo gefunden — /stand im Repo oder einem Klon aufrufen."
    )


REPO = repo_finden()
MANIFEST_ORDNER = "docs/agents/manifests"
VPS_ZIEL = "clawy-vps"
VPS_REPO = "/opt/duoplus-management"
#: Spec gilt als aktiv (Details ohne ``--alle``), wenn sich in diesem Zeitraum etwas bewegt hat.
AKTIV_FENSTER = timedelta(days=3)

PROBLEM = re.compile(
    r"blockiert|mensch nötig|vorfall|abgebrochen|platte .*voll|fehlgeschlagen|rot auf|gate rot",
    re.I,
)
DAVID = re.compile(
    r"wartet auf david|live wartet auf|frage an david|davids? (wort|freigabe)", re.I
)
FERTIG_GEBAUT = re.compile(
    r"fertig gebaut|bau fertig|code fertig|fertig und geprüft", re.I
)

EMOJI = {
    "live": "🚀",
    "zu": "✅",
    "david": "👤",
    "haengt": "🟥",
    "gebaut": "📦",
    "wartet": "⏳",
    "arbeit": "🔨",
}
TEXT = {
    "live": "live",
    "zu": "fertig (nicht live)",
    "david": "wartet auf David",
    "haengt": "hängt",
    "gebaut": "fertig gebaut, wartet auf Merge",
    "wartet": "wartet auf Vorgänger",
    "arbeit": "in Arbeit / frei",
}


@dataclass
class Ticket:
    nummer: int
    titel: str
    offen: bool
    grund: str | None
    zu_am: str | None
    labels: list[str]
    blocker_offen: list[int]
    letzter_kommentar: str
    kommentar_am: str | None
    commit: str | None = None
    live: bool | None = None
    status: str = ""

    @property
    def zuletzt(self) -> str | None:
        return max(filter(None, [self.zu_am, self.kommentar_am]), default=None)


@dataclass
class Spec:
    nummer: int
    titel: str
    feature: str
    tickets: list[Ticket] = field(default_factory=list)


def sh(*args: str, timeout: int = 60) -> str:
    """Befehl im Repo ausführen; bei Fehler leerer Text (Stand soll nie abbrechen)."""
    try:
        r = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug("%s → %s", args[:3], e)
        return ""
    if r.returncode != 0:
        log.debug("%s → %s", args[:3], r.stderr.strip()[:200])
        return ""
    return r.stdout.strip()


def repo_slug() -> tuple[str, str]:
    url = sh("git", "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
    if not m:
        raise SystemExit("origin ist kein GitHub-Repo")
    return m.group(1), m.group(2)


def manifest(spec: int) -> dict | None:
    roh = sh("git", "show", f"origin/master:{MANIFEST_ORDNER}/spec-{spec}.json")
    try:
        return json.loads(roh) if roh else None
    except json.JSONDecodeError:
        return None


def offene_specs() -> list[tuple[int, str]]:
    roh = sh(
        "gh",
        "issue",
        "list",
        "--state",
        "open",
        "--search",
        "Spec in:title",
        "--limit",
        "100",
        "--json",
        "number,title",
    )
    return [
        (i["number"], i["title"])
        for i in json.loads(roh or "[]")
        if i["title"].lower().startswith("spec")
    ]


def folge_tickets(spec: int) -> list[int]:
    roh = sh(
        "gh",
        "issue",
        "list",
        "--state",
        "all",
        "--limit",
        "50",
        "--json",
        "number",
        "--search",
        f'"Teil von #{spec}" OR "Part of #{spec}" OR "Folge von #{spec}"',
    )
    return [i["number"] for i in json.loads(roh or "[]") if i["number"] != spec]


def tickets_holen(nummern: list[int]) -> dict[int, Ticket]:
    """Alle Tickets per GraphQL, 60 je Abruf."""
    owner, name = repo_slug()
    erg: dict[int, Ticket] = {}
    for start in range(0, len(nummern), 60):
        teil = nummern[start : start + 60]
        felder = " ".join(
            f"t{n}:issue(number:{n}){{number title state stateReason closedAt labels(first:15){{nodes{{name}}}} "
            f"blockedBy(first:30){{nodes{{number state}}}} comments(last:1){{nodes{{body createdAt}}}}}}"
            for n in teil
        )
        roh = sh(
            "gh",
            "api",
            "graphql",
            "-f",
            f'query=query{{repository(owner:"{owner}",name:"{name}"){{{felder}}}}}',
        )
        daten = (json.loads(roh) if roh else {}).get("data", {}).get("repository") or {}
        for wert in daten.values():
            if not wert:
                continue
            k = (wert["comments"]["nodes"] or [{}])[0]
            erg[wert["number"]] = Ticket(
                nummer=wert["number"],
                titel=wert["title"],
                offen=wert["state"] == "OPEN",
                grund=wert.get("stateReason"),
                zu_am=wert.get("closedAt"),
                labels=[x["name"] for x in wert["labels"]["nodes"]],
                blocker_offen=[
                    b["number"]
                    for b in wert["blockedBy"]["nodes"]
                    if b["state"] == "OPEN"
                ],
                letzter_kommentar=(k.get("body") or "").strip(),
                kommentar_am=k.get("createdAt"),
            )
    return erg


def vps_head() -> str | None:
    """VPS-Commit, nur wenn per ssh erreichbar und lokal bekannt (auf dem Bau-Server oft nicht)."""
    sha = sh(
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "BatchMode=yes",
        VPS_ZIEL,
        f"cd {VPS_REPO} && git rev-parse HEAD",
        timeout=15,
    )
    bekannt = (
        sha
        and subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=REPO,
            capture_output=True,
        ).returncode
        == 0
    )
    if bekannt:
        return sha
    return None


def commits_je_ticket() -> dict[int, str]:
    """Jüngster Code-Commit auf origin/master mit „(#N)“ im Betreff (Doku-Commits zählen nicht)."""
    roh = sh("git", "log", "origin/master", "--since=90.days", "--format=%H %s")
    erg: dict[int, str] = {}
    for zeile in roh.splitlines():
        sha, _, betreff = zeile.partition(" ")
        if betreff.startswith("docs(") or betreff.startswith("chore(kontext)"):
            continue
        for n in re.findall(r"\(#(\d+)\)", betreff):
            erg.setdefault(int(n), sha)
    return erg


#: Review-Hook meldet nach Stop-Zählung, Beleg folgt meist Minuten später; blockiert nichts (Befund 18.11.).
FALSCHALARM = re.compile(r"review angefordert, aber kein beleg", re.I)


def status_setzen(t: Ticket) -> None:
    if FALSCHALARM.search(t.letzter_kommentar[:400]):
        t.letzter_kommentar = ""
    if not t.offen:
        t.status = "live" if t.live else "zu"
    elif "checkpoint:human" in t.labels:
        t.status = "wartet" if t.blocker_offen else "david"
    elif DAVID.search(t.letzter_kommentar[:400]):
        t.status = "david"
    elif PROBLEM.search(t.letzter_kommentar[:400]):
        t.status = "haengt"
    elif FERTIG_GEBAUT.search(t.letzter_kommentar[:400]):
        t.status = "gebaut"
    elif t.blocker_offen:
        t.status = "wartet"
    else:
        t.status = "arbeit"


def kurz(text: str, n: int) -> str:
    text = re.sub(r"[*`_>#]", "", text.splitlines()[0] if text else "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def feature_name(titel: str, feature_praefix: str) -> str:
    teile = titel.split(":", 1)
    if (
        len(teile) == 2
        and len(teile[0]) <= 40
        and not teile[0].lower().startswith(("prefactoring", "abnahme"))
    ):
        titel = teile[1].strip()
    return kurz(titel, 70)


def zeit(iso: str | None) -> str:
    if not iso:
        return "–"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return t.strftime("%d.%m. %H:%M")


def balken(fertig: int, gesamt: int, breite: int = 12) -> str:
    voll = round(breite * fertig / gesamt) if gesamt else 0
    return "█" * voll + "░" * (breite - voll)


def engpass(s: Spec) -> tuple[Ticket, int] | None:
    """Offenes Ticket, auf das (direkt oder über andere) die meisten offenen Tickets warten."""
    offen = {t.nummer: t for t in s.tickets if t.offen}
    if not offen:
        return None

    def wartende(n: int) -> set[int]:
        gesehen: set[int] = set()
        stapel = [n]
        while stapel:
            k = stapel.pop()
            for t in offen.values():
                if k in t.blocker_offen and t.nummer not in gesehen:
                    gesehen.add(t.nummer)
                    stapel.append(t.nummer)
        return gesehen

    bester = max(offen.values(), key=lambda t: (len(wartende(t.nummer)), -t.nummer))
    anzahl = len(wartende(bester.nummer))
    return (bester, anzahl) if anzahl else None


def details(s: Spec, live_bekannt: bool) -> list[str]:
    zu = sum(not t.offen for t in s.tickets)
    gesamt = len(s.tickets)
    z = [f"## #{s.nummer} · {kurz(s.titel.split(':', 1)[-1], 70)}", ""]
    z.append(
        f"**{zu}/{gesamt} geschlossen** `{balken(zu, gesamt)}` {round(100 * zu / gesamt) if gesamt else 0} %"
    )
    zaehl = {k: sum(t.status == k for t in s.tickets) for k in EMOJI}
    z.append(" · ".join(f"{EMOJI[k]} {zaehl[k]} {TEXT[k]}" for k in EMOJI if zaehl[k]))
    z += ["", "| | Ticket | Feature | Stand |", "|---|---|---|---|"]
    reihenfolge = list(EMOJI)
    for t in sorted(s.tickets, key=lambda t: (reihenfolge.index(t.status), t.nummer)):
        if t.status in ("live", "zu"):
            info = f"zu {zeit(t.zu_am)}" + (f" · {t.commit[:7]}" if t.commit else "")
        elif t.status == "wartet":
            info = (
                "wartet auf "
                + ", ".join(f"#{b}" for b in t.blocker_offen[:4])
                + (" …" if len(t.blocker_offen) > 4 else "")
            )
        else:
            info = kurz(t.letzter_kommentar, 80) or "–"
        z.append(
            f"| {EMOJI[t.status]} | #{t.nummer} | {feature_name(t.titel, s.feature)} | {info} |"
        )

    hinweise: list[str] = []
    for t in s.tickets:
        if t.status == "haengt":
            hinweise.append(
                f"🟥 #{t.nummer} hängt ({zeit(t.kommentar_am)}): {kurz(t.letzter_kommentar, 110)}"
            )
    for t in s.tickets:
        if t.status == "david":
            hinweise.append(
                f"👤 #{t.nummer} wartet auf dich: {feature_name(t.titel, s.feature)}"
            )
    gebaut = [t for t in s.tickets if t.status == "gebaut"]
    if gebaut:
        hinweise.append(
            "📦 Fertig gebaut, noch nicht gemergt: "
            + ", ".join(f"#{t.nummer}" for t in gebaut)
        )
    e = engpass(s)
    if e:
        hinweise.append(
            f"🧱 Engpass: #{e[0].nummer} — {e[1]} Tickets warten darauf ({TEXT[e[0].status]})"
        )
    if not live_bekannt and zu:
        hinweise.append(
            "ℹ️ Live-Stand nicht prüfbar (kein Zugang zum VPS von hier) — ✅ heißt nur „geschlossen“."
        )
    elif zaehl["zu"]:
        hinweise.append(f"🟡 {zaehl['zu']} geschlossen, aber noch nicht auf dem VPS")
    juengst = max(s.tickets, key=lambda t: t.zuletzt or "", default=None)
    if juengst and juengst.zuletzt:
        hinweise.append(
            f"🕒 Letzte Bewegung: #{juengst.nummer} am {zeit(juengst.zuletzt)}"
        )
    if hinweise:
        z += ["", "**Hinweise**", *[f"- {h}" for h in hinweise]]
    return z


# ---------------------------------------------------------------------------
# Kurz-Modus (Standard seit 2026-11-18): ein Satz zur Spec dieser Session + Session-Check.
# ---------------------------------------------------------------------------

#: Ticket-Zustände, in denen keine Bau-Session laufen muss.
OHNE_SESSION_OK = ("david", "wartet")
#: Labels, mit denen der Aufpasser bewusst nie eine Session startet.
NIE_STARTEN = {"ready-for-human", "needs-info", "wontfix"}
BAU_SERVER = "bau-server"


def spec_erkennen() -> tuple[int | None, str]:
    """Spec dieser Session: Aufseher-Env → Bau-Env → Klon-Ordner ``duoplus-<S>`` → tmux ``spec-<S>``."""
    import os

    for var in ("TO_SPAWN_WACHE_SPEC", "TO_SPAWN_SPEC"):
        wert = os.environ.get(var, "").strip()
        if wert.isdigit():
            return int(wert), var
    m = re.search(r"duoplus-(\d+)$", REPO.name)
    if m:
        return int(m.group(1)), f"Ordner {REPO.name}"
    if os.environ.get("TMUX"):
        name = sh("tmux", "display-message", "-p", "#S", timeout=5)
        m = re.fullmatch(r"spec-(\d+)", name)
        if m:
            return int(m.group(1)), f"tmux {name}"
    return None, ""


def to_spawn_home() -> Path:
    """Ordner des installierten to-spawn-Skills (``TO_SPAWN_HOME`` überschreibt)."""
    import os

    return Path(
        os.environ.get("TO_SPAWN_HOME", Path.home() / ".claude/skills/to-spawn")
    )


def hindernis_zeile() -> str | None:
    """Offene Hindernisse der letzten 24 h als eine Zeile (#1433), sonst ``None``.

    Format, Fenster und „offen“ kennt nur ``to_spawn/hindernis.py`` (``kurzzeile``);
    fehlt das Modul oder scheitert es, zeigt ``/stand`` einfach nichts.
    """
    import importlib.util

    pfad = to_spawn_home() / "to_spawn" / "hindernis.py"
    if not pfad.exists():
        return None
    try:
        spec_mod = importlib.util.spec_from_file_location("to_spawn_hindernis", pfad)
        mod = importlib.util.module_from_spec(spec_mod)  # type: ignore[arg-type]
        spec_mod.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.kurzzeile()
    except Exception as e:  # noqa: BLE001 — Stand darf nie abbrechen, Grund wird gemeldet
        log.warning("Hindernis-Log nicht lesbar: %s", e)
        return None


def sessions_lokal(spec: int) -> dict[str, str] | None:
    """Zustand je Ticket/Aufseher aus ``sessions_stand.py`` (to-spawn-Skill), ``None`` wenn nicht ladbar."""
    import importlib.util
    import os

    pfad = to_spawn_home() / "skripte/sessions_stand.py"
    if not pfad.exists():
        return None
    os.environ.setdefault("TO_SPAWN_REPO", str(REPO))
    try:
        spec_mod = importlib.util.spec_from_file_location("sessions_stand", pfad)
        mod = importlib.util.module_from_spec(spec_mod)  # type: ignore[arg-type]
        sys.modules["sessions_stand"] = mod  # dataclasses braucht das Modul im Register
        spec_mod.loader.exec_module(mod)  # type: ignore[union-attr]
        eintraege = mod.manifeste_lesen(str(spec))
        mod.zuordnen(eintraege, mod.prozesse_lesen())
    except Exception as e:  # noqa: BLE001 — Stand darf nie abbrechen, Grund wird gemeldet
        log.warning("Session-Check lokal nicht möglich: %s", e)
        return None
    return {
        ("aufseher" if e.art == "spec" else n): e.zustand for n, e in eintraege.items()
    }


def sessions_server(spec: int) -> dict[str, str] | None:
    """Gleicher Check per ssh auf dem Bau-Server (Klon ``~/duoplus-<S>``, sonst Hauptrepo)."""
    befehl = (
        f"cd ~/duoplus-{spec} 2>/dev/null || cd ~/duoplus-management; "
        f"PYTHONIOENCODING=utf-8 python3 ~/.claude/skills/stand/stand.py {spec} --sessions-json"
    )
    roh = sh(
        "ssh",
        "-o",
        "ConnectTimeout=6",
        "-o",
        "BatchMode=yes",
        BAU_SERVER,
        befehl,
        timeout=40,
    )
    try:
        return json.loads(roh.splitlines()[-1]) if roh else None
    except (json.JSONDecodeError, IndexError):
        return None


def laeuft(zustand: str) -> bool:
    return zustand.startswith("läuft") and "VERWAIST" not in zustand


def satz(s: Spec, sessions: dict[str, str] | None) -> str:
    zu = sum(not t.offen for t in s.tickets)
    live = sum(t.status == "live" for t in s.tickets)
    teile = [
        f"**Spec #{s.nummer}: {zu} von {len(s.tickets)} Tickets fertig"
        + (f" ({live} live)" if live else "")
        + ".**"
    ]
    if sessions:
        bauen = [
            t.nummer
            for t in s.tickets
            if t.offen
            and t.status != "david"
            and laeuft(sessions.get(str(t.nummer), "aus"))
        ]
    else:
        bauen = [t.nummer for t in s.tickets if t.status in ("arbeit", "gebaut")]
    teile.append(
        "Bauen gerade: " + ", ".join(f"#{n}" for n in bauen) + "."
        if bauen
        else "Gerade baut keins."
    )
    david = [t.nummer for t in s.tickets if t.status == "david"]
    if david:
        teile.append("Wartet auf dich: " + ", ".join(f"#{n}" for n in david) + ".")
    return " ".join(teile)


def probleme(s: Spec, sessions: dict[str, str]) -> list[str]:
    """Was die Spec am Weiterlaufen hindert — eine Zeile je Fund, leer = alles sauber."""
    erg: list[str] = []
    offen = [t for t in s.tickets if t.offen]
    if offen and sessions.get("aufseher", "aus") == "aus":
        erg.append(f"Aufseher `wache {s.nummer}` läuft nicht")
    for t in s.tickets:
        z = sessions.get(str(t.nummer), "aus")
        if "VERWAIST" in z:
            erg.append(f"#{t.nummer}: Claude-Session {z} (bau.py weg)")
        elif (
            t.offen
            and z == "aus"
            and t.status not in OHNE_SESSION_OK
            and not (NIE_STARTEN & set(t.labels))
        ):
            erg.append(f"#{t.nummer}: frei, aber keine Session")
        elif not t.offen and z != "aus":
            erg.append(f"#{t.nummer}: Ticket zu, Session noch an ({z})")
        if t.status == "haengt":
            erg.append(
                f"#{t.nummer} hängt ({zeit(t.kommentar_am)}): {kurz(t.letzter_kommentar, 110)}"
            )
    return erg


def spec_laden(
    nr: int, titel: str | None = None, mit_folge: bool = True
) -> Spec | None:
    m = manifest(nr)
    if not m or not m.get("tickets"):
        return None
    if titel is None:
        roh = sh("gh", "issue", "view", str(nr), "--json", "title")
        titel = json.loads(roh)["title"] if roh else f"Spec #{nr}"
    s = Spec(nr, titel, str(m.get("feature", "")))
    nummern = [int(n) for n in m["tickets"]]
    if mit_folge:
        nummern += [n for n in folge_tickets(nr) if n not in nummern]
    holen = tickets_holen(nummern)
    commits = commits_je_ticket()
    vps = vps_head()
    live_menge = (
        set(sh("git", "rev-list", vps, "--since=90.days").splitlines())
        if vps
        else set()
    )
    s.tickets = [holen[n] for n in nummern if n in holen]
    for t in s.tickets:
        t.commit = commits.get(t.nummer)
        t.live = bool(vps and t.commit and t.commit in live_menge)
        status_setzen(t)
    return s


def kurz_modus(nr: int, quelle: str) -> int:
    s = spec_laden(nr)
    if s is None:
        sys.stdout.write(f"Spec #{nr} hat kein Ticket-Manifest auf origin/master.\n")
        return 0
    sessions, ort = sessions_lokal(nr), "hier"
    if (
        sessions is not None
        and not any(z != "aus" for z in sessions.values())
        and any(t.offen for t in s.tickets)
    ):
        fern = sessions_server(
            nr
        )  # nichts läuft hier → Spec baut vermutlich auf dem Bau-Server
        if fern and any(z != "aus" for z in fern.values()):
            sessions, ort = fern, "Bau-Server"
    aus = [satz(s, sessions), ""]
    hindernis = hindernis_zeile()
    if hindernis:
        aus += [hindernis, ""]
    if sessions is None:
        aus.append("Sessions: ❔ nicht prüfbar (to-spawn-Skill fehlt hier).")
    elif not any(t.offen for t in s.tickets):
        aus.append("Sessions: 🟢 Spec fertig, keine Session nötig.")
    else:
        funde = probleme(s, sessions)
        an = sum(laeuft(z) for z in sessions.values())
        if funde:
            aus.append(f"Sessions ({ort}): ⚠️ {len(funde)} Problem(e)")
            aus += [f"- {f}" for f in funde]
        else:
            aus.append(f"Sessions ({ort}): ✅ sauber, {an} laufen.")
    aus.append(f"<!-- spec={nr} quelle={quelle or 'Aufruf'} ort={ort} -->")
    sys.stdout.write("\n".join(aus) + "\n")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser(description="Ticket-Stand je Spec (für /stand)")
    ap.add_argument(
        "spec", nargs="?", type=int, help="Spec (sonst aus Session erkannt)"
    )
    ap.add_argument(
        "--alle",
        action="store_true",
        help="alte Übersicht: alle offenen Specs mit Tabellen",
    )
    ap.add_argument(
        "--details", action="store_true", help="alte Tabellen-Ansicht für diese Spec"
    )
    ap.add_argument("--sessions-json", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ohne-fetch", action="store_true")
    a = ap.parse_args()

    if a.sessions_json:  # interner Aufruf per ssh vom PC aus
        sys.stdout.write(json.dumps(sessions_lokal(a.spec) if a.spec else None) + "\n")
        return 0
    if not a.ohne_fetch:
        sh("git", "fetch", "-q", "origin", "master", timeout=90)
    if a.alle or a.details:
        return tabellen_modus(a.spec if a.details else None, a.alle)
    nr, quelle = (a.spec, "Aufruf") if a.spec else spec_erkennen()
    if nr:
        return kurz_modus(nr, quelle)
    # Keine Spec erkennbar (z. B. Hauptsession): je aktiver Spec ein Satz.
    zeilen = []
    for nr2, titel in offene_specs():
        s = spec_laden(nr2, titel, mit_folge=False)
        if (
            s
            and any(t.offen for t in s.tickets)
            and any(t.status not in ("wartet",) for t in s.tickets if t.offen)
        ):
            zeilen.append("- " + satz(s, None))
    sys.stdout.write(
        "Keine Spec in dieser Session erkannt — offene Specs:\n"
        + ("\n".join(zeilen) or "- keine")
        + "\n"
    )
    hindernis = hindernis_zeile()
    if hindernis:
        sys.stdout.write(hindernis + "\n")
    return 0


def tabellen_modus(spec_arg: int | None, alle_arg: bool) -> int:
    """Alte Ansicht (bis 2026-11-17 Standard): Übersicht + Feature-Tabellen."""

    class _A:
        spec = spec_arg
        alle = alle_arg

    a = _A()

    if a.spec:
        roh = sh("gh", "issue", "view", str(a.spec), "--json", "title")
        kandidaten = [(a.spec, json.loads(roh)["title"] if roh else f"Spec #{a.spec}")]
    else:
        kandidaten = offene_specs()

    specs: list[Spec] = []
    for nr, titel in kandidaten:
        m = manifest(nr)
        if not m or not m.get("tickets"):
            continue
        s = Spec(nr, titel, str(m.get("feature", "")))
        s.tickets = [
            Ticket(int(n), "", True, None, None, [], [], "", None) for n in m["tickets"]
        ]
        specs.append(s)
    if not specs:
        sys.stdout.write(
            "Keine offene Spec mit Ticket-Manifest auf origin/master gefunden.\n"
        )
        return 0

    zusatz = {s.nummer: folge_tickets(s.nummer) for s in specs if a.spec or a.alle}
    alle = sorted(
        {t.nummer for s in specs for t in s.tickets}
        | {n for l in zusatz.values() for n in l}
    )
    holen = tickets_holen(alle)
    commits = commits_je_ticket()
    vps = vps_head()
    live_menge = (
        set(sh("git", "rev-list", vps, "--since=90.days").splitlines())
        if vps
        else set()
    )

    jetzt = datetime.now(timezone.utc)
    for s in specs:
        nummern = [t.nummer for t in s.tickets] + [
            n
            for n in zusatz.get(s.nummer, [])
            if n not in {t.nummer for t in s.tickets}
        ]
        s.tickets = [holen[n] for n in nummern if n in holen]
        for t in s.tickets:
            t.commit = commits.get(t.nummer)
            t.live = bool(vps and t.commit and t.commit in live_menge)
            status_setzen(t)

    aus: list[str] = [
        f"# Ticket-Stand · {jetzt.astimezone().strftime('%d.%m. %H:%M')}"
        + (f" · VPS {vps[:7]}" if vps else ""),
        "",
    ]
    aus += [
        "| | Spec | Thema | geschlossen | 🟥 | 👤 | letzte Bewegung |",
        "|---|---|---|---|---|---|---|",
    ]

    def aktiv(s: Spec) -> bool:
        z = max((t.zuletzt or "" for t in s.tickets), default="")
        return (
            bool(z)
            and jetzt - datetime.fromisoformat(z.replace("Z", "+00:00"))
            <= AKTIV_FENSTER
        )

    for s in sorted(specs, key=lambda s: -s.nummer):
        zu = sum(not t.offen for t in s.tickets)
        g = len(s.tickets)
        fazit = (
            "🟢"
            if zu == g
            else (
                "🟥"
                if any(t.status == "haengt" for t in s.tickets)
                else ("🔨" if aktiv(s) else "💤")
            )
        )
        zuletzt = max((t.zuletzt or "" for t in s.tickets), default="") or None
        aus.append(
            f"| {fazit} | #{s.nummer} | {kurz(s.titel.split(':', 1)[-1], 55)} | {zu}/{g} `{balken(zu, g, 8)}` "
            f"| {sum(t.status == 'haengt' for t in s.tickets)} | {sum(t.status == 'david' for t in s.tickets)} | {zeit(zuletzt)} |"
        )
    aus.append("")
    aus.append(
        "🟢 alles zu · 🔨 bewegt sich (3 Tage) · 🟥 etwas hängt · 💤 keine Bewegung"
    )

    for s in sorted(specs, key=lambda s: -s.nummer):
        if a.spec or a.alle or (aktiv(s) and any(t.offen for t in s.tickets)):
            aus += ["", *details(s, bool(vps))]
    sys.stdout.write("\n".join(aus) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
