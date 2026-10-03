"""Aufseher-Takt (#316): Prüf-Skript ohne KI, Claude nur bei Bedarf.

Ein Takt nimmt die Leitstand-Sperre ``takt-<S>`` (nicht blockierend — belegt ⇒
sofort aussteigen), lässt capo einen Tick laufen und sammelt, was zu entscheiden
ist: neue Bau-Log-Zeilen ``blockiert``/``entscheidung``/``frage``, neu
geschlossene Tickets der Spec (Belegseite prüfen), neue capo-Verstöße, Spec
fertig. Leer ⇒ kein Claude-Prozess. Sonst startet eine frische Claude-Session
(``-p``, endet selbst) mit Entscheidungsliste und Notizzettel
``waechter-<S>`` aus dem Leitstand. Der Merker liegt im Leitstand-Zustand unter
``takt.<S>`` und wird erst fortgeschrieben, wenn Claude mit 0 endet — und nur
für die Einträge, die Claude wirklich gezeigt bekam. Ohne Merker (erster Takt)
ist alles Ausgangsstand außer ``blockiert``/``frage`` offener Tickets.

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

from . import capo, config, gh, leitstand, waechter_lauf

log = logging.getLogger("to_spawn.waechter_takt")

SKILL = Path(__file__).resolve().parent.parent
MODELL = "claude-opus-5-5"
RELEVANTE_TYPEN = frozenset({"blockiert", "entscheidung", "frage"})
CRON_TAKT = "*/5 * * * *"
#: Zeilen, die im ersten Takt (ohne Merker) trotzdem an Claude gehen — offene Fragen.
FRAGE_TYPEN = frozenset({"blockiert", "frage"})
#: Kontext sparen: je Takt höchstens so viele Einträge, der Rest kommt im nächsten Takt.
MAX_EINTRAEGE = 30
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

PROMPT = """Aufseher-Takt Spec #{S} ({REPO}). Einmaliger Lauf: kein Handoff, kein /loop, kein ScheduleWakeup — nach dieser Liste endest du. Ziel: Spec {S} vollständig, sauber, autonom fertig. Jede Rückfrage (Bau-Log blockiert/entscheidung/frage) entscheidest DU selbst im besten Interesse von David (Nordstern, Doktrinen) und antwortest per Issue-Kommentar (max. 2 Zeilen) + `python {SKILL}/to_spawn.py eintrag --ticket <N> --typ entscheidung --frage … --wahl … --grund …`. Nur Label checkpoint:human bleibt für David. Du baust nichts und sprichst keine Bau-Session an. Ticket neu zu → Belegseite unter docs/verify-hard/ per Grep prüfen (Akzeptanz erfüllt? Live-Klick-Weg-Beleg mit Rolle da?), bei Mangel 2-Zeilen-Kommentar „Aufseher: … fehlt“ + `gh issue reopen`. capo-Verstoß: capo hat schon geöffnet/kommentiert — nicht doppelt tun. Bau fertig, bereit zur Abnahme (alle Bau-Tickets zu oder nur noch Live-Belege/checkpoint:human-Abnahme offen, bzw. „Kette … durch“ oder „SPEC FERTIG“) → Abschluss-Paket `--stand abnahme` (einmal): Rundschau als Artifact (Skill rundschau), `python {SKILL}/skripte/belege_uebersicht.py {S}` und `python {SKILL}/skripte/test_uebersicht.py {S}` je als Artifact, Direkt-Links je Ticket in die Stage-App (staging.url aus .to-spawn/config.json + Route an die richtige Stelle, Rolle im Titel), Zugang nur als Namen (Basic-Auth-Nutzer, App-Rolle, Bitwarden-Eintragsname — nie Passwort), dann `python {SKILL}/skripte/abschluss_paket.py {S} --stand abnahme --stage <url> --rundschau <link> --belege <link> --tests <link> --direkt "<Titel (als Rolle)>=<url>"… --basic-auth-nutzer <name> --app-rolle "<Name (rolle)>" --bitwarden <eintrag>` (schreibt docs/agents/abschluss_{S}.md + mailt David), Abschlussbericht als Kommentar auf #{S} (max. 10 Zeilen). Danach Aufbau-Prüfung (einmal): `python {SKILL}/skripte/thermo_lauf.py plan {S}`. Exit 0 → je Eintrag in `teile` ein Subagent, alle parallel im selben Zug (`model: opus`, Prompt = Feld `prompt` unverändert); jede JSON-Antwort als `<befunde_ordner>/teil-<nr>.json` speichern, dann `python {SKILL}/skripte/thermo_lauf.py sammeln {S}` (schreibt docs/agents/thermo_{S}.md, mit Pathspec + [skip ci] committen + pushen) und die Issue-URL in den Abschlussbericht. Exit 2/4 → überspringen. Exit 3 → fehlenden Teil nachstarten, erneut sammeln. Kein Umbau in der Spec — Befunde gehen nur ins Sammel-Issue. Nach dem Live-Deploy dasselbe einmal mit `--stand live`. Kontext sparen: Tickets nie voll laden, Belegseiten per Grep/limit.

Zu entscheiden:
{LISTE}

Notizzettel aus dem letzten Takt:
{NOTIZ}

PFLICHT am Ende: Notizzettel fortschreiben (Stand + offene Punkte, knapp) mit `python {SKILL}/to_spawn.py takt {S} --notiz "<text>"`."""


# --- Prüfen ---------------------------------------------------------------------


def _jetzt_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _schluessel(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _ist_zu(issue: dict[str, Any]) -> bool:
    return str(issue.get("state") or "").lower() == "closed" and issue.get("state_reason") not in (
        "not_planned",
        "duplicate",
    )


def _zeilen(repo: Path, ref: str | None, ticket: int, wt_basis: str) -> list[dict[str, Any]]:
    """Bau-Log-Zeilen des Tickets: Hauptzweig + Laufdateien (Worktree, Repo)."""
    zeilen = capo.log_vom_ref(repo, ref, ticket) if ref else []
    for _quelle, pfad in capo.laufdateien(repo, capo.worktree_ordner(ticket, wt_basis), ticket):
        zeilen += capo.lauf_zeilen(pfad) or []
    return zeilen


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
        offen = not _ist_zu(issue)
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
        if not offen:
            zu.add(nr)
            if not erster and nr not in alt_zu:
                titel = capo._kurz(issue.get("title"), 80)
                text = f"#{nr} neu geschlossen — Belegseite prüfen: {titel}"
                eintraege.append((text, "zu", nr))
    verstoesse: set[str] = set()
    for v in tick.verstoesse:
        k = f"{v.ticket}:{v.regel}"
        verstoesse.add(k)
        if not erster and k not in alt_verstoesse:
            text = f"#{v.ticket} capo-Verstoß {v.regel}: {capo._kurz(v.text)}"
            eintraege.append((text, "verstoesse", k))
    if tick.fertig and not erster and not alt.get("fertig"):
        eintraege.append((f"#{spec} SPEC FERTIG — Abschluss-Paket", "fertig", True))
    neu = {
        "zeilen": sorted(zeilen),
        "zu": sorted(zu),
        "verstoesse": sorted(verstoesse),
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
    proc = subprocess.Popen(  # noqa: S603 — Liste, kein Shell, Prompt nur über stdin
        cmd, cwd=str(repo), stdin=subprocess.PIPE, env=_kind_env()
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
                return _fehler(
                    spec,
                    f"Claude über Zeitlimit {_timeout_s():.0f} s — beendet, Merker bleibt.",
                )
            if code != 0:
                return _fehler(spec, f"Claude Exit {code} — Merker bleibt.")
            if leitstand.notiz(notiz_name) == notiz_vorher:
                meldung = f"Takt #{spec}: Claude endete mit 0, Notizzettel nicht fortgeschrieben."
                log.warning("%s", meldung)
                print(f"WARNUNG: {meldung}")
            neu = ohne_ungezeigte(neu, eintraege[MAX_EINTRAEGE:])
        else:
            print(f"Takt #{spec}: nichts zu entscheiden")

        def merke(zustand: dict[str, Any]) -> None:
            zustand.setdefault("takt", {})[str(spec)] = neu

        leitstand.aendere_zustand(merke)
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
        subprocess.Popen(  # noqa: S603 — fester Befehl, kein Shell
            cmd,
            cwd=str(SKILL),
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
