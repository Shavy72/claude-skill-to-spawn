"""Aufseher-Takt (#316): Prüf-Skript ohne KI, Claude nur bei Bedarf.

Ein Takt nimmt die Leitstand-Sperre ``takt-<S>`` (nicht blockierend — belegt ⇒
sofort aussteigen), lässt capo einen Tick laufen und sammelt, was zu entscheiden
ist: neue Bau-Log-Zeilen ``blockiert``/``entscheidung``/``frage``, neu
geschlossene Tickets der Spec (Belegseite prüfen), neue capo-Verstöße, Spec
fertig. Leer ⇒ kein Claude-Prozess. Sonst startet eine frische Claude-Session
(``-p``, endet selbst) mit Entscheidungsliste und Notizzettel
``waechter-<S>`` aus dem Leitstand. Der Merker liegt im Leitstand-Zustand unter
``takt.<S>`` und wird erst fortgeschrieben, wenn Claude mit 0 endet — und nur
für die Einträge, die Claude wirklich gezeigt bekam. Endet Claude mit Fehler oder
an der Zeitgrenze, kommt alles Gezeigte wieder; waren Briefkasten-Fragen dabei,
läuft nur ihr Zeige-Zähler der Bremse weiter (#339), und sie bleiben im Merker-Feld
``fragen`` (gezeigt), damit der nächste Takt den Kommentar an den Nutzer sucht (#346). Ohne Merker (erster Takt)
ist alles Ausgangsstand außer ``blockiert``/``frage`` offener Tickets.

Bremse (#315): eine offene Briefkasten-Frage, die immer wieder kommt, geht nach dem
:data:`BREMSE_AB`-ten Zeigen nur noch alle :data:`BREMSE_ABSTAND_S` an Claude, und der Takt
meldet sie einmal selbst per Ticket-Kommentar an den Nutzer (keine Mail).

Bewusste Grenze (#346): eine kaputte Briefkasten-Datei hat keine id, ihre Kennung ist der Hash des
Sperrgrunds. Kommt derselbe Sperrgrund später wieder (gleicher Text, z. B. dieselbe Störung der
Hauptdatei), findet die Kennung einen alten Kommentar an den Nutzer bzw. eine alte Bremse-Meldung im
Ticket und gilt als belegt bzw. gemeldet. Hingenommen, weil das Ticket gesperrt bleibt, bis der Nutzer
die Datei prüft (der Sperrgrund steht weiter in ``offene``), und eine eindeutige Kennung je Vorfall
erst der Briefkasten liefern müsste (Upgrade: id/Zeitstempel für Sperrgründe in ``briefkasten.offene``).

Auslöser: Cron alle 5 min (:func:`cron_einrichten`) und der Stop-Hook bei
``session_ende`` (:func:`starte_abgeloest`, höchstens ein Start je Minute und Spec).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import befugnis, befund, briefkasten, capo, config, gh, leitstand, starter, ticket_zustand, waechter_lauf

log = logging.getLogger("to_spawn.waechter_takt")

SKILL = Path(__file__).resolve().parent.parent
MODELL = "claude-opus-5-5"
RELEVANTE_TYPEN = frozenset({"blockiert", "entscheidung", "frage"})
CRON_TAKT = "*/5 * * * *"
#: Zeilen, die im ersten Takt (ohne Merker) trotzdem an Claude gehen — offene Fragen.
FRAGE_TYPEN = frozenset({"blockiert", "frage"})
#: Kontext sparen: je Takt höchstens so viele Einträge, der Rest kommt im nächsten Takt.
MAX_EINTRAEGE = 30
#: Bremse (#315): ab so vielen Zeigen kommt eine offene Briefkasten-Frage nur noch im großen Abstand.
BREMSE_AB = 3
BREMSE_ABSTAND_S = 30 * 60
MAX_NOTIZ = 2000
#: Ein hängender Claude darf die Sperre ``takt-<S>`` nicht ewig halten.
CLAUDE_TIMEOUT_S = 45 * 60
#: Stop feuert nach jeder Runde — der Hook startet je Spec höchstens einmal je Minute.
MIN_ABSTAND_S = 60.0
#: Bau-Session-Variablen: im Takt-Claude schriebe sein Stop-Hook sonst ins Ticket-Log.
TICKET_VARIABLEN = ("TO_SPAWN_TICKET", "TO_SPAWN_SPEC", "TO_SPAWN_LOG_REPO")
#: capo.tick bricht mit diesen Zeilen früh ab — dann gibt es keinen verwertbaren Stand.
CAPO_ABBRUCH = (
    "FEHLER: ein anderer capo-Lauf",
    "FEHLER: Sub-Issues von",
    "FEHLER: kein origin/",
)

#: (Text, Merker-Feld, Schlüssel) — Feld/Schlüssel sagen, was „gesehen“ bedeutet.
Eintrag = tuple[str, str, Any]

PROMPT = """Aufseher-Takt Spec #{S} ({REPO}). Einmaliger Lauf: kein Handoff, kein /loop, kein ScheduleWakeup — nach dieser Liste endest du. Ziel: Spec {S} vollständig, sauber, autonom fertig. {BEFUGNIS} Klasse 3 im besten Interesse des Menschen (Nordstern, Doktrinen). Nur Label checkpoint:human bleibt für den Menschen. Du baust nichts und sprichst keine Bau-Session an. Ticket neu zu → Belegseite unter docs/verify-hard/ per Grep prüfen (Akzeptanz erfüllt? Live-Klick-Weg-Beleg mit Rolle da?), bei Mangel 2-Zeilen-Kommentar „Aufseher: … fehlt“ + `gh issue reopen`. capo-Verstoß: capo hat schon geöffnet/kommentiert — nicht doppelt tun. Bau fertig, bereit zur Abnahme (alle Bau-Tickets zu oder nur noch Live-Belege/checkpoint:human-Abnahme offen, bzw. „Kette … durch“ oder „SPEC FERTIG“) → Abschluss-Paket `--stand abnahme` (einmal): Rundschau als Artifact: Skill `rundschau` (fehlt er: melden, kein Ersatz), Aufbau und Prüfung allein nach to_spawn/rundschau_deck.py — `python {SKILL}/skripte/rundschau_deck.py erzeugen --texte <json> --entscheidungen <json> --nach <html>` (Teil 1 Nutzer-Präsentation, Teil 2 gewohnter Rest, Schluss Entscheidungs-Zeitstrahl aus dem Bau-Verlauf; Zahl nennen), Screenshots per echtem Browser-Blick; `abschluss_paket.py ablegen` bekommt `--rundschau-datei <html>` und meldet Exit 4 bei Mangel, `python {SKILL}/skripte/belege_uebersicht.py {S}` und `python {SKILL}/skripte/test_uebersicht.py {S}` je als Artifact, Direkt-Links je Ticket in die Stage-App (staging.url aus .to-spawn/config.json + Route an die richtige Stelle, Rolle im Titel), Zugang nur als Namen (Basic-Auth-Nutzer, App-Rolle, Bitwarden-Eintragsname — nie Passwort), dann `python {SKILL}/skripte/abschluss_paket.py ablegen {S} --stand abnahme --stage <url> --rundschau <link> --belege <link> --tests <link> --direkt "<Titel (als Rolle)>=<url>"… --basic-auth-nutzer <name> --app-rolle "<Name (rolle)>" --bitwarden <eintrag>` (legt ab, mailt nicht), Abschlussbericht als Kommentar auf #{S} (max. 10 Zeilen). Danach Aufbau-Prüfung (einmal): `python {SKILL}/skripte/thermo_lauf.py plan {S}`. Exit 0 → je Eintrag in `teile` ein Subagent, alle parallel im selben Zug (`model: opus`, Prompt = Feld `prompt` unverändert); jede JSON-Antwort als `<befunde_ordner>/teil-<nr>.json` speichern, dann `python {SKILL}/skripte/thermo_lauf.py sammeln {S}` (schreibt docs/agents/thermo_{S}.md, mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). Exit 2/4 → überspringen. Exit 3 → fehlenden Teil nachstarten, erneut sammeln. Kein Umbau in der Spec — Befunde stehen als Code-Befunde im Marker docs/agents/thermo_{S}.md (dritter Teil der Abschluss-Mail), kein Issue. Danach Rückblick (einmal): `python {SKILL}/skripte/rueckblick.py planen {S}`. Exit 0 → ein Subagent (`model: opus`, Skill mp-retro) liest `.to-spawn/rueckblick/{S}/auftrag.md` und schreibt dort `ergebnis.json` wie im Auftrag beschrieben, dann `python {SKILL}/skripte/rueckblick.py sammeln {S}` (schreibt docs/agents/rueckblick_{S}.md, mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). Exit 4 → überspringen. Exit 3 → KI nachstarten, erneut sammeln. Exit 1 → Grund notieren, weiter. Zum Schluss `python {SKILL}/skripte/abschluss_paket.py nachsehen {S}` (genau eine Mail an den Menschen; nur für `--stand abnahme` gilt: Exit 3 = wartet noch, capo stößt es je Tick selbst an — nicht warten). Nach dem Live-Deploy dasselbe einmal mit `--stand live` (`abschluss_paket.py ablegen {S} --stand live …` + `abschluss_paket.py nachsehen {S} --stand live`); `nachsehen --stand live` bis Exit 0 wiederholen (capo stößt live nicht an). Kontext sparen: Tickets nie voll laden, Belegseiten per Grep/limit.

Zu entscheiden:
{LISTE}

Notizzettel aus dem letzten Takt:
{NOTIZ}

PFLICHT am Ende: Notizzettel fortschreiben (Stand + offene Punkte, knapp) mit `python {SKILL}/to_spawn.py takt {S} --notiz "<text>"`."""
# Befugnis aus einer Quelle (#278 P3b), einmal beim Laden: jeder ``PROMPT.format`` braucht nur noch
# S/REPO/SKILL/LISTE/NOTIZ; ``{SKILL}`` bleibt für ``format`` stehen.
PROMPT = PROMPT.replace("{BEFUGNIS}", befugnis.aufseher_text("python {SKILL}/to_spawn.py"))


# --- Prüfen ---------------------------------------------------------------------


def _jetzt_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _schluessel(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _zeilen(repo: Path, ref: str | None, ticket: int, wt_basis: str) -> list[dict[str, Any]]:
    """Bau-Log-Zeilen des Tickets: Hauptzweig + Laufdateien (Worktree, Repo)."""
    zeilen = capo.log_vom_ref(repo, ref, ticket) if ref else []
    for _quelle, pfad in capo.laufdateien(repo, capo.worktree_ordner(ticket, wt_basis), ticket):
        zeilen += capo.lauf_zeilen(pfad) or []
    return zeilen


def _briefkasten_offen(repo: Path, nummern: list[int]) -> list[tuple[Eintrag, bool]]:
    """Alle offenen Briefkasten-Fragen der Tickets als (Eintrag, kommt_wieder).

    Eintrag: Feld ``fragen``, Schlüssel ``<ticket>:<id>``; leere id (kaputte Datei) → Schlüssel aus dem
    Hash des Sperrgrunds. Nur Klasse 3 trägt den fertigen Antwort-Befehl und kommt wieder, bis sie
    beantwortet ist (sie wartet auf den Aufseher); alles andere (Klasse 2, kaputte Datei, unbekannte
    Klasse) wartet auf den Nutzer und kommt wieder, bis der Kommentar an ihn belegt ist (:func:`_belegte`).
    """
    befehl = f"python {SKILL.as_posix()}/to_spawn.py"
    eintraege: list[tuple[Eintrag, bool]] = []
    for f in briefkasten.offene(nummern, hauptbaum=repo):
        t, fid = f["ticket"], f["id"]
        frage, vorschlag = capo._kurz(f["frage"], 60), capo._kurz(f["vorschlag"], 40)
        if not fid:
            text = (
                f"#{t} Briefkasten gesperrt (kaputte Datei) — wartet auf den Nutzer: {befugnis.KLASSE_2_WEITERGABE}, "
                f"prüft/löscht nur der Nutzer: {frage}"
            )
        elif f["klasse"] == "3":
            text = (
                f"#{t} Briefkasten-Frage {fid} (Klasse 3, du entscheidest): {frage} · Vorschlag: {vorschlag} → "
                f"`{befehl} antworten {t} --id {fid} --wahl … --grund … --beleg …`"
            )
        else:
            text = (
                f"#{t} Briefkasten-Frage {fid} (Klasse 2) — wartet auf den Nutzer: {befugnis.KLASSE_2_WEITERGABE}: "
                f"{frage} · Vorschlag: {vorschlag}"
            )
        kennung = fid or _schluessel(f["frage"])
        kommt_wieder = bool(fid) and f["klasse"] == "3"
        if not kommt_wieder:
            text += f" · Kommentar an den Nutzer mit `{kennung}` (sonst kommt die Frage wieder)"
        eintraege.append(((text, "fragen", f"{t}:{kennung}"), kommt_wieder))
    return eintraege


def _gebremst(stand: Any, jetzt: datetime) -> bool:
    """Zeige-Zähler ``{"n", "zuletzt"}`` einer Frage: ab :data:`BREMSE_AB` Zeigen erst nach dem Abstand wieder.

    Unlesbarer Stand bremst nie (sichere Richtung: lieber zeigen als verschlucken).
    """
    if not isinstance(stand, dict) or not isinstance(stand.get("n"), int) or stand["n"] < BREMSE_AB:
        return False
    try:
        zuletzt = datetime.fromisoformat(str(stand.get("zuletzt")))
        return (jetzt - zuletzt).total_seconds() < BREMSE_ABSTAND_S
    except (TypeError, ValueError):  # TypeError: Zeit ohne Zeitzone
        return False


def _zu_zeigen(
    offen: list[tuple[Eintrag, bool]], erledigt: set[str], gezeigt: dict[str, Any] | None = None
) -> list[Eintrag]:
    jetzt = datetime.now().astimezone()
    gezeigt = gezeigt or {}
    return [
        e
        for e, kommt_wieder in offen
        if (kommt_wieder or e[2] not in erledigt) and not _gebremst(gezeigt.get(e[2]), jetzt)
    ]


def briefkasten_eintraege(repo: Path, nummern: list[int], erledigt: set[str]) -> list[Eintrag]:
    """Offene Briefkasten-Fragen für den Takt: Klasse 3, bis beantwortet; Klasse 2/kaputt, bis sie in
    ``erledigt`` stehen (Merker-Feld ``belegt``: Kommentar an den Nutzer belegt)."""
    return _zu_zeigen(_briefkasten_offen(repo, nummern), erledigt)


def _belegte(gh_repo: str, offen: list[tuple[Eintrag, bool]], gesehen: set[str], vorher: set[str]) -> set[str]:
    """Schlüssel der offenen Klasse-2/kaputt-Fragen, deren Kommentar an den Nutzer im Ticket steht (#315).

    Beleg = Kommentar eines Repo-Beteiligten mit :data:`befugnis.KLASSE_2_KOMMENTAR` und der Kennung
    (id bzw. Hash). GitHub wird nur für schon Gezeigtes, noch nicht Belegtes gefragt; stumm ⇒ nicht belegt
    (die Frage kommt wieder).
    """
    belegt: set[str] = set()
    texte: dict[str, list[str]] = {}
    for (_text, _feld, k), kommt_wieder in offen:
        if kommt_wieder or k not in gesehen:
            continue
        if k in vorher:
            belegt.add(k)
            continue
        ticket, _, kennung = k.partition(":")
        if ticket not in texte:
            texte[ticket] = [str(c.get("body") or "") for c in capo.issue_kommentare(gh_repo, int(ticket)) or []]
        if any(befugnis.KLASSE_2_KOMMENTAR in t and kennung in t for t in texte[ticket]):
            belegt.add(k)
    return belegt


def pruefe(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    merker: dict[str, Any] | None,
    *,
    dry_run: bool,
) -> tuple[list[Eintrag], dict[str, Any]]:
    """(Einträge, neuer Merker). ``merker is None`` = erster Takt.

    Wirft ``RuntimeError``, wenn capo früh abbricht, GitHub stumm ist oder der Hauptzweig fehlt.
    """
    wt_basis = str(konfig.get("wt_basis") or "")
    tick = capo.tick(repo, spec, gh_repo, konfig, wt_basis=wt_basis, dry_run=dry_run, katalog=True)
    # exit_code allein taugt nicht: auch fetch/Mail/Katalog-Fehler setzen ihn,
    # ohne dass der Tick abbricht. Früher Abbruch = eine der Abbruch-Zeilen.
    abbruch = [z for z in tick.zeilen if z.startswith(CAPO_ABBRUCH)]
    if tick.exit_code != 0 and abbruch:
        raise RuntimeError(f"capo-Tick abgebrochen — {abbruch[0]}")
    liste = capo.kinder(gh_repo, spec)
    if liste is None:
        raise RuntimeError(f"Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh)")
    ref = capo.haupt_ref(repo)
    if ref is None:
        raise RuntimeError("kein origin/master bzw. origin/main — git fetch prüfen")
    erster = merker is None
    alt = merker or {}
    gesehen = set(alt.get("zeilen") or [])
    alt_zu = set(alt.get("zu") or [])
    alt_verstoesse = set(alt.get("verstoesse") or [])
    eintraege: list[Eintrag] = []
    zeilen: set[str] = set()
    zu: set[int] = set()
    for issue in sorted(liste, key=lambda i: int(i["number"])):
        nr = int(issue["number"])
        offen = not ticket_zustand.fertig_gebaut(issue)  # fertig gebaut hat keine offene Frage
        for z in _zeilen(repo, ref, nr, wt_basis):
            if z.get("typ") not in RELEVANTE_TYPEN:
                continue
            k = _schluessel(json.dumps(z, sort_keys=True, ensure_ascii=False))
            if k in zeilen:
                continue
            zeilen.add(k)
            if k in gesehen:
                continue
            # Erster Takt: nur offene Fragen gehen an Claude, der Rest ist Ausgangsstand.
            if erster and not (offen and z.get("typ") in FRAGE_TYPEN):
                continue
            eintraege.append((capo.verdichte(nr, z), "zeilen", k))
        # Belegseite gilt dem echten Schluss; verworfen hat keine (#279).
        if ticket_zustand.stufe(issue) == "zu":
            zu.add(nr)
            if not erster and nr not in alt_zu:
                titel = capo._kurz(issue.get("title"), 80)
                text = f"#{nr} neu geschlossen — Belegseite prüfen: {titel}"
                eintraege.append((text, "zu", nr))
    # Briefkasten-Fragen auch im ersten Takt: eine offene Frage wartet immer auf Antwort (#278 P3b).
    offen_fragen = _briefkasten_offen(repo, [int(i["number"]) for i in liste])
    offen_schluessel = {e[2] for e, _wieder in offen_fragen}
    belegt = _belegte(gh_repo, offen_fragen, set(alt.get("fragen") or []), set(alt.get("belegt") or []))
    gezeigt_alt = alt.get("gezeigt") if isinstance(alt.get("gezeigt"), dict) else {}
    # Beantwortete fallen raus; kaputter Zähler (kein dict, ``n`` keine Zahl) zählt wie „fehlt“ (#339).
    gezeigt = {
        k: v
        for k, v in gezeigt_alt.items()
        if k in offen_schluessel and isinstance(v, dict) and type(v.get("n")) is int
    }
    eintraege += _zu_zeigen(offen_fragen, belegt, gezeigt)
    verstoesse: set[str] = set()
    for v in tick.verstoesse:
        k = f"{v.ticket}:{v.regel}"
        verstoesse.add(k)
        if not erster and k not in alt_verstoesse:
            text = f"#{v.ticket} capo-Verstoß {v.regel}: {capo._kurz(v.text)}"
            eintraege.append((text, "verstoesse", k))
    if tick.fertig and not erster and not alt.get("fertig"):
        eintraege.append((f"#{spec} SPEC FERTIG — Abschluss-Kette (Thermo → Rückblick → Mail)", "fertig", True))
    neu = {
        "zeilen": sorted(zeilen),
        "zu": sorted(zu),
        "verstoesse": sorted(verstoesse),
        "fragen": sorted(offen_schluessel),  # nur noch offene; beantwortete fallen raus
        "belegt": sorted(belegt),
        "gezeigt": gezeigt,
        "fertig": bool(tick.fertig),
        "zuletzt": _jetzt_iso(),
    }
    return eintraege, neu


def ohne_ungezeigte(neu: dict[str, Any], ungezeigt: list[Eintrag]) -> dict[str, Any]:
    """Merker ohne die Einträge, die Claude nicht gezeigt bekam (Kappung) — sie kommen wieder."""
    for _text, feld, k in ungezeigt:
        if feld == "fertig":
            neu["fertig"] = False
        else:
            neu[feld] = [x for x in neu[feld] if x != k]
    return neu


def zaehle_gezeigte(neu: dict[str, Any], gezeigt: list[Eintrag]) -> None:
    """Zeige-Zähler der Briefkasten-Fragen, die Claude wirklich bekam, um eins weiter (Bremse, #315)."""
    jetzt = _jetzt_iso()
    for _text, feld, k in gezeigt:
        if feld == "fragen":
            stand = neu["gezeigt"].setdefault(k, {"n": 0, "gemeldet": False})
            stand["n"] += 1
            stand["zuletzt"] = jetzt


def _bremse_kopf(kennung: str) -> str:
    """Anfang der Bremse-Meldung — woran :func:`melde_gebremste` eine schon gepostete Meldung erkennt."""
    return f"{befugnis.KLASSE_2_KOMMENTAR}: Briefkasten-Frage `{kennung}` ist seit"


def bremse_text(schluessel: str, n: int) -> str:
    """Kommentar an den Nutzer, wenn die Bremse greift — trägt den Beleg-Anfang und die Kennung."""
    ticket, _, kennung = schluessel.partition(":")
    befehl = f"python {SKILL.as_posix()}/to_spawn.py"
    return (
        f"{_bremse_kopf(kennung)} {n} Takten offen, der Aufseher "
        f"hat nicht entschieden — der Takt zeigt sie ab jetzt nur noch alle {BREMSE_ABSTAND_S // 60} min.\n"
        f"Ansehen: `{befehl} offene {ticket}` · Antwort nur im eigenen Terminal: "
        f"`{befehl} nutzer-antwort {ticket} --id <id> --wahl <Wahl> --grund <Grund>`"
    )


def melde_gebremste(gh_repo: str, neu: dict[str, Any]) -> None:
    """Jede gebremste Frage einmal per Ticket-Kommentar an den Nutzer; gescheitert ⇒ nächster Takt erneut.

    Vor dem Kommentar zählt das Ticket selbst: steht die Meldung schon dort (Merker nach dem Kommentar
    verloren), gilt sie als gemeldet (#339). Kommentare nicht lesbar ⇒ nächster Takt erneut.
    """
    for k, stand in neu["gezeigt"].items():
        if stand["n"] < BREMSE_AB or stand.get("gemeldet"):
            continue
        ticket, _, kennung = k.partition(":")
        kommentare = capo.issue_kommentare(gh_repo, int(ticket))
        if kommentare is None:
            continue
        if any(_bremse_kopf(kennung) in str(c.get("body") or "") for c in kommentare):
            stand["gemeldet"] = True
        else:
            stand["gemeldet"] = befund.kommentiere(ticket, gh_repo, bremse_text(k, stand["n"]))


def baue_prompt(spec: int, repo: Path, eintraege: list[str], notiz: str | None) -> str:
    rest = len(eintraege) - MAX_EINTRAEGE
    liste = [f"- {capo._kurz(e, 200)}" for e in eintraege[:MAX_EINTRAEGE]]
    if rest > 0:
        liste.append(f"- … und {rest} weitere (nächster Takt)")
    notiz_text = (notiz or "(leer — erster Takt)")[:MAX_NOTIZ]
    return PROMPT.format(
        S=spec,
        REPO=repo,
        SKILL=SKILL.as_posix(),
        LISTE="\n".join(liste),
        NOTIZ=notiz_text,
    )


# --- Claude ---------------------------------------------------------------------


def claude_start(claude: str) -> list[str]:
    """Startbefehl ohne Shell: ``.py`` über Python; Windows-``.cmd``-Shim → node + cli.js bzw. claude.exe.

    Nur wenn nichts anderes da ist, bleibt der Shim — Windows startet ihn dann über
    ``cmd /c``; der Prompt kommt aber über stdin, als Argumente stehen nur feste Flags.
    """
    if claude.endswith(".py"):
        return [sys.executable, claude]
    pfad = shutil.which(claude) or claude
    if sys.platform == "win32" and pfad.lower().endswith((".cmd", ".bat")):
        ordner = Path(pfad).parent
        cli = ordner / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = shutil.which("node")
        if cli.is_file() and node:
            return [node, str(cli)]
        exe = ordner / "claude.exe"
        if exe.is_file():
            return [str(exe)]
        exe_im_pfad = shutil.which("claude.exe")
        if exe_im_pfad:
            return [exe_im_pfad]
    return [pfad]


def claude_befehl(claude: str, konfig: dict[str, Any], spec: int) -> list[str]:
    """``waechter_lauf.befehl`` ohne Prompt-Argument, dafür ``-p`` (Prompt kommt über stdin)."""
    modelle = konfig.get("modelle") if isinstance(konfig.get("modelle"), dict) else {}
    modell = str(modelle.get("waechter") or MODELL)
    ausweich = str(modelle.get("waechter_ausweich") or "")
    cmd = waechter_lauf.befehl(claude, modell, ausweich, False, spec, "", effort=waechter_lauf.effort(konfig))
    return [*claude_start(claude), *cmd[1:-1], "-p"]


def claude_pfad() -> str:
    return os.environ.get("TO_SPAWN_CLAUDE") or shutil.which("claude") or "claude"


def _kind_env() -> dict[str, str]:
    """Umgebung für Takt/Claude ohne Bau-Session-Variablen (sonst Stop-Hook ins Ticket-Log)."""
    env = {k: v for k, v in os.environ.items() if k not in TICKET_VARIABLEN}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _timeout_s() -> float:
    try:
        return float(os.environ.get("TO_SPAWN_TAKT_TIMEOUT_S") or CLAUDE_TIMEOUT_S)
    except ValueError:
        return float(CLAUDE_TIMEOUT_S)


def starte_claude(cmd: list[str], repo: Path, prompt: str) -> int:
    """Claude ohne Shell starten, Prompt über stdin; wirft ``OSError``/``TimeoutExpired``."""
    proc = starter.starte(  # Liste, kein Shell, Prompt nur über stdin
        cmd, cwd=repo, notnagel=None, stdin=subprocess.PIPE, env=_kind_env()
    )
    try:
        proc.communicate(prompt.encode("utf-8"), timeout=_timeout_s())
    except subprocess.TimeoutExpired:
        waechter_lauf._beende(proc)
        raise
    return proc.returncode


# --- Takt -----------------------------------------------------------------------


def _fehler(spec: int, text: str) -> int:
    """Eine Quelle für Log und Ausgabe; der Merker bleibt unverändert."""
    meldung = f"Takt #{spec}: {text}"
    log.error("%s", meldung)
    print(f"FEHLER: {meldung}")
    return 1


def takt(
    repo: Path,
    spec: int,
    gh_repo: str,
    *,
    dry_run: bool = False,
    claude: str | None = None,
    grund: str = "",
) -> int:
    halter = f"takt pid {os.getpid()}" + (f" ({grund})" if grund else "")
    try:
        schein = leitstand.versuche(f"takt-{spec}", halter)
    except OSError as fehler:
        return _fehler(spec, f"Leitstand-Sperre takt-{spec} nicht prüfbar: {fehler}")
    if schein is None:
        print(f"Takt #{spec} läuft schon — steige aus.")
        return 0
    with schein:
        konfig = config.lade(repo)
        try:
            merker = leitstand.lese_zustand().get("takt", {}).get(str(spec))
            eintraege, neu = pruefe(repo, spec, gh_repo, konfig, merker, dry_run=dry_run)
        except (RuntimeError, OSError) as fehler:
            return _fehler(spec, str(fehler))
        if dry_run:
            print(f"Takt #{spec} (Probelauf): {len(eintraege)} zu entscheiden")
            for text, _feld, _k in eintraege:
                print(f"- {text}")
            return 0
        gescheitert = ""
        if eintraege:
            notiz_name = f"waechter-{spec}"
            notiz_vorher = leitstand.notiz(notiz_name)
            texte = [text for text, _feld, _k in eintraege]
            prompt = baue_prompt(spec, repo, texte, notiz_vorher)
            cmd = claude_befehl(claude or claude_pfad(), konfig, spec)
            print(f"Takt #{spec}: {len(eintraege)} zu entscheiden — starte Claude.")
            sys.stdout.flush()
            try:
                code = starte_claude(cmd, repo, prompt)
            except OSError as fehler:
                return _fehler(spec, f"Claude nicht startbar ({cmd[0]}): {fehler}")
            except subprocess.TimeoutExpired:
                gescheitert = f"Claude über Zeitlimit {_timeout_s():.0f} s — beendet"
            else:
                gescheitert = f"Claude Exit {code}" if code != 0 else ""
            if gescheitert:
                if not any(feld == "fragen" for _t, feld, _k in eintraege[:MAX_EINTRAEGE]):
                    return _fehler(spec, f"{gescheitert} — Merker bleibt.")
                # Nichts gilt als erledigt, aber gezeigt ist gezeigt: die Bremse zählt weiter (#339), und
                # gezeigte Fragen bleiben in ``fragen``, damit der nächste Takt ihren Beleg sucht (#346).
                neu = ohne_ungezeigte(
                    neu, [e for i, e in enumerate(eintraege) if e[1] != "fragen" or i >= MAX_EINTRAEGE]
                )
            else:
                if leitstand.notiz(notiz_name) == notiz_vorher:
                    meldung = f"Takt #{spec}: Claude endete mit 0, Notizzettel nicht fortgeschrieben."
                    log.warning("%s", meldung)
                    print(f"WARNUNG: {meldung}")
                neu = ohne_ungezeigte(neu, eintraege[MAX_EINTRAEGE:])
            zaehle_gezeigte(neu, eintraege[:MAX_EINTRAEGE])
        else:
            print(f"Takt #{spec}: nichts zu entscheiden")
        melde_gebremste(gh_repo, neu)

        def merke(zustand: dict[str, Any]) -> None:
            zustand.setdefault("takt", {})[str(spec)] = neu

        leitstand.aendere_zustand(merke)
        if gescheitert:
            return _fehler(spec, f"{gescheitert} — Gezeigtes kommt wieder, nur die Bremse zählt.")
    return 0


# --- Auslöser -------------------------------------------------------------------


def log_datei(spec: int) -> Path:
    ordner = leitstand.leitstand_ordner() / "takt"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{spec}.log"


def abgeloest_optionen() -> dict[str, Any]:
    """Popen-Optionen für den abgelösten Takt-Lauf.

    Windows: CREATE_NO_WINDOW statt DETACHED_PROCESS. Ohne Konsole bekäme
    jeder Kind-Aufruf (git, gh, ssh) eine eigene neue Konsole, und Windows
    Terminal reißt dafür je ein Fenster nach vorn (~1/s, Befund 27.09.2026).
    Mit unsichtbarer eigener Konsole erben alle Kinder sie — kein Fenster.
    Abgelöst bleibt der Lauf trotzdem: eigene Konsole + eigene Prozessgruppe.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def starte_abgeloest(spec: int, repo: Path, grund: str) -> bool:
    """Takt als eigenen, abgelösten Prozess starten (Stop-Hook); Ausgabe ins Leitstand-Log.

    Stop feuert nach jeder Runde, nicht nur am Session-Ende (der Hook kann das nicht
    unterscheiden) — daher höchstens ein Start je ``MIN_ABSTAND_S`` und Spec
    (Zeitpunkt im Leitstand-Zustand ``takt_ausloeser``). ``True`` = gestartet.
    """
    jetzt = time.time()
    frei: list[bool] = []

    def pruefe_abstand(zustand: dict[str, Any]) -> None:
        letzte = zustand.setdefault("takt_ausloeser", {})
        frei.append(jetzt - float(letzte.get(str(spec)) or 0) >= MIN_ABSTAND_S)
        if frei[0]:
            letzte[str(spec)] = jetzt

    leitstand.aendere_zustand(pruefe_abstand)
    if not frei[0]:
        log.info(
            "Takt #%s: letzter Start < %.0f s — %s ausgelassen.",
            spec,
            MIN_ABSTAND_S,
            grund,
        )
        return False
    cmd = [
        sys.executable,
        str(SKILL / "to_spawn.py"),
        "takt",
        str(spec),
        "--repo-dir",
        str(repo),
        "--grund",
        grund,
    ]
    optionen = abgeloest_optionen()
    with log_datei(spec).open("a", encoding="utf-8") as ausgabe:
        ausgabe.write(f"--- {_jetzt_iso()} Takt #{spec} ausgelöst: {grund}\n")
        ausgabe.flush()
        starter.starte(  # fester Befehl, kein Shell
            cmd,
            cwd=SKILL,
            notnagel=None,
            stdin=subprocess.DEVNULL,
            stdout=ausgabe,
            stderr=subprocess.STDOUT,
            env=_kind_env(),
            **optionen,
        )
    return True


def cron_marke(spec: int) -> str:
    return f"# to-spawn-takt #{spec}"


def cron_zeilen(zeilen: list[str], spec: int, befehl: str) -> list[str]:
    """Crontab mit genau einer Takt-Zeile für ``spec`` (an der alten Stelle, sonst am Ende)."""
    marke = cron_marke(spec)
    zeile = f"{befehl}  {marke}"
    stellen = [i for i, z in enumerate(zeilen) if z.rstrip().endswith(marke)]
    neu = [z for z in zeilen if not z.rstrip().endswith(marke)]
    neu.insert(stellen[0] if stellen else len(neu), zeile)
    return neu


def cron_befehl(spec: int, repo: Path, claude: str, log: Path) -> str:
    """Cron-Befehl: Cron hat ein knappes PATH — Claude absolut, alle Pfade gequotet."""
    q = shlex.quote
    return (
        f"{CRON_TAKT} TO_SPAWN_CLAUDE={q(claude)} {q(sys.executable)} "
        f"{q(str(SKILL / 'to_spawn.py'))} takt {spec} --repo-dir {q(str(repo))} "
        f"--grund cron >> {q(str(log))} 2>&1"
    )


def cron_einrichten(spec: int, repo: Path, *, trocken: bool = False) -> str:
    """Takt alle 5 min als Cron-Zeile (idempotent, alte crontab gesichert). Nur Linux."""
    from . import aufpasser

    claude = os.environ.get("TO_SPAWN_CLAUDE") or shutil.which("claude")
    if not claude:
        raise RuntimeError("claude nicht im PATH — TO_SPAWN_CLAUDE setzen, Cron nicht eingerichtet")
    befehl = cron_befehl(spec, repo, os.path.abspath(claude), log_datei(spec))
    alt = aufpasser._crontab("-l")
    zeilen = alt.stdout.splitlines() if alt.returncode == 0 else []
    neu = cron_zeilen(zeilen, spec, befehl)
    zeile = neu[[i for i, z in enumerate(neu) if z.rstrip().endswith(cron_marke(spec))][0]]
    if neu == zeilen:
        return f"Cron unverändert: {zeile}"
    if trocken:
        return f"[trocken] Cron würde eingerichtet: {zeile}"
    stempel = time.strftime("%Y%m%d-%H%M%S")
    (log_datei(spec).parent / f"crontab.vorher.{stempel}").write_text(alt.stdout, encoding="utf-8")
    ergebnis = aufpasser._crontab("-", eingabe="\n".join(neu) + "\n")
    if ergebnis.returncode != 0:
        raise RuntimeError(f"crontab schreiben: {ergebnis.stderr.strip()[:300]}")
    return f"Cron eingerichtet: {zeile}"


# --- CLI ------------------------------------------------------------------------


def _repo(args: argparse.Namespace) -> Path:
    if args.repo_dir:
        return Path(args.repo_dir).resolve()
    if os.environ.get("TO_SPAWN_REPO"):
        return Path(os.environ["TO_SPAWN_REPO"]).resolve()
    return config.repo_wurzel()


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser("takt", help="Aufseher-Takt: prüfen, Claude nur bei Bedarf (#316)")
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="nur Liste zeigen: kein Claude, kein Merker",
    )
    p.add_argument(
        "--repo-dir",
        default="",
        help="Git-Repo (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)",
    )
    p.add_argument("--gh-repo", default="", help="owner/name (Vorgabe: aus origin)")
    p.add_argument("--grund", default="", help="Auslöser (cron, session_ende …) für Halter/Log")
    p.add_argument(
        "--claude",
        default=None,
        help="claude-Befehl (Vorgabe: TO_SPAWN_CLAUDE bzw. PATH)",
    )
    p.add_argument("--notiz", default=None, help="nur Notizzettel waechter-<S> setzen und enden")

    p_e = unter.add_parser("takt-einrichten", help="Aufseher-Takt alle 5 min als Cron (nur Linux)")
    p_e.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p_e.add_argument(
        "--repo-dir",
        default="",
        help="Git-Repo (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)",
    )
    p_e.add_argument("--trocken", action="store_true", help="nur zeigen, nichts schreiben")


def lauf(args: argparse.Namespace) -> int:
    if args.befehl == "takt-einrichten":
        if sys.platform == "win32":
            print("takt-einrichten gibt es nur auf dem Linux-Bau-Server (Cron) — hier nichts eingerichtet.")
            return 2
        try:
            print(cron_einrichten(args.spec, _repo(args), trocken=args.trocken))
        except RuntimeError as fehler:
            print(f"FEHLER: {fehler}")
            return 1
        return 0
    if args.notiz is not None:
        leitstand.setze_notiz(f"waechter-{args.spec}", args.notiz)
        print(f"Notizzettel waechter-{args.spec} gesetzt.")
        return 0
    repo = _repo(args)
    gh_repo = args.gh_repo or gh.repo_aus_origin(repo, fallback="")
    if not gh_repo:
        print("FEHLER: GitHub-Repo unbekannt — --gh-repo owner/name angeben.")
        return 2
    return takt(
        repo,
        args.spec,
        gh_repo,
        dry_run=args.dry_run,
        claude=args.claude,
        grund=args.grund,
    )
