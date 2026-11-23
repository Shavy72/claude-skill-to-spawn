# Auftrag typescript

## Prüfe

- `any`, `as`-Casts oder `!`-Non-Null, die einen echten Typfehler verdecken (Auslöser nennen).
- Unbehandelte Promises: fehlendes `await`, `.then` ohne `.catch`, `forEach(async …)`.
- `JSON.parse`/Fetch-Antworten ungeprüft als Typ behandelt an Trust-Boundaries.
- Vergleich/Wahrheit: `==`, `||` statt `??` bei gültigem `0`/`""`/`false`.
- Diskriminierte Unions ohne vollständigen `switch` (fehlendes `never`-Exhaustiveness).
- Mutation geteilter Objekte/Arrays, die als unveränderlich übergeben wurden.
- Zeitzonen/`Date`-Parsing mit Nicht-ISO-Strings.
- Exportierte Signatur geändert, Aufrufer nicht angepasst.
- `tsconfig` strict gelockert oder `@ts-ignore` ohne Grund.

## Ignoriere

- `let` vs. `const`, Pfeil- vs. Funktionsdeklaration, Formatierung (Prettier/ESLint).
- `any` in Testdateien und Fixtures.
- Fehlende Rückgabetypen, wenn Inferenz eindeutig ist.
- Vorschläge, eine andere Bibliothek zu nutzen.

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
