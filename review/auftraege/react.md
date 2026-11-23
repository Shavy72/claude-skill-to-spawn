# Auftrag react

## Prüfe

- Hook-Regeln: Hooks in Bedingungen/Schleifen, Hooks außerhalb von Komponenten.
- `useEffect`: fehlende oder falsche Abhängigkeiten mit konkretem Stale-Wert; fehlendes Cleanup (Timer, Listener, Abo, AbortController).
- State: direkte Mutation, abgeleiteter State doppelt gehalten und driftet, `setState` mit altem Wert statt Updater.
- Listen: fehlender oder instabiler `key` (Index bei umsortierbaren Listen).
- Race: Antwort alter Anfrage überschreibt neue (kein Abbruch/Ignore-Flag).
- Rendering: teure Berechnung oder neues Objekt je Render, das Memo/Effekt jedes Mal auslöst (nur mit belegtem Effekt).
- `dangerouslySetInnerHTML` mit Fremddaten.
- Server/Client-Grenze: Browser-APIs beim Server-Render, Hydration-Abweichung.
- Formulare: kontrolliert/unkontrolliert gemischt, Submit ohne `preventDefault` wo nötig.
- Barrierefreiheit-Basis: klickbares `div` ohne Rolle/Tastatur, Bild ohne `alt`, Eingabe ohne Label.

## Ignoriere

- Fehlendes `useMemo`/`useCallback` ohne gemessenen Re-Render-Schaden.
- Komponentengröße, Dateiaufteilung, Namenskonventionen.
- CSS-Geschmack (macht Prüfer oberflaeche).

## Ausgabe

Wie `basis.md`. `pruefer` = dein Name.
