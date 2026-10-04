"""Aufseher-Anleitung (#500, Spec #399: E42, E10, E11, E28, E38, E8, E31, E41, E40).

Einzige Quelle der festen Texte, die an mehreren Stellen wortgleich stehen müssen:
Aufseher-Prompt (``skripte/wache.py``), Anstupser der Eingriffs-Leiter
(``leiter.MINDSET_TEXT``) und des Aufpassers (``aufpasser.ANSTUPS_TEXT``) und die
Bau-Prompt-Vorlage (Platzhalter ``{MINDSET}`` und ``{ABLOESE_SOP}``, gefüllt in
``skripte/bau.py``). Nur Text, keine Logik — wer einen Satz ändern will, ändert ihn hier.
"""

from __future__ import annotations

#: Mindset (E10, E41, E40, E42): steht wortgleich im Aufseher-Prompt, in jedem
#: Anstupser und in der Bau-Prompt-Vorlage.
MINDSET = (
    "Mindset: Alles geht erst auf Staging — dort kann nichts live schaden. Entscheide "
    "offene Punkte nach bestem Wissen selbst, statt zu fragen. Notiere jede Entscheidung "
    "mit Optionen und Gründen (`to_spawn.py eintrag --typ entscheidung`) und gib sie in "
    "Befund, Zusammenfassungsseite bzw. Mail an David mit („ist schon auf Staging, kannst "
    "testen“), damit David direkt live schalten kann. Nichts wartet auf David außer der "
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
ABLOESE_SOP = """Ablöse-SOP (gilt für Bau-Sessions und für den Aufseher selbst, immer in dieser Reihenfolge):
1) Handoff: die volle Session schreibt ihren Handoff (Bau-Session `docs/handoffs/HANDOFF_<datum>_<N>.md` im Ticket-Worktree, Aufseher `docs/HANDOFF_<datum>_waechter_<S>.md`, ohne Ticket `.claude-handoff.md`), committet ihn mit Pathspec + [skip ci] und pusht (Bau-Session: zuletzt gepushter `wip/<N>-<kurzname>`).
2) Startprompt: sie gibt einen fertigen Startprompt für die Folgesession aus und legt ihn als Datei ab (`docs/handoffs/START_<datum>_<N>.txt` bzw. `docs/handoffs/START_<datum>_waechter_<S>.txt`) — mit Pfad zur Handoff-Datei, Ticket-/Spec-Nummer und Branch. Danach tut die alte Session nichts mehr (keine Zeile „Staffel: weiter“).
3) Neue Session öffnen, Remote Control aktiv — Bau-Server: neues tmux-Fenster in `spec-<S>` (`bau <N> neu` mit `bau <N> --sofort --ohne-prompt`, darin `/remote-control`); PC: neuer Windows-Terminal-Tab (`wt -w 0 nt --title "bau <N>" pwsh -NoExit -Command "bau <N> --sofort --ohne-prompt"`, darin `/remote-control`).
4) Startprompt aus der Datei in die neue Session einfügen.
5) Die neue Session schließt die alte — erst wenn sie übernommen hat (Startprompt angenommen, erste Antwortzeile da); vorher bleibt die alte stehen.
Werkzeug: Bau-Session auf dem Bau-Server → der Aufseher ruft die Eingriffs-Leiter (`to_spawn.py leiter <S> <N>`), deren Stufe 2/3 (Skill `respawn`) macht Schritt 1–5. Am PC macht der Aufseher Schritt 3–5 selbst nach obiger Form. Aufseher selbst → Selbstneustart nach derselben SOP: Schritt 1–2 schreibt er selbst, Schritt 3–5 übernimmt die Aufsicht `wache.py` (Nachfolger im selben Fenster `wache <S>`, Remote Control „Aufseher #<S>“ automatisch, alter Aufseher endet erst nach Übernahme) — auslösen mit `wache.py <S> --abloesen <handoff>` oder automatisch an der Handoff-Grenze (#436)."""
