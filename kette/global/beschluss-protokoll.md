## Beschluss-Protokoll: Grillen schreibt, Spec liest (gilt in jedem Projekt, geht dem Text von grill-/to-spec-Skills vor)
Lange Chats vermischen Besprochenes und Beschlossenes (Lost in the Middle, Auto-Zusammenfassung). Darum hält jede Grill-Session (`grilling`, `grill-with-docs`, `grill-me`, `/wayfinder` …) ihre Klarheit laufend in einer Datei fest, und jede Spec (`/to-spec` o. ä.) baut nur aus dieser Datei. Oberstes Ziel des Grillens ist Klarheit; jede erreichte Klarheit wird sofort gesichert.

**Grillen — schreiben**
- **Datei:** `docs/GRILL_<JJJJ-MM-TT>_<thema>.md`, beim ersten Beschluss anlegen, danach nur fortschreiben. Pfad im Chat nennen.
- **Wann:** nach jeder Runde, sobald der Mensch einen Punkt ausdrücklich bestätigt (sein „ja“ zur Empfehlung oder seine eigene klare Vorgabe). Meine Empfehlung ohne Antwort des Menschen ist KEIN Beschluss.
- **Was:** nur Endstände, nie der Diskussionsverlauf. Je Beschluss so viel Text, dass eine fremde Session ohne Chat versteht, worum es geht:
  `### E<n> — <Titel> ✅` · **Beschluss:** konkret, prüfbar · **Herleitung:** warum, welche Befunde/Fakten (mit Quelle), welche Alternative verworfen · **Wort des Menschen:** kurzes Zitat (Runde n).
- **Weitere Abschnitte:** `## Ziel` (Worte des Menschen, verdichtet) · `## Befunde` (Fakten aus Code/Recherche mit Quelle) · `## Verworfen ❌` (V<n>, je ein Satz Grund) · `## Offen 💭` (O<n>: besprochen, nicht entschieden — auch Gedankenspiele „wäre cool, wenn …“).
- **Änderung:** Kippt der Mensch einen Beschluss, alten Eintrag nicht löschen, sondern `~~E3~~ überholt durch E9` — die Datei ist die Wahrheit, nicht der Chat.
- **Abschluss:** erst wenn der Mensch die E-Liste gesehen und bestätigt hat → Kopfzeile `Status: abgeschlossen (Mensch bestätigt <Datum>)`. ADRs/Glossar (`domain-modeling`) bleiben zusätzlich für große, schwer umkehrbare Entscheidungen.

**Spec — nur aus dem Protokoll**
- **Quelle:** nur das Protokoll (Pfad aus Chat/Aufruf, sonst jüngste passende `docs/GRILL_*.md`). Der Chat dient nur zum Verstehen, nie als Inhaltsquelle. Tipp: nach dem Grillen `/clear`, dann `/to-spec <pfad>` in frischer Session.
- **Kein Protokoll** (altes Grillen): erst eins nach obigem Format aus dem Chat anlegen, streng einordnen (✅ nur mit ausdrücklichem Ja des Menschen, sonst 💭), den Menschen bestätigen lassen, dann weiter.
- **Zuordnung:** User Stories + Umsetzungs-Entscheidungen nur aus ✅, jede Zeile endet mit `(E<n>)` — „ausführlich“ heißt jeden ✅ vollständig abdecken, keine neuen Wünsche erfinden · Out of Scope = alle ❌ mit Grund · Weitere Notizen = nur Herleitung/Befunde aus dem Protokoll · neuer Abschnitt **Offene Fragen** = alle 💭, werden nicht gebaut. Neue Klärungen während der Spec (z. B. Test-Seams, Design) erst als E-Nummer ins Protokoll, wenn der Mensch sie bestätigt.
- **Gegenprüfung vor dem Veröffentlichen:** frischer Subagent ohne Chat bekommt nur Protokoll + Spec-Entwurf und meldet (a) Spec-Punkte ohne ✅-Quelle, (b) fehlende ✅-Beschlüsse, (c) 💭/❌ als Auftrag. Erst bei 0/0/0 veröffentlichen. Spec nennt oben `Quelle: docs/GRILL_<…>.md (Stand <Datum>)`.
