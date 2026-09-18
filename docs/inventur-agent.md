# Analyse-Agent für die Werkzeug-Inventur (Setup-Wizard Teil 2)

Die Wizard-Session startet diesen Agenten **mit `model: sonnet`** (ausdrücklich
angeben, sonst erbt er das teure Session-Modell). Er bekommt die Ausgabe von
`python ~/.claude/skills/to-spawn/to_spawn.py inventur --json` und ändert selbst
nichts: keine Dateien, kein `--schreiben`, keine Installation.

## Prompt (so übergeben, `<JSON>` ersetzen)

```
Du prüfst eine Werkzeug-Inventur für das Repo <repo>. Unten steht die Ausgabe von
`to_spawn.py inventur --json`. Du änderst NICHTS, du lieferst nur Vorschläge.

Aufbau: 12 Kategorien in fester Reihenfolge (bauen, testen, design, recherche,
medien, deploy, projekt, sicherheit, kontext, steuerung, sehen, zweitmeinung),
je Werkzeug name/art/kategorie/quelle/beschreibung/genutzt. Dazu projekt
(Dateiendungen, Merkmale, Namen der .env-Schlüssel), historie (Zähler, auch
Bash-Unterbauten wie ffmpeg/adb/gh), setup_zeilen (fehlende Unterbauten) und
tote_winkel (schon gemeldet: leere Kategorien, Skill-Ordner ohne SKILL.md,
doppelte Kurznamen, fehlende Historie, genutzt aber nicht installiert, fehlende
Agenten-Dateien). abgewaehlt_nicht_gefunden = abgewählt, gerade nicht installiert.

Prüfe in dieser Reihenfolge:
1. Einordnung: Steht ein Werkzeug offensichtlich falsch? Die Zuordnung ist eine
   Schlüsselwort-Regel und irrt sich (z. B. ein Rechtschreib-Skill in „Design“,
   weil die Beschreibung „UI-Text“ enthält). Nenne nur klare Fehler.
2. Tote Winkel aktiv suchen, über die gemeldeten hinaus. Beispiele:
   - Merkmal „Frontend“, aber kein Werkzeug in „Sehen/Verstehen“ oder „Design“.
   - historie.unterbauten zeigt ffmpeg, aber „Medien“ ist leer.
   - Merkmal „Tests“, aber „Testen/Beweisen“ leer.
   - .env-Schlüssel für einen Dienst vorhanden, aber kein passendes Werkzeug.
   - Werkzeug oft genutzt, aber in keiner Kategorie, die dazu passt.
3. Setup-Zeilen: Ist die Abhilfe für dieses Projekt die richtige? Fehlt ein
   Unterbau, den Historie oder Projekt klar brauchen?

Antwort: höchstens 15 Zeilen, je Zeile genau ein Vorschlag in einer dieser Formen:
- UMKATEGORISIEREN <name>: <alt> → <neu> — <Grund in wenigen Wörtern>
- ERGÄNZEN <kategorie>: <Werkzeug-Vorschlag> — <Grund>
- SETUP <name>: <Abhilfe> — <Grund>
- ABWÄHLEN? <name> — <Grund> (nur wenn klar überflüssig, z. B. doppelt)
Keine Einleitung, keine Zusammenfassung. Nichts gefunden → eine Zeile „keine Vorschläge“.

<JSON>
```

## Was die Wizard-Session damit macht

Die Vorschläge zeigt sie dem Nutzer zusammen mit der Liste. Umkategorisieren ist
heute nur ein Hinweis (die Regel-Tabelle `REGELN` in `to_spawn/inventur.py` ist
die Quelle); ergänzen und einrichten macht der Nutzer oder eine eigene Session.

werkzeuge.json liest heute noch niemand beim Start — die Übergabe an die
Bau-Session (z. B. --disallowedTools) folgt im Nest-Ticket #210.
