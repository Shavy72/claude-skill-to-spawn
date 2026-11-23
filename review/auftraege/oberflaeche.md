# Auftrag oberflaeche (drei Linsen)

Der Dirigent startet je Linse einen eigenen Prüfer. Dein Prompt nennt deine Linse. Lies den Skill deiner Linse und wende NUR dessen Prüfteil an — keine Fixes, keine Umbauten.

| Linse | Skill | Blick |
|---|---|---|
| impeccable-audit | `impeccable` (Modus audit) | Gestaltung, Hierarchie, Abstände, Zustände (leer, lädt, Fehler) |
| web-design-guidelines | `web-design-guidelines` | Regeln für Web-Oberflächen, Formulare, Interaktion |
| accessibility | `accessibility` | WCAG 2.2 AA: Kontrast, Tastatur, Fokus, Labels, Rollen, alt |

## Prüfe

- Nur geänderte Oberflächen-Dateien und die Seiten, die sie rendern.
- Phone-Viewport 390×844: Überlauf, abgeschnittener Text, Tap-Ziele < 44 px, fixe Breiten.
- Zustände: leer, lädt, Fehler, lange Texte, deutsche Umlaute (ä ö ü ß) statt Ersatzschreibung.
- Tastatur: alle Aktionen erreichbar, sichtbarer Fokus, keine Fokusfalle.
- Formulare: Label, Fehlermeldung am Feld, kein Datenverlust bei Fehler.
- Falls Screenshot-Pfad im Prompt: Befunde am Bild belegen; sonst am Markup/CSS.

Projekt-eigene Regeln: Liegt im geprüften Repo `.review/regeln.md`, gelten dessen Regeln zu Oberfläche zusätzlich (z. B. Design-Doktrin vorher lesen, einfache Wörter, Namen der Knöpfe wie auf dem Bildschirm).

## Ignoriere

- Geschmacksfragen ohne Regelbezug (Farbton, „wirkt altmodisch“).
- Bestehende Probleme auf unveränderten Seiten.
- Vorschläge für neue Features oder Umgestaltung ganzer Seiten.

## Ausgabe

Wie `basis.md`. `datei`/`zeile` = Markup- oder CSS-Stelle. `pruefer` = `oberflaeche-<linse>`. Befund ohne Datei-Stelle (nur im Bild sichtbar) → Stelle suchen, sonst weglassen.
