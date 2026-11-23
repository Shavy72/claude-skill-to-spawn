"""Aufseher-Anleitung (#500, Spec #399: E42, E10, E11, E28, E38, E8, E31, E41, E40).

Einzige Quelle der festen Texte, die an mehreren Stellen wortgleich stehen müssen:
Aufseher-Prompt (``skripte/wache.py``), Anstupser der Eingriffs-Leiter
(``leiter.MINDSET_TEXT``) und des Aufpassers (``aufpasser.ANSTUPS_TEXT``) und die
Bau-Prompt-Vorlage (Platzhalter ``{MINDSET}`` und ``{ABLOESE_SOP}``, gefüllt in
``skripte/bau.py``); den Befugnis-Abschnitt hält ``befugnis`` (#278). Nur Text, keine Logik — wer einen Satz ändern will, ändert ihn hier.

Dazu alle Texte, die ``to_spawn`` selbst in ein Bau- oder Aufseher-Fenster tippt
(:data:`SYSTEM_EINGABEN`, #372) — einzige Ausnahme ist die Logik :func:`ist_system_eingabe`.
"""

from __future__ import annotations

from .befugnis import KOPF as _BEFUGNIS_KOPF

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


def anstupser(wer: str) -> str:
    """Anstupser-Text mit Platzhalter ``{min}`` (Minuten still) für ``wer`` (Aufseher/Aufpasser)."""
    return f"{wer}: Du stehst seit {{min}} min still. {STOSS} {MINDSET} {STOSS_SCHLUSS}"


#: Kern-SOP Ablösung (E42): gilt für Bau-Sessions UND den Aufseher selbst, PC und Bau-Server.
ABLOESE_SOP = (
    """Ablöse-SOP (gilt für Bau-Sessions und für den Aufseher selbst, immer in dieser Reihenfolge):
1) Handoff: die volle Session schreibt ihren Handoff (Bau-Session `docs/handoffs/HANDOFF_<datum>_<N>.md` im Ticket-Worktree, Aufseher `docs/HANDOFF_<datum>_waechter_<S>.md`, ohne Ticket `.claude-handoff.md`), committet ihn mit Pathspec + [skip ci] und pusht (Bau-Session: zuletzt gepushter `wip/<N>-<kurzname>`).
2) Startprompt: sie gibt einen fertigen Startprompt für die Folgesession aus und legt ihn als Datei ab (`docs/handoffs/START_<datum>_<N>.txt` bzw. `docs/handoffs/START_<datum>_waechter_<S>.txt`) — mit Pfad zur Handoff-Datei, Ticket-/Spec-Nummer und Branch; Handoff und Startprompt entstehen immer zusammen (ein Commit). Bau-Session: der Startprompt trägt den Abschnitt „"""
    + _BEFUGNIS_KOPF
    + """ …“ aus dem eigenen Start-Auftrag wörtlich und vollständig, damit die Folgesession dieselbe Befugnis hat. Danach tut die alte Session nichts mehr (keine Zeile „Staffel: weiter“); eine Bau-Session hält dabei ihren Weckruf (ScheduleWakeup 600 s, noop: true), bis sie abgelöst ist — höchstens einmal, danach Loop mit stop: true beenden (Ablösung greift nicht: letzte Staffel-Runde, anderer Arbeitsordner, Sperre belegt). Bau-Session: Ablösung ab dem Ziel-Wert der Smart Zone (laufenden Schritt abschließen), spätestens an der Handoff-Grenze.
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


#: Alle Texte, die ``to_spawn``/``skripte`` per tmux in ein Fenster tippen, als Vorlage:
#: ``{…}`` = eingesetzter Wert. Wer eine neue Tipp-Stelle baut, trägt ihren Text hier ein
#: (``tests/test_leiter_frage_372.py`` zählt die Tipp-Stellen und bricht sonst den Build).
#: Bewusst nicht drin: der Start-Prompt einer neuen Session (``respawn._start_prompt``) — freier
#: Text aus der Handoff-Datei, erste Eingabe im frischen Fenster, davor steht nichts.
SYSTEM_EINGABEN: tuple[str, ...] = (
    anstupser("Aufseher"),
    anstupser("Aufpasser"),
    handoff_auftrag("{handoff}", "{start}"),
    WEITER_AUFTRAG,
    WEITER_AUFTRAG_AUFSEHER,
    REMOTE_CONTROL,
    EXIT,
)
#: Claude Code zeigt eine lange Einfügung (``respawn.tippen`` fügt als Bracketed Paste ein)
#: im Verlauf eingeklappt; den Inhalt sieht man dann nicht.
_EINGEKLAPPT = "[Pasted text #"
#: Kürzer abgeschnittene Zeilen zählen nicht als Anfang eines System-Texts.
_MIN_ANFANG = 12


def ist_system_eingabe(eingabe: str) -> bool:
    """``True``, wenn ``eingabe`` (Text hinter ``❯``, nur die erste Fensterzeile) ein Text aus
    :data:`SYSTEM_EINGABEN` sein kann, also vom System getippt, nicht vom Menschen (#372).

    Der Aufseher wertet so eine Zeile nie als „der Mensch hat bis hier gesehen“. Verglichen wird
    der Anfang bis zum ersten Platzhalter; eine umbrochene Zeile reicht, wenn sie ein Anfang davon
    ist. Im Zweifel ``True`` (auch für eingeklappte Einfügungen): dann zählt mehr Text vor der
    Frage, die geht eher zum Menschen.
    """
    e = eingabe.strip()
    if e.startswith(_EINGEKLAPPT):
        return True
    for text in SYSTEM_EINGABEN:
        kopf = text.split("{", 1)[0]
        if e.startswith(kopf) or (len(e) >= _MIN_ANFANG and kopf.startswith(e)):
            return True
    return False
