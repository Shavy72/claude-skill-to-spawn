"""Aufseher-Anleitung (#500, Spec #399: E42, E10, E11, E28, E38, E8, E31, E41, E40).

Einzige Quelle der festen Texte, die an mehreren Stellen wortgleich stehen müssen:
Aufseher-Prompt (``skripte/wache.py``), Anstupser der Eingriffs-Leiter
(``leiter.MINDSET_TEXT``) und des Aufpassers (``aufpasser.ANSTUPS_TEXT``) und die
Bau-Prompt-Vorlage (Platzhalter ``{MINDSET}`` und ``{ABLOESE_SOP}``, gefüllt in
``skripte/bau.py``); den Befugnis-Abschnitt hält ``befugnis`` (#278). Nur Text, keine Logik — wer einen Satz ändern will, ändert ihn hier.

Dazu die festen Texte, die ``respawn``, Aufseher-Ablösung und Aufpasser in ein Fenster tippen.
"""

from __future__ import annotations

from .befugnis import KOPF as _BEFUGNIS_KOPF
from .befugnis import aufseher_text as _aufseher_text
from .ticket_zustand import STAGING_FERTIG_LABEL

#: Überschrift der Entscheidungs-Linie (#376).
LINIE_KOPF = "Entscheidungs-Linie"

#: Mindset (E10, E41, E40, E42): steht wortgleich im Aufseher-Prompt, in jedem
#: Anstupser und in der Bau-Prompt-Vorlage.
MINDSET = (
    "Mindset: Alles geht erst auf Staging — dort kann nichts live schaden. Entscheide "
    "offene Punkte nach bestem Wissen selbst, statt zu fragen. Notiere jede Entscheidung "
    "mit Optionen und Gründen (`to_spawn.py eintrag --typ entscheidung`) und gib sie in "
    "Befund, Zusammenfassungsseite bzw. Mail an den Menschen mit („ist schon auf Staging, kannst "
    "testen“), damit der Mensch direkt live schalten kann. Nichts wartet auf den Menschen außer der "
    "fertigen Gesamtabnahme; Mail nur für Unverzichtbares (Spec fertig zur Abnahme, echter "
    "roter Stopp)."
)

#: Mindset-Stoß (E11 b): der feste Kern jedes Anstupsers.
STOSS = "Staging, entscheide selbst, mach fertig."

#: Schluss jedes Anstupsers (E11 c): volle Session → Ablöse-SOP statt weiterbauen.
STOSS_SCHLUSS = (
    "Bleib in der Smart Zone — ist deine Session voll (Handoff-Grenze), mach die "
    "Ablöse-SOP (Handoff + Startprompt schreiben, dann nichts mehr tun). Sonst setz deinen "
    "Loop genau dort fort, wo du warst."
)


def entscheidungs_linie(hauptzweig: str = "") -> str:
    """Entscheidungs-Linie (#376, Lastenheft L9): acht feste Punkte, einzige Quelle.

    Steht im Start-Text jeder Bau-Session (``{ENTSCHEIDUNGS_LINIE}`` in ``skripte/bau.py``)
    und als Maßstab im Aufseher-Auftrag (``befugnis.aufseher_text``) — ohne Zusatz-Auftrag.
    ``hauptzweig`` = Hauptzweig laut Repo-Konfig (``gh.hauptzweig``); leer → „dem Hauptzweig“.
    Das Staging-Label ist dasselbe, an dem ``to_spawn.py blocker`` einen erledigten Blocker
    erkennt. Ohne geschweifte Klammern (Aufseher-Vorlagen laufen durch ``str.format``).
    """
    zweig = f"`origin/{hauptzweig}`" if hauptzweig else "dem Hauptzweig"
    return f"""{LINIE_KOPF} (gilt ohne Zusatz-Auftrag):
1) Blocker erledigt = geschlossen ODER Label „{STAGING_FERTIG_LABEL}“ + Commit auf {zweig} (genau das prüft `to_spawn.py blocker`).
2) Dann selbst claimen und den Skill `implement` starten.
3) Keine Fragen an den Nutzer: eigenen Vorschlag nehmen, ins Bau-Log schreiben (`to_spawn.py eintrag --typ entscheidung`), weiterbauen; geht das nicht, entscheidet der Aufseher (Briefkasten-Frage mit Vorschlag).
4) Staging- und Test-Daten sind frei, auch Löschen — vorher Sicherung, danach Beleg.
5) Nie ohne den Nutzer: Live, Live-Daten, Live-Freigabe, Kosten, Konten echter Personen — diesen Schritt auslassen, Klasse-2-Frage in den Briefkasten (`to_spawn.py frage <N> --klasse 2 --frage … --vorschlag …`) + Issue-Kommentar „braucht den Nutzer“, den Rest weiterbauen. Kostenpflichtiges Extra, das niemand bestellt hat (z. B. Cloud-Review `/code-review ultra`), ist Nein: nicht starten, nicht vorschlagen, nicht fragen — höchstens eine Hinweiszeile im Bericht.
6) Fertig = grüne Schlange + echter Klickweg auf Staging + Beleg. HTTP-Abrufe für Belege nicht über `curl` (kann per Schutz-Hook gesperrt sein), sondern direkt über Python (`urllib.request`) oder den echten Browser/Playwright.
7) Vor jedem Push die Vor-Prüfungen lokal grün.
8) Jede für den Nutzer getroffene Entscheidung als eine Zeile (was · warum · Quelle) in die Entscheidungs-Liste der Spec: `to_spawn.py eintrag --typ entscheidung --frage <was> --wahl <Wahl> --grund "<warum> · Quelle: <Fundstelle>"`."""


def anstupser(wer: str) -> str:
    """Anstupser-Text mit Platzhalter ``{min}`` (Minuten still) für ``wer`` (Aufseher/Aufpasser)."""
    return f"{wer}: Du stehst seit {{min}} min still. {STOSS} {MINDSET} {STOSS_SCHLUSS}"


#: Kern-SOP Ablösung (E42): gilt für Bau-Sessions UND den Aufseher selbst, PC und Bau-Server.
ABLOESE_SOP = (
    """Ablöse-SOP (gilt für Bau-Sessions und für den Aufseher selbst, immer in dieser Reihenfolge):
1) Handoff: die volle Session schreibt ihren Handoff (Bau-Session `docs/handoffs/HANDOFF_<datum>_<N>.md` im Ticket-Worktree, Aufseher `docs/HANDOFF_<datum>_waechter_<S>.md`, ohne Ticket `.claude-handoff.md`), committet ihn mit Pathspec + [skip ci] und bringt ihn hoch (Bau-Session: pusht ihn auf den zuletzt gepushten `wip/<N>-<kurzname>`; Aufseher: `to_spawn.py einreichen <S>` — geht über `ticket/<S>-uebergabe` ohne Rückfrage auf den Hauptzweig, nie selbst auf den Hauptzweig pushen). Unter „Wartet auf den Menschen“ stehen in Übergaben nie Aufräumen, RAM, Platte oder Neustart.
2) Startprompt: sie gibt einen fertigen Startprompt für die Folgesession aus und legt ihn als Datei ab (`docs/handoffs/START_<datum>_<N>.txt` bzw. `docs/handoffs/START_<datum>_waechter_<S>.txt`) — mit Pfad zur Handoff-Datei, Ticket-/Spec-Nummer und Branch; Handoff und Startprompt entstehen immer zusammen (ein Commit). Bau-Session: der Startprompt trägt den Abschnitt „"""
    + _BEFUGNIS_KOPF
    + """ …“ aus dem eigenen Start-Auftrag wörtlich und vollständig, damit die Folgesession dieselbe Befugnis hat. Danach tut die alte Session nichts mehr (keine Zeile „Staffel: weiter“); eine Bau-Session hält dabei ihren Weckruf (ScheduleWakeup 600 s, noop: true), bis sie abgelöst ist — höchstens einmal, danach Loop mit stop: true beenden (Ablösung greift nicht: letzte Staffel-Runde, anderer Arbeitsordner, Sperre belegt). Bau-Session: Ablösung ab dem Ziel-Wert der Smart Zone laut `~/.claude/smart-zone.json` (laufenden Schritt abschließen), spätestens an der Handoff-Grenze.
3) Neue Session öffnen, Remote Control aktiv — Bau-Server: neues tmux-Fenster in `spec-<S>` (`bau <N> neu` mit `bau <N> --sofort --ohne-prompt`, darin `/remote-control`); PC (kein tmux, nichts tippbar): `to_spawn.py respawn <S> <N>` beendet die alte Session samt `bau.py` und Tab und öffnet einen neuen Windows-Terminal-Tab `bau <N>` mit `bau.py <N> --sofort --remote-control` und dem Handoff-Auftrag — Schritt 4 entfällt dort.
4) Startprompt aus der Datei in die neue Session einfügen.
5) Die neue Session schließt die alte — erst wenn sie übernommen hat (Startprompt angenommen, erste Antwortzeile da); vorher bleibt die alte stehen.
Werkzeug: Bau-Session (PC und Bau-Server) → zuerst ihr eigener Launcher: der Staffel-Hook von `bau.py` erkennt den committeten Handoff am Ende des Zugs und startet die Folgesession im selben Fenster — ohne Aufseher (#276); Aufseher und Aufpasser sind nur das Netz, falls das nicht greift (letzte Staffel-Runde, Sperre belegt). Daneben ruft der Aufseher auf dem Bau-Server die Eingriffs-Leiter (`to_spawn.py leiter <S> <N>`), deren Stufe 2/3 (Skill `respawn`) macht Schritt 1–5. Am PC ebenso über die Leiter; sie löst dort erst an der Kontext-Grenze oder nach langer echter Stille ab (kein Anstupsen). Aufseher selbst → Selbstneustart nach derselben SOP: Schritt 1–2 schreibt er selbst, Schritt 3–5 übernimmt die Aufsicht `wache.py` (Nachfolger im selben Fenster `wache <S>`, Remote Control „Aufseher #<S>“ automatisch, alter Aufseher endet erst nach Übernahme) — auslösen mit `wache.py <S> --abloesen <handoff>` oder automatisch an der Handoff-Grenze (#436)."""
)


#: Ablösung (respawn) nach Schritt a abgebrochen: wird der alten Bau-Session getippt.
WEITER_AUFTRAG = (
    "Ablösung abgebrochen (respawn). Die neue Session startet nicht — arbeite normal "
    "am Ticket weiter. Handoff-Datei und Start-Prompt dürfen liegen bleiben."
)

#: Ablösung des Aufsehers nach Schritt a abgebrochen: wird dem Aufseher getippt.
WEITER_AUFTRAG_AUFSEHER = (
    "Ablösung abgebrochen (respawn). Der Nachfolge-Aufseher startet nicht — arbeite als "
    "Bau-Aufseher normal weiter (nächster Tick wie gehabt). Handoff-Datei und Start-Prompt "
    "dürfen liegen bleiben."
)


def aufseher_befugnis(skill: str) -> str:
    """Befugnis + Mindset des Aufsehers für jeden Text, der ihn ohne vollen Auftrag weiterführt (#422).

    ``skill`` = Skill-Ordner (``startklar.skill_pfad()``). Teile aus ihren Quellen
    (``befugnis.KOPF``, ``befugnis.aufseher_text``, :data:`MINDSET`), keine Kopie.
    """
    return f"{_BEFUGNIS_KOPF} {_aufseher_text(f'python {skill}/to_spawn.py')} {MINDSET}"


#: Schaltet Remote Control in der neuen Session ein (respawn Schritt d).
REMOTE_CONTROL = "/remote-control"

#: Beendet eine Session (Aufpasser, Leiter Stufe 4).
EXIT = "/exit"


def handoff_auftrag(handoff: str, start: str) -> str:
    """Handoff-Auftrag (respawn Schritt a) für Bau-Session und Aufseher."""
    return (
        "Ablösung dieser Session (respawn, Skill handoff, Bau-Variante): Schreib jetzt den "
        f"Handoff nach {handoff} und den Start-Prompt für die neue Session nach {start} "
        "(nur Text, die neue Session liest ihn als ersten Auftrag). Gibt es die Dateien schon, "
        "sind sie aus einer früheren Runde veraltet: komplett mit dem jetzigen Stand "
        "überschreiben, auch wenn sich wenig geändert hat. Beide Dateien committen. "
        "Keine Zeile „Staffel: weiter“ ausgeben — die neue Session übernimmt, eine "
        "Folge-Runde in diesem Fenster wäre eine zweite Session. Danach nichts mehr tun."
    )


#: Einzige Quelle der Abschluss-Anweisung für Aufseher-Prompt (``skripte/wache.py``) und
#: Aufseher-Takt (``to_spawn/waechter_takt.py``) (#367, #429). Platzhalter ``{S}`` und ``{SKILL}``
#: werden erst beim Bauen des jeweiligen Prompts gefüllt.
ABSCHLUSS_ANWEISUNG = (
    "Bau fertig, bereit zur Abnahme (alle Bau-Tickets zu oder nur noch Live-Belege/checkpoint:human-Abnahme offen, "
    "bzw. „Kette … durch“ oder „SPEC FERTIG“) → PFLICHT Abschluss-Paket `--stand abnahme` (einmal): "
    "zuerst Rundschau-Auftrag erzeugen mit `python {SKILL}/skripte/abschluss_paket.py rundschau-auftrag {S}` "
    "(schreibt docs/agents/rundschau_auftrag_{S}.md mit Tickets, Direkt-Links, Pflicht-Folie Bild + Klick-Schritte je Bau-Ticket "
    "und Pflicht-Folie „Für dich entschieden“ aus docs/agents/entscheidungen_{S}.md). "
    "Dann Rundschau gemäß diesem Auftrag als Datei im Repo `docs/agents/rundschau_{S}.html` UND als Artifact erstellen: "
    "Skill `rundschau` (fehlt er: melden, kein Ersatz), Aufbau und Prüfung allein nach to_spawn/rundschau_deck.py — "
    "`python {SKILL}/skripte/rundschau_deck.py erzeugen --texte <json> --entscheidungen <json> --nach docs/agents/rundschau_{S}.html` "
    "(Teil 1 Nutzer-Präsentation, Teil 2 Details mit Bild + Klick-Schritten je Bau-Ticket + „Offenes“, Schluss Entscheidungs-Zeitstrahl aus dem Bau-Verlauf; Zahl nennen), "
    "Screenshots per echtem Browser-Blick; `ablegen --stand abnahme` verlangt `--rundschau-datei docs/agents/rundschau_{S}.html` im Repo, "
    "fehlt die Datei oder fehlt einem Bau-Ticket Bild/Klick-Schritte oder ist die Prüfung rot: Exit 4 mit Mängelliste, nichts abgelegt. "
    "Danach KI-Selbstprüfung der Rundschau gegen Staging (einmal, vor `ablegen`, #368): "
    "`python {SKILL}/skripte/abschluss_paket.py selbstpruefung planen {S}` — "
    "Exit 0 → genau einen frischen Helfer starten (liest `.to-spawn/selbstpruefung/{S}/auftrag.md`, "
    "klickt jede Klick-Schrittfolge kopflos gegen Staging, legt keine Testdaten dauerhaft an bzw. löscht markierte Dummy-Daten, "
    "schreibt je Schritt „passt / passt nicht“ + Beleg in `.to-spawn/selbstpruefung/{S}/ergebnis.json`), "
    "dann `python {SKILL}/skripte/abschluss_paket.py selbstpruefung sammeln {S}` "
    "(schreibt `docs/agents/selbstpruefung_{S}.md`; „passt nicht“-Schritte vor Abnahme reparieren oder im Paket sichtbar stehen lassen). "
    "Exit 2 → keine Staging-Adresse (`staging.url` fehlt), ohne Helfer weiter (`ablegen` vermerkt „Selbstprüfung nicht möglich, weil …“). "
    "Ohne Ergebnis-Datei bei gesetzter `staging.url` verweigert `ablegen --stand abnahme` (Exit 4). "
    "`python {SKILL}/skripte/belege_uebersicht.py {S}` und `python {SKILL}/skripte/test_uebersicht.py {S}` je als Datei im Repo und Artifact, "
    "Direkt-Links je Ticket in die Stage-App (staging.url aus .to-spawn/config.json + Route an die richtige Stelle, Rolle im Titel), "
    "Zugang nur als Namen (Basic-Auth-Nutzer, App-Rolle, Bitwarden-Eintragsname — nie Passwort). "
    "Vor dem Ablegen Prüfung „Bereit für Live?“ (nur lesend, fester Teil `live_bereit`, #369): "
    "`python {SKILL}/skripte/thermo_lauf.py live-bereit plan {S}` → einen Lese-Prüfer (`model: opus`, Prompt = Feld `prompt` unverändert) starten, "
    "JSON-Antwort als `<befunde_ordner>/teil-live_bereit.json` speichern, dann `python {SKILL}/skripte/thermo_lauf.py live-bereit sammeln {S}` "
    "(prüft: jede Zeile aus docs/agents/entscheidungen_{S}.md im Code umgesetzt, bei Live-Daten-Änderung Sicherung + Rückweg, Abnahme-Beleg je Bau-Ticket; "
    "schreibt docs/agents/live_bereit_{S}.md; Code 2 = Lücken gefunden und je ein Folge-Ticket an der Spec angelegt — kein stilles Weiterlaufen, erst Lücken schließen; "
    "Code 0 = bereit, docs/agents/live_bereit_{S}.md mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). "
    "Erst danach `python {SKILL}/skripte/abschluss_paket.py ablegen {S} --stand abnahme --stage <url> --rundschau <link> "
    '--rundschau-datei docs/agents/rundschau_{S}.html --belege <link> --tests <link> --direkt "<Titel (als Rolle)>=<url>"… '
    '--basic-auth-nutzer <name> --app-rolle "<Name (rolle)>" --bitwarden <eintrag>` '
    "(prüft Rundschau-Deck + docs/agents/live_bereit_{S}.md, legt ab, schreibt docs/agents/entscheidungen_{S}.md, mailt nicht). "
    "Danach Aufbau-Prüfung (einmal): `python {SKILL}/skripte/thermo_lauf.py plan {S}`. "
    "Exit 0 → je Eintrag in `teile` ein Subagent, alle parallel im selben Zug (`model: opus`, Prompt = Feld `prompt` unverändert); "
    "jede JSON-Antwort als `<befunde_ordner>/teil-<nr>.json` speichern, dann `python {SKILL}/skripte/thermo_lauf.py sammeln {S}` "
    "(schreibt docs/agents/thermo_{S}.md, mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). "
    "Exit 2/4 → überspringen. Exit 3 → fehlenden Teil nachstarten, erneut sammeln. "
    "Kein Umbau in der Spec — Befunde stehen als Code-Befunde im Marker docs/agents/thermo_{S}.md (dritter Teil der Abschluss-Mail), kein Issue. "
    "Danach Rückblick (einmal): `python {SKILL}/skripte/rueckblick.py planen {S}`. "
    "Exit 0 → ein Subagent (`model: opus`, Skill mp-retro) liest `.to-spawn/rueckblick/{S}/auftrag.md` und schreibt dort `ergebnis.json` "
    "wie im Auftrag beschrieben, dann `python {SKILL}/skripte/rueckblick.py sammeln {S}` "
    "(schreibt docs/agents/rueckblick_{S}.md, mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). "
    "Exit 4 → überspringen. Exit 3 → KI nachstarten, erneut sammeln. Exit 1 → Grund notieren, weiter. "
    "Zum Schluss `python {SKILL}/skripte/abschluss_paket.py nachsehen {S}` "
    "(genau eine Mail an den Menschen; nur für `--stand abnahme` gilt: Exit 3 = wartet noch, capo stößt es je Tick selbst an — nicht warten). "
    "Live-Schritt nur über `python {SKILL}/to_spawn.py live {S}` (die Tür prüft selbst vorher die Komplett-Prüfung, höchstens einmal je Spec): "
    "Exit 0 → live; Exit 1 → kein Live, Grund aus der Zeile beheben und erneut rufen; "
    "Exit 2 → `live.befehl` fehlt, Komplett-Prüfung grün, Live-Schritt nach Repo-Ablauf von Hand. "
    "Ohne ausdrückliches Wort des Nutzers geht nichts live. "
    "Meldet der Nutzer nach dem Abnahme-Paket Nachbesserungen (Kommentar im Spec-Issue), je Punkt ein Folge-Ticket an derselben Spec ausschließlich über "
    '`python {SKILL}/skripte/abschluss_paket.py nachbesserung {S} --punkt "<Punkt>"` anlegen (nie über `befund --stufe gelb`; Manifest ergänzt, Kette baut weiter); '
    "sind die Folge-Tickets fertig, läuft der Abnahme-Abschluss erneut (neue Rundschau, neue Selbstprüfung, `thermo_lauf.py live-bereit plan {S} --neu`, "
    "`abschluss_paket.py ablegen {S} --stand abnahme …` → Paket trägt „Runde 2“ im Titel; keine zweite Mail). "
    "Nach dem Live-Deploy dasselbe einmal mit `--stand live` (`abschluss_paket.py ablegen {S} --stand live …` + `abschluss_paket.py nachsehen {S} --stand live`); "
    "`nachsehen --stand live` bis Exit 0 wiederholen (capo stößt live nicht an)."
)
