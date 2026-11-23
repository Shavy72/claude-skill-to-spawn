# Auftrag n8n (Workflow-JSON)

## Prüfe

- `connections` zeigt auf Knotennamen, die in `nodes` nicht existieren (nach Umbenennung).
- Fehlerpfad: HTTP-/DB-Knoten ohne `continueOnFail`-Absicht oder Error-Workflow; `continueOnFail: true`, danach kein Prüfen des Fehlers → stiller Fehler.
- Ausdrücke `{{ $json.x }}` auf Felder, die der Vorgänger-Knoten nicht liefert; `$node["Name"]` auf umbenannte Knoten.
- IF/Switch: fehlender Zweig oder Default, leerer Array-Eingang bricht still ab.
- Schleifen/Split-in-Batches ohne Abbruch; Retry ohne Obergrenze bei kostenpflichtigen APIs.
- Credentials: Werte im Klartext in Parametern statt Credential-Referenz; Token in URL.
- Webhook ohne Authentifizierung oder Signaturprüfung.
- Zeitplan (Cron-Trigger) doppelt oder in falscher Zeitzone.
- `pinData` oder Test-Webhook-URLs im Produktivstand.
- Code-Knoten: gleiche Regeln wie JS (`typescript.md` sinngemäß), Rückgabeform `[{json: …}]`.

## Ignoriere

- Knotenpositionen (`position`), IDs, `typeVersion`-Sprünge ohne Verhaltensänderung.
- Namen und Notizen von Knoten.
- Reihenfolge der Knoten in `nodes`.

## Ausgabe

Wie `basis.md`. `zeile` = Zeile im JSON, an der der betroffene Knoten bzw. Parameter steht. `pruefer` = dein Name.
