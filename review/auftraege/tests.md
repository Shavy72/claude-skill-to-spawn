# Auftrag tests

Frage: Würden diese Tests rot werden, wenn der geänderte Code kaputt wäre?

## Prüfe

- Rot-Beweis: Neuer Test für einen Fix — würde er ohne den Fix fehlschlagen? Test, der immer grün ist (prüft nur Attrappe, `assert True`, fängt Ausnahme weg), ist ein Befund.
- Weg-Tests statt Attrappen: Nutzer-Ablauf wird mit gemockter eigener Logik getestet statt mit echtem Weg; Attrappen nur für externe Dienste (mit echten Aufzeichnungen) oder reine Rechenlogik.
- Tests ersetzt statt ergänzt: gelöschte oder abgeschwächte Assertions, entfernte Testfälle, gesenkte Schwellen, `skip`/`xfail` ohne Grund.
- Lücken: geänderter Fehlerpfad oder Grenzfall ohne Test, wenn er Daten, Kosten oder Sicherheit betrifft.
- Unzuverlässig: echte Zeit, Zufall ohne Seed, Reihenfolge-Abhängigkeit, Netzwerk ohne Aufzeichnung, geteilte Dateien zwischen parallelen Tests.
- Assertions prüfen Implementierungsdetail (Aufrufzähler einer Attrappe) statt Ergebnis.
- Testdaten auf Produktivsystemen oder echten Konten.
- KI prüft sich nicht selbst: Ein Test zählt nur, wenn er rot werden kann. Tests, die nur Attrappen prüfen oder deren Assert immer wahr ist (z. B. `assert True`, Assert auf die Attrappe selbst statt echtes Ergebnis) → Befund.

Projekt-eigene Regeln: Liegt im geprüften Repo `.review/regeln.md`, gelten dessen Regeln zu Tests zusätzlich (z. B. Tests nur addieren, Nutzer-Ablauf ohne Attrappen, parallele Läufe und gemeinsame Ressourcen).

## Ignoriere

- Testnamen, Aufteilung in Dateien, Fixture-Stil.
- Fehlende Tests für triviale Getter, reine Umbenennungen, Doku.
- Abdeckungsprozente ohne konkreten ungetesteten Risiko-Pfad.

## Ausgabe

Wie `basis.md`. `datei`/`zeile` = Test-Stelle bzw. ungetestete Code-Stelle. `pruefer` = dein Name.
