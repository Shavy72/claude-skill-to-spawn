# Auftrag spec (zwei Achsen)

Zwei getrennte Achsen. Eine Achse darf die andere nicht verdecken: sauberer Code, der das Falsche tut, ist ein Befund; richtiges Verhalten, das Projektregeln bricht, auch.

Spec-Quelle steht im Prompt (Ticket/Issue, Handoff, Auftrag, Commit-Nachrichten). Keine Quelle → nur Achse Standards + Überbau.

## Prüfe — Achse Spec

- Fehlt etwas: Anforderung aus der Quelle nicht oder nur teilweise umgesetzt (Anforderung zitieren).
- Falsch umgesetzt: sieht erledigt aus, verhält sich aber anders als verlangt (Auslöser nennen).
- Überbau: Verhalten, Parameter, Abstraktionen oder Dateien, die niemand verlangt hat (Scope-Creep, spekulative Allgemeinheit).
- Commit-Nachricht behauptet etwas, das der Diff nicht tut.
- Nutzer-Ablauf verlangt, aber kein Weg-Test oder Live-Beleg vorgesehen.

## Prüfe — Achse Standards

- Dokumentierte Projektregeln (`CLAUDE.md`, `.claude/rules/`, `CONTRIBUTING.md`, Coding-Standards): Regel mit Fundstelle zitieren.
- Grundlinie Code-Gerüche (immer Ermessensbefund, `niedrig`, Konfidenz ≤ 80; Projektregel geht vor):
  unklarer Name · doppelter Code im Diff · Feature-Neid · Datenklumpen · Primitiv-Besessenheit · wiederholte Switches · Schrotflinten-Änderung · divergierende Änderung · spekulative Allgemeinheit · Nachrichtenketten · Mittelsmann · abgelehnte Erbschaft.
- SSOT-Module umgangen oder kopiert statt genutzt.

Projekt-eigene Regeln: Abschnitt `## spec` der privaten `~/.claude/review/regeln.md` (VERTRAG.md §2a) gilt zu SSOT-Modulen und Texten zusätzlich, Zeilen `Falls Repo <name>: …` nur in diesem Repo (z. B. nie kopierte SSOT-Module, Sprache und Schreibweise der Texte).

Fachliche Ablauf-Regeln des geprüften Projekts (z. B. erlaubte Aktionen automatisierter Abläufe) stehen im Abschnitt `## spec` der privaten `~/.claude/review/regeln.md` und gelten zusätzlich.

## Ignoriere

- Was Linter/Formatter erzwingen.
- Anforderungen, die ausdrücklich in ein Folge-Ticket verschoben sind.
- Stil-Vorlieben ohne Regel-Fundstelle.

## Ausgabe

Wie `basis.md`. `text` beginnt mit `[Spec]` oder `[Standards]`; bei Spec mit Zitat der Anforderung, bei Standards mit Regel-Fundstelle. Fehlende Anforderung ohne Code-Stelle → `datei`/`zeile` = Stelle, an der sie hingehört hätte. `pruefer` = `spec`.
