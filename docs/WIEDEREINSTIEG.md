# Wiedereinstieg — hier anfangen, wenn der Bau steht

Dieses Dokument ist der eine Einstieg für jede Session und jeden Menschen, der den Bau des
Bau-Systems („USB-Stick“, Lastenheft L1–L5) fortsetzen soll — nach einem Abbruch, einem
Maschinen-Ausfall oder einer langen Pause. Es braucht keinen Chat und keine Übergabe-Datei.

Pflege: Die führende Leit-Session hält den Abschnitt „Stand“ aktuell (nach jedem gemergten
Paket einer Etappe und bei jeder Ablösung). Regel dazu: Lastenheft L5. Der Test
`tests/test_uebergabe_sichern_145.py` wird rot, wenn der jüngste Stand-Eintrag mehr als 24 Stunden
älter ist als der jüngste Merge.

**Ablösungs-Regel (Leit-Session, Aufseher, Ober-Aufsicht):** vor dem Start der Nachfolgerin
1. hier im Abschnitt „Stand“ einen Eintrag schreiben (Stand, als Nächstes),
2. `python <skill>/to_spawn.py sichern <Übergabe-Datei> [<Entscheidungs-Log>]` aufrufen. Das kopiert
   Übergabe und Entscheidungs-Log in den privaten Sicherungs-Ordner der Maschine (Vorgabe:
   Notiz-Tresor `to-spawn/sicherung/<Rechnername>/`, ohne Tresor `~/.local/share/to-spawn/sicherung/`;
   eigener Ort: Maschinen-Liste, Gruppe `sicherung`, Schlüssel `ziel`) und lädt angefangene Zweige
   des Skill-Repos hoch (nie `main`/`master`, nie `ticket/*`). Ein Ziel in einem öffentlichen Repo
   wird verweigert.

Die Aufseher-Ablösung und jeder Hausmeister-Rundgang rufen dieselbe Sicherung von selbst auf.

## 1. Das Ziel in vier Sätzen

In jedem Repo, auf jeder Maschine (Windows-PC und Linux-Bau-Server) läuft die Kette
`grill-with-docs → /to-spec → /to-tickets → /to-spawn` ohne einen Handgriff von David bis
„alle Tickets auf Staging“. Der Skill bringt alles mit, richtet fremde Repos selbst ein, hält sie
aktuell und räumt hinter sich auf. Prüfen und Gate sind schnell und prüfen nur das Nötige.
„Fertig“ ist es erst, wenn jede Zeile M1–M15 in `docs/ABNAHME_L1.md` einen Beleg hat.

## 2. Wo was liegt

| Was | Ort | Wozu |
|---|---|---|
| Auftrag, Davids Wortlaut 1:1 | `docs/LASTENHEFT.md` (L1–L5, A1–A28, N1–N11) | Was gewollt ist und was nicht. Bei Zweifel gilt der Wortlaut. |
| Plan, Design, Etappen 0–5, Stand | `docs/PLAN_L1_usb_stick.md` | Wie gebaut wird, in welcher Reihenfolge, was zuletzt geschah (Abschnitt „Stand“). |
| Abnahme mit Belegen | `docs/ABNAHME_L1.md` | Was bewiesen ist. Tabelle M1–M15: die erste Zeile ohne Beleg ist das nächste Ziel. |
| Prüftor-Vergleich | `docs/PRUEFTOR_VERGLEICH.md` | Messwerte, Quellen, Empfehlung und Bau-Pakete für den Prüf-Werkzeugkasten. |
| Arbeitspakete | Issues und Pull-Requests dieses Repos | Jede offene Arbeit ist ein Issue oder ein Pull-Request. Sammel-Issues je Etappe: 49–53. |
| Angefangene Arbeit | Zweige dieses Repos (`l1/…`, `fix/…`) | Wird mindestens bei jeder Ablösung hochgeladen, auch unfertig. |
| Laufende Übergaben, Entscheidungs-Log | privater Notiz-Tresor (Obsidian-Vault; auf dem Bau-Server `~/obsidian/`), Ordner `projects/bau-system/` | Tagesgenauer Verlauf, Fallen, Entscheidungen mit Grund. Komfort, nicht Voraussetzung. |

Sind die Übergaben verloren, reicht dieses Repo: Auftrag, Plan, Abnahme und Issues tragen alles,
was zum Weiterbauen nötig ist.

## 3. Stand

- **2026-11-21 23:15 (Server-Uhr, main f4de1b7):** Pull-Request 161 (Issue 154, Windows-Prozessabfrage)
  ist gemergt.

- **2026-11-21 23:06 (Server-Uhr, main 175c8ec):** Gemergt: Pull-Request 170 (Nachbesserung
  Windows-Job-Objekt zu 151; Issue 122 bleibt offen bis zum Lauf am PC), 146 (Issue 88 zu: Folge-Tickets
  über befund, Spec schließen mit Sperre), 167 (Issue 135 bleibt offen: Hausmeister zählt Nummern nur im
  Ticket-Repo, offener Pull-Request hält den Ordner). Pull-Request 161 (Issue 154, Windows-Prozessabfrage)
  nicht gemergt: Linux-Lauf rot, `test_aufseher_428` findet die alte Rollenbezeichnung in
  `to_spawn/prozessbaum.py`. Offen im Pull-Request: 134, 148 (dritte Fix-Runde: geräumt wird nur noch
  selbst Gekennzeichnetes), 171 (Issue 95). Neu: Issue 169 (Sicherung: Probelauf lädt hoch, keine
  Inhaltsprüfung). Auf dem Bau-Server ist der Skill mit Stand 34a7ab1 installiert, die Sicherung ist
  eingerichtet. Prüf-Tür Stufe 1 und 2 (Pull-Request 127) am Code belegt: `to_spawn/pruef/sofort.py`.

- **2026-11-21 22:59 (Server-Uhr, main 4e2447e):** Gemergt: Pull-Request 160 (Issue 145,
  `to_spawn/uebergabe_sichern`: Übergaben und Entscheidungs-Log sichern sich von selbst; Issue 145 bleibt
  offen: Kaltstart-Probe M15, Windows-Lauf, `sicherung.dateien` eintragen), 127 (Issue 100 zu; Prüf-Tür
  Stufe 1 und 2), 166 (Issue 156 zu; Prüf-Test to-tickets). Offen im Pull-Request: 134, 146, 148, 161, 167.

- **2026-11-21 22:51 (Server-Uhr, main 0118fde):** Gemergt: Pull-Request 159 (alte Rollenbezeichnung
  entfernt), 158 (Issue 155 zu), 142 (Issue 94 zu), 151 (Issue 122, Windows-Job-Objekt). Offen im
  Pull-Request: 127 (Issue 100, Merge läuft), 160 (Issue 145), 146 (Issue 88), 148 (Issue 136, Fix-Runde 2),
  134 (Issue 93), 161 (Issue 154, vom PC). Bau läuft zu Issue 135, 95, 156. Als Nächstes: 129 nach 135;
  102 vor 101, 104, 149; 96, 97.

- **2026-11-21 22:37 (Server-Uhr):** Gemergt: Pull-Request 152 (Issue 144 zu), 153 (roter Test
  `test_aufseher_428` auf main behoben), 137 (P7c-a, Windows-Testaufbau; die Sperre für
  `to_spawn/prozessbaum.py` ist damit aufgehoben). In Fix-Runde nach Prüfer-Urteil: 127 (Issue 100,
  Runde 4), 142 (94), 146 (88), 148 (136). 3b-0 entschieden: DuoPlus bekommt den schnelleren
  Voll-Lauf (Issue 102) statt Test-Auswahl (101). Neue Issues: 155 (install.sh scheitert an
  `repo-scripts/__pycache__`), 156 (Prüf-Test to-tickets, Rest-Befunde aus 144); Kommentar an 96
  (App-Kopie `scripts/capo.py` trägt im Tick-Namen noch die alte Rollenbezeichnung). Als Nächstes: 135, 129, 145, 102, 101, 104.

- **2026-11-21 22:33 (Server-Uhr):** Gemergt: Pull-Request 126 (Issue 103, 3b-4 Wackler, main 8dfcaa8),
  143 (Issue 138), 131, 132, 133 (Windows-Pakete P7d, P7c-b, P7e). Offen und in Prüfung: 127 (Issue 100),
  134 (Issue 93), 137 (P7c-a), 142 (Issue 94, Fix-Runde läuft), 146 (Issue 88), 148 (Issue 136).
  Als Nächstes: Issues 135, 129, 145, 101, 102, 104. Bekannter roter Test auf main:
  `tests/test_aufseher_428.py` (Fix läuft als eigener Pull-Request). Folge-Härtung zu 126
  (Wackler/Voll-Lauf, zählt für M12): Issue 149.

- **2026-11-21 22:27 (Server-Uhr):** Seit dem letzten Eintrag gemergt: Pull-Requests 131, 132, 133
  (Windows-Reste P7c-b, P7d, P7e), 141 (Issue 87, to-tickets) und 143 (Issue 138, Installer-Randfälle).
  Offen im Pull-Request: 126 (Issue 103, Fix-Runde 3 im Review), 127 (Issue 100, Fix-Runde 3 im Review),
  134 (Issue 93, als Letzter der Welle), 137 (P7c-a, Windows grün gemeldet, Linux-Lauf läuft),
  142 (Issue 94, Fix-Runde 1 nach Review-Befund), 146 (Issue 88, Spec schließen).
  In Arbeit ohne Pull-Request: Issue 136 (Temp-Ordner aufräumen, L4). Als Nächstes: Issues 135, 129,
  144, 145. Abnahme: noch keine M-Zeile belegt.
- **2026-11-21 22:25 (PC-Uhr):** Etappe 0 fertig (sechs Bremsen-Tickets im App-Repo geschlossen).
  Etappe 1 auf dem Bau-Server gebaut und installiert; Windows offen (Issue 68, Pakete P7c–P7f,
  fünf Pull-Requests offen). Etappe 2 in 13 Pakete geschnitten (Issues 85–97), zwei gemergt.
  Etappe 3: Vergleich fertig, Tür `pruefen` gemergt, Pakete 3b in Issues 98–106, zwei im
  Pull-Request. Etappen 4 und 5 nicht begonnen. Abnahme: keine M-Zeile belegt.
  Offen neben dem Bau: Arbeitsordner `~/wt` auf dem Bau-Server noch nicht auf „nur offene Tickets“
  geräumt; Temp-Ordner auf der RAM-Platte (Issue 136).

Für den laufenden Stand gilt dieser Abschnitt „3. Stand“; die Leit-Session pflegt ihn zuerst. Für Ziel,
Etappen und Abnahme gilt der Plan. Der Plan-Abschnitt „Stand“ ist ein älterer Verlauf.

## 4. So geht es weiter, wenn alles steht

1. **Lesen** (in dieser Reihenfolge): `LASTENHEFT.md` → `PLAN_L1_usb_stick.md` (Design, Etappen,
   Stand) → `ABNAHME_L1.md`.
2. **Nachsehen, ob noch etwas läuft.** Bau-Server: `tmux ls` und
   `tmux list-windows -t leit-bausystem` (höchste `leit <N>` führt). PC: ein Terminal-Tab
   „Ober-Aufsicht“. Läuft eine Kette, nicht eine zweite starten, sondern dieser zuarbeiten.
3. **Wirklichkeit gegen Dokumente abgleichen:** `gh pr list --state open`, `gh issue list`,
   `git log origin/main -15`, installierter Skill-Stand auf der Maschine
   (`python ~/.claude/skills/to-spawn/to_spawn.py einrichten --pruefen`). Meldet das
   `skill_aktuell: grosse_luecke`, ist der Skill veraltet: `git -C ~/claude-skill-to-spawn pull && bash
   ~/claude-skill-to-spawn/install.sh`. Abweichungen zuerst im Abschnitt „3. Stand“ festhalten.
4. **Kette starten, falls keine läuft.** Immer über `~/.claude/bin/claude-frisch.sh` (Linux) bzw.
   `claude-frisch.ps1` (Windows), nie `claude` mit geerbter Umgebung. Der Startauftrag lautet:
   „Lies `docs/WIEDEREINSTIEG.md` im Skill-Repo und verfolge das Ziel 1:1 weiter, bis es erreicht
   ist.“ Gibt es noch eine Übergabe-Datei, wird sie zusätzlich gelesen; sie wächst fortlaufend, es genügen
   die letzten 200 Zeilen (`tail -200`). Die genauen Befehle für
   die zwei Maschinen stehen im Notiz-Tresor (`projects/bau-system/WIEDEREINSTIEG_maschinen.md`).
5. **Weiterbauen in der Reihenfolge der Etappen.** Offene Pull-Requests zuerst fertig machen
   (Review, Linux- und Windows-Lauf, Merge nach der Merge-Regel in Abschnitt 5), dann die offenen Issues der niedrigsten Etappe, dann
   die erste M-Zeile ohne Beleg.

## 5. Regeln, die immer gelten

- Keine Fragen an David. Entscheidung und Grund ins Entscheidungs-Log, weiterbauen (L1, L2).
  Echte Sperre (kein Zugriff, Secret fehlt): Eintrag im Entscheidungs-Log, Issue anlegen, mit einem
  anderen Paket weiterbauen.
- Der Auftrag wird bei jeder Ablösung im Wortlaut weitergegeben, nie als Zusammenfassung (L2).
- Erst vergleichen und messen, dann bauen; bewährtes Werkzeug vor Eigenbau (L3).
- Aufräumen, RAM, Platte, Neustarts landen nie bei David (L4).
- Kein Wissen nur im Chat oder nur auf einer Maschine: Was zählt, steht hier im Repo, als Issue
  oder im Tresor (L5).
- Merge-Regel: Ein Pull-Request wird nur gemergt, wenn das Prüf-Urteil „Hartes: nein“ lautet. Vorher
  die letzten Kommentare am Pull-Request lesen; bittet dort jemand um Aufschub, nicht mergen. Vor dem
  Merge `main` in den Zweig hineinmergen und die Pflicht-Tests des Zweigs laufen lassen. Kein `rebase`,
  kein `--force`. Befehl: `gh pr merge <N> --merge`.
- App-Live wird nie angefasst. Eine Leit-Kette je Maschine, eine Ober-Aufsicht am PC.
