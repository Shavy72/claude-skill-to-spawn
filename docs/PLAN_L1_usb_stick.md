# Plan L1 — Skill als „USB-Stick“ (Vorgabe der Ober-Aufsicht, 2026-11-21)

Quelle des Ziels: `docs/LASTENHEFT.md`, Eintrag L1 (A1–A12, N1–N6). Dieses Dokument ist die
Vorgabe, wie L1 erreicht wird. Es ersetzt die alte Messlatte der Leit-Kette („alle Bremsen-Tickets
zu“) durch die Abnahme unten. Entscheidungen hier sind nach Davids Auftrag vom 21.11.2026 selbst
getroffen („entscheide selbst … ich möchte nicht ein einziges mal irgend etwas sagen müssen“) und
stehen zusätzlich im Entscheidungs-Log. David kann jede kippen.

## Befund (belegt, 21.11.2026 18:40–19:15, sieben Lese-Helfer)

- Die Leit-Kette auf dem Bau-Server kannte das Lastenheft nicht. Für A2, A4, A5, A6 gab es kein
  Ticket. Sie arbeitet 18 Einzel-Bremsen im App-Repo ab; von der alten Messlatte ist kein Punkt erreicht.
- Vermischung im App-Repo `duoplus-management`: rund 7.800 Zeilen allgemeine Mechanik
  (`deploy_schlange.py`, Gate-Teil von `safe_deploy_vps.sh`, `gate_auswahl.py`, `ticket_status.py` …)
  und rund 67 Testdateien (ca. 20.000 Zeilen) dazu. Eine Schlange, eine Sperre, ein Gate mit der
  App-Suite, ein master. Folge (belegt): App-Tickets #1341/#1494 machten master rot, System-Tickets
  (#1429) verloren volle Läufe; System-Tickets hingen an der App-Live-Freigabe.
- Skill im fremden Repo: `/to-spawn` bricht ab bei fehlendem Manifest, fehlenden Weiterleitungen
  (`scripts/bau.py`) oder fehlendem `server_repo`. Einrichten geht nur von Hand (`install --repo`),
  Weiterleitungen veralten still. Nichts legt Gate oder Staging an. 15 feste Verdrahtungen auf
  DuoPlus (u. a. `hausmeister.py:131–135,180,507`, `aufpasser.py:227,534`, `docs/agents/…` als
  Konstante in 9 Dateien). `to-spec`/`to-tickets` liegen nicht im Skill-Repo.
- Bau-Server: `~/wt` 221 Ordner, 152 GB; Swap 10 von 16 GB belegt.

## Entscheidungen

- **P1 Übernehmen statt beenden.** Die Leit-Kette bleibt die bauende Führung auf dem Bau-Server,
  bekommt aber dieses Ziel. Die Ober-Aufsicht am PC gibt Richtung, prüft die Windows-Seite und
  nimmt ab. Grund: Die Kette kennt das System und hat funktionierende Review- und Ablöse-Regeln.
- **P2 Design „Mechanik im Skill, Einstellungen im Repo, eine Tür“.** Verworfen: (a) alles ins
  App-Repo kopieren und per Abgleich aktuell halten — Kopien veralten, jede Regeländerung wäre eine
  Änderungs-Lawine über alle Repos; (b) reiner Einrichter, der Gate-/Schlangen-Code ins Repo legt —
  gleiches Problem. Gewählt: Der Skill führt die Mechanik selbst aus; das Repo enthält nur
  `.to-spawn/` (Einstellungen + Stand) und höchstens zwei eigene Befehle.
- **P3 System-Arbeit läuft ab jetzt im Skill-Repo.** Neue System-Tickets = Issues in
  `Shavy72/claude-skill-to-spawn`, Änderung = Pull-Request dort, geprüft von der Skill-eigenen
  Test-Suite, eingeschaltet durch Installieren. Nie mehr über die App-Schlange, nie an eine
  App-Freigabe gekoppelt (Davids Wort 21.11. 18:10).
- **P4 Bremsen-Tickets im App-Repo:** nur fertig machen, was hochlade-fertig ist oder
  master/Schlange blockiert (#1539, #1536, #1537, #1385, #1445, #1429). Jedes andere offene
  System-Ticket bekommt genau eine von drei Antworten: „entfällt durch Umzug“ (schließen mit Verweis),
  „zieht als Skill-Issue um“, „bleibt App“. Keine neuen System-Tickets im App-Repo.
- **P5 Abnahme über ein Probe-Repo**, nicht über Ticket-Zahlen (siehe „Abnahme“).

## Design (Kästen und Türen)

**Kasten Skill** (alles Wissen über den Ablauf): Kette grill-with-docs → to-spec → to-tickets →
to-spawn, Bau-Session, Aufseher, Ablösung, Prüftor mit Reihenfolge und RAM-Einteilung, Staging-Schritt,
Hausmeister, Entscheidungs-Log, Abschluss-Paket. Für Windows und Linux.

**Kasten Repo** (nur Wissen über die App): `.to-spawn/config.json` und `.to-spawn/stand.json`.

**Tür 1 — `to_spawn.py einrichten [--pruefen]`** (läuft automatisch vor jedem `spawn`, A4–A6):
eine Soll-Liste im Skill, je Baustein eine Prüfung und ein Anleger. Ergebnis je Baustein:
da und aktuell · angelegt/nachgezogen · große Lücke. `stand.json` hält den Skill-Stand, mit dem
zuletzt eingerichtet wurde; weicht er ab, wird zuerst nachgezogen, dann gebaut. Große Lücken
(kein Prüf-Befehl, kein Staging) brechen nicht ab und fragen nicht, sondern werden zum
**Einrichtungs-Ticket**: Es wird als erstes Ticket der Spec vor alle anderen gesetzt und von einem
Agenten-Team gebaut (A5). Maschinen-Seite (venv, gh, Server-Klon, Nest, Hausmeister-Ziel) gehört
in dieselbe Soll-Liste.

**Tür 2 — Repo-Vertrag, zwei Befehle in der Konfig:**

- `befehle.pruefen <basis> <ziel>` → Exit 0 grün, 1 rot, Log auf stdout. Die App entscheidet, was
  ihre Prüfung ist. Fehlt der Befehl, legt das Einrichtungs-Ticket ihn an (Standard: Linter + Tests
  der geänderten Dateien + kleiner Rauchsatz).
- `befehle.bereitstellen <sha> staging` → Exit 0 ok, sonst Fehler; dazu `staging.url`. Fehlt er,
  legt das Einrichtungs-Ticket ein Staging an. `live` ist nie Teil des autonomen Laufs.

Alles Weitere (Origin, Ticket-Repo, Zweig-Muster, Worktree-Basis, harmlose Pfade, Manifest-Ordner,
Meldekanal) sind Konfig-Schlüssel mit Vorgaben, keine Konstanten im Code. Der Skill fragt die
Konfig, nie umgekehrt. Fehler werden wegdesignt: fehlender Schlüssel = Vorgabe, nie Abbruch.

**Prüftor im Skill („Reißverschluss“, N6):** eine Reihe je Maschine, nicht je Repo. Geprüft wird
nur, was die Änderung betrifft (das entscheidet `pruefen`), ein RAM-Deckel teilt Worker zu, rote
Tickets blockieren die Reihe nicht (hinten anstellen, Bau-Session bekommt das Log). System-Repo
und App-Repos haben je ihr eigenes `pruefen`; ein roter App-master hält keine System-Änderung auf.

**Prüf-Werkzeugkasten im Skill (L3, A15–A21) — schärft das Prüftor:**
Ein Kasten, eine Tür: `to_spawn.py pruefen <basis> <ziel>`. Der Aufrufer (Bau-Session, Prüftor,
Aufseher) sagt nur „von wo bis wo“. Der Kasten entscheidet selbst, was läuft, und gibt ein Urteil
(Exit 0 grün, 1 rot) plus ein Prüf-Protokoll zurück: was lief, was ausgelassen wurde, warum, wie lange.

Versteckt im Kasten (Aufrufer muss nichts davon wissen):

- **Erkennen:** Sprache und Test-Werkzeug des Repos (zuerst Python und Node, erweiterbar je Adapter).
- **Stufen-Leiter, immer nur so hoch wie nötig:**
  1. Sofort-Prüfung der geänderten Dateien (Format, Linter, Typen) — Sekunden.
  2. Betroffene Tests: nur Tests, die vom geänderten Code abhängen.
  3. Kleiner Rauchsatz: startet die App, die wichtigsten Wege antworten.
  4. Voller Lauf, parallel verteilt unter dem RAM-Deckel — nur bei Risiko-Auslösern
     (Abhängigkeiten, Prüf-Konfig, Datenbank-Schema, Auswahl nicht sicher bestimmbar) und am
     Sammelpunkt vor Staging.
  5. Weg-Test im Browser gegen Staging — nur wenn ein Nutzer-Ablauf berührt ist.
- **Nichts doppelt:** Ergebnis je Stand und Eingaben merken; war genau dieser Stand schon grün,
  läuft er nicht noch einmal.
- **Sammelpunkt statt Einzel-Stau:** mehrere fertige Tickets gemeinsam prüfen; ist der Stapel rot,
  durch Halbieren den Schuldigen finden, die anderen laufen weiter.
- **Wackel-Tests:** erkennen, einmal wiederholen, getrennt führen und melden — nie still übergehen,
  nie die Reihe blockieren.
- **Warm starten:** vorbereitete Umgebung je Repo, damit kein Lauf mit Installieren beginnt.
- **Schnell rot:** zuletzt rote und neueste Tests zuerst.

Kandidaten für die Umsetzung (erst vergleichen und messen, dann wählen — A19): Test-Auswahl über
Abhängigkeits-Karte (z. B. `pytest-testmon`, `vitest related`, `jest --findRelatedTests`),
Verteilung nach gemessener Laufzeit (`pytest-xdist` mit Zeit-Aufteilung), Ergebnis-Speicher nach
Inhalts-Hash (Prinzip Bazel/Turborepo/Nx), Sammel-Prüfung nach Art einer Merge Queue mit Halbieren,
schnelle Umgebungen (`uv`, fertige Container). Das Ergebnis des Vergleichs steht mit Quellen und
Messwerten in `docs/PRUEFTOR_VERGLEICH.md`; gewählt wird, was im Probe-Repo und in DuoPlus
nachweislich am besten läuft. Bewährtes Werkzeug vor Eigenbau.

Im Repo liegt nur, was nur die App wissen kann: Risiko-Pfade, die den vollen Lauf erzwingen, und
zusätzliche Befehle. `befehle.pruefen` bleibt als Ausnahme für Repos mit eigenem Gate (DuoPlus am
Anfang); Ziel ist, dass auch DuoPlus den Werkzeugkasten nutzt (Etappe 5).

Bewusst draußen: Live-Prüfungen, Last-Tests, Sicherheits-Scans über das Übliche hinaus.

**Aufräumen (N2):** Hausmeister liest Ticket-Repos und Worktree-Wurzeln aus der Konfig aller
eingerichteten Repos (Liste auf der Maschine), löscht Fertiges ohne Rückfrage, auf beiden Systemen.

## Etappen

0. **Laufendes sauber beenden** (P4), Skill installieren, `~/wt` aufräumen (Ziel: nur Ordner
   offener Tickets), master grün.
1. **Entkoppeln + Einrichter:** feste Verdrahtungen in Konfig-Schlüssel; `einrichten` mit Soll-Liste
   und `stand.json`; `spawn` ruft es immer zuerst; Weiterleitungen im Repo entfallen (Skill startet
   direkt) oder werden vom Einrichter erneuert.
2. **Kette vollständig im Skill-Repo:** `to-spec`, `to-tickets` (legt Manifest im konfigurierten
   Ordner an) und die Grill-Protokoll-Regel ziehen ins Skill-Repo; ein Installer für alle.
3. **Repo-Vertrag + Prüftor + Einrichtungs-Ticket** im Skill; DuoPlus wird erster Nutzer über zwei
   dünne Befehle auf seine vorhandenen Skripte (Verhalten bleibt gleich).
   Davor **3a Vergleich** (L3, A19): Recherche mit Quellen + Messung von mindestens zwei
   grundverschiedenen Ansätzen im Probe-Repo und an DuoPlus → `docs/PRUEFTOR_VERGLEICH.md`.
   Dann **3b Prüf-Werkzeugkasten** bauen (eine Tür `pruefen`, Stufen-Leiter, Prüf-Protokoll,
   Zeitmessung je Stufe), auf Linux und Windows.
4. **Probe-Repo-Abnahme** Linux und Windows (siehe unten). Was dabei bricht, wird als Skill-Issue
   an der Wurzel behoben und die Probe von vorn gefahren.
5. **Umzug der DuoPlus-Mechanik** in der risikoarmen Reihenfolge: Konfig-Schlüssel →
   `safe_deploy_vps.sh` in Prüf- und Deploy-Teil schneiden → passive Teile (ticket_status,
   deploy_warten, wackelig, pytest_deckel, Kern gate_auswahl) → Nachtlauf → zuletzt die Schlange
   (erst gegen ein Bare-Repo proben). System-Tests ziehen mit; im App-Gate laufen danach nur App-Tests.

Etappe 5 darf die laufende App-Arbeit (Spec #1330 am PC) nie blockieren: jeder Schritt
verhaltensgleich, Staging zuerst, App-Live bleibt Davids Entscheidung.

## Abnahme (neue Messlatte für „fertig“)

- **M1** Frisches Probe-Repo (kleine Web-App, kein `.to-spawn/`, kein Gate, kein Staging, keine
  Manifeste): Kette Protokoll → Spec → Tickets → Spawn läuft auf dem **Bau-Server** ohne einen
  einzigen Eingriff bis „alle Tickets auf Staging“, inklusive selbst angelegtem Prüf-Befehl und Staging.
- **M2** Dasselbe am **Windows-PC** (`--ziel local`), mit zweitem frischen Probe-Repo.
- **M3** Zweite Spec im selben Probe-Repo: Einrichter meldet „alles da und aktuell“, Bau startet sofort.
- **M4** Skill-Stand ändern (Soll-Liste +1 Baustein) → nächster Spawn zieht im Probe-Repo von selbst nach.
- **M5** Während der Probe wird je eine Bau-Session und der Aufseher absichtlich beendet → Ablösung
  ohne Mensch, Lauf endet trotzdem auf Staging.
- **M6** Nach Abschluss: keine Worktrees/Zweige der Probe-Spec mehr auf der Maschine, kein offenes
  Ticket der Spec im falschen Zustand, kein Fenster ohne Kommentar stehen geblieben.
- **M7** Am Ende liegt ein Abschluss-Paket vor: Staging-Adresse, Entscheidungs-Log, Liste der
  Tickets; null Rückfragen im ganzen Lauf (aus den Sitzungs-Protokollen gezählt).
- **M8** DuoPlus: eine echte kleine Spec läuft über denselben Weg bis Staging; ein absichtlich
  roter App-Zweig hält eine gleichzeitige Skill-Änderung nicht auf.
- **M9** Prüftor-Zeit: Ticket mit kleiner Änderung im Probe-Repo von Push bis Urteil unter 5 min;
  DuoPlus-Median von Push bis Staging gemessen vorher/nachher, nachher klar kürzer.
- **M10** (L3, A15/A20) Werkzeugkasten sofort bereit: in zwei frischen Probe-Repos mit
  verschiedener Sprache (Python und Node), je auf Bau-Server und Windows-PC, liefert `pruefen`
  ohne repo-eigene Vorarbeit ein Urteil und ein Prüf-Protokoll.
- **M11** (A16/A17) Kleinste ausreichende Prüfung, drei Fälle im Probe-Repo: nur Doku geändert →
  kein Test läuft, Urteil in Sekunden · kleine Code-Änderung → nur betroffene Tests · Änderung an
  Abhängigkeiten oder Prüf-Konfig → voller Lauf. Das Prüf-Protokoll nennt jeweils den Grund.
- **M12** (A21/N9) Sicherheit trotz Tempo: mindestens 20 absichtlich eingebaute Fehler an
  verschiedenen Stellen (Probe-Repo und DuoPlus). Die schnelle Auswahl findet jeden Fehler, den
  der volle Lauf findet — 0 übersehen. Übersieht sie einen, gilt die Auswahl als nicht fertig.
- **M13** (A18/A19) Tempo gemessen statt geschätzt: `docs/PRUEFTOR_VERGLEICH.md` liegt vor
  (mindestens zwei Ansätze, Quellen, Messwerte). Kleine Änderung im Probe-Repo: Urteil unter
  2 min. DuoPlus: Median und langsamstes Zehntel von Push bis Urteil vorher/nachher, nachher
  mindestens halbiert. Zehn Tickets gleichzeitig laufen durch, ohne dass der RAM-Deckel reißt
  oder ein Ticket länger als 15 min wartet.

Jede M-Zeile braucht einen Beleg (Befehl + Ausgabe oder Protokoll-Zeile) in
`docs/ABNAHME_L1.md` im Skill-Repo.

## Arbeitsweise

- Keine Fragen an David. Entscheidung + Grund ins Entscheidungs-Log, weiterbauen.
- App-Live nie anfassen. Probe-Repos sind privat, tragen `probe` im Namen und werden nach der
  Abnahme archiviert, nicht gelöscht.
- Design-Schritt vor jedem neuen Modul: zwei Varianten, schmale Schnittstelle, Wissen an einem Ort.
- Parallel bauen, wo Dateien disjunkt sind; Helfer auf Opus, Führung auf Fable.
- Stand der Etappen fortlaufend unten eintragen.

## Stand

- 2026-11-21 19:20 — Plan angelegt, Kette benachrichtigt. Etappe 0 läuft.
- 2026-11-21 19:30 (Server-Uhr) — Etappe 0: App-Tickets #1539, #1445 gemergt und geschlossen; #1385, #1429, #1537 hochgeladen, laufen in der Schlange; #1536 (Hausmeister räumt `~/wt`): zwei Reviews „Hartes: nein“, Fix-Runde 2 läuft, danach Merge, Install, erster echter Hausmeister-Lauf. Installiert auf dem Bau-Server: 7c1500c.
- Einordnung: alle offenen System-Tickets im App-Repo eingeordnet; Sammel-Issues im Skill-Repo: Etappe 1 = 49, Etappe 2 = 50, Etappe 3 = 51, Etappe 4 = 52, Etappe 5 = 53; 110 App-Tickets mit Link geschlossen (offen 453 → 343), 24 bleiben App.
- Etappe 1: Schnitt in sieben Pakete P0–P6; P0 Konfig-Kern gemergt (Pull-Request 48, main 2aea41e, Issue 46). P2a (Aufpasser) und P5 (Einrichter) im Bau; P2b, P3a, P3b, P4a, P4b folgen, P1 (Hausmeister) nach Merge von #1536, P6 (Prüf-Test „keine festen Namen“ + Doku) zuletzt. Noch nichts von Etappe 1 installiert.
- 2026-11-21 20:05 (Server-Uhr, von `leit 58`) — Etappe 0: Skill 0cafd14 installiert 19:46; erster echter Hausmeister-Lauf seit 20:00 (`~/wt` 148G → 132G um 20:04, Platte 83 % → 77 %, läuft noch). #1539, #1445 grün auf master; #1385 (Kopf ec3339bc2) und #1429 testen in der Schlange, #1537 dahinter.
- Etappe 1: auf main (96d4b34) gemergt P0, P1 (Pull-Request 67), P2a (57), P2b (61), P3a (63), P3b (65), P4a (72), P4b (70), P5 (59). main a6e5d91 geprüft: Paket-Auswahl 880 passed, 21 skipped, 6 xfailed. P6a (Rest-Umstellungen) baut; danach P6b (Prüf-Test „keine festen Namen“ + Doku), dann P7 Windows-Prüfnetz (Skill-Issue 68; P7a Sammelbarkeit vom Server, P7b echte Windows-Fehler anhand der Kommentare der Ober-Aufsicht an Issue 68). Etappe 1 ist noch NICHT „für Windows prüfbar“ gemeldet (erst nach P6 + Install).
- Am PC zu prüfen (Ober-Aufsicht), aus P4b (Pull-Request 70, gemergt), im App-Repo am PC (pwsh 7):
  1. `pwsh -File <klon>\spawn_local.ps1 <S> -DryRun` → gleiche Tab-Liste wie mit `main`, kein Fehler.
  2. In einem Repo ohne `scripts\bau.py` (Wegwerf-Klon): `pwsh -File <klon>\spawn_local.ps1 <S> -DryRun` bricht nicht mehr mit „Kein ./scripts/bau.py“ ab.
  3. `install.ps1` nur in einem Wegwerf-Profil prüfen (`$PROFILE` umbiegen), dann `bau --help` ohne `scripts\bau.py` → startet Skill-`bau.py`.
  Hinweis: ps1-Änderungen wirken erst nach Neuinstallation am PC. Fundstelle 18 (Prozess-Regex `spawn_local.ps1`) bewusst nicht gebaut, weil Spec #1330 die Zeile nutzt. `pwsh` gibt es auf dem Bau-Server (`/usr/local/bin/pwsh`), Parser dort 0 Fehler.
- 2026-11-21 20:20 (PC-Uhr, Ober-Aufsicht Session 1) — **Neuer Lastenheft-Eintrag L3** (David,
  Wortlaut in `docs/LASTENHEFT.md`): Tempo und kluges Prüfen in jedem Repo. Im Plan ergänzt:
  Design „Prüf-Werkzeugkasten“, Etappe 3 um 3a Vergleich und 3b Bau, Abnahme M10–M13. Damit gilt
  „fertig“ erst mit M1–M13. Noch nichts davon gebaut oder gemessen.
- 2026-11-21 21:05 (Server-Uhr, von `leit 61`) — **Etappe 1 Bau komplett auf main:** zusätzlich
  P6a (Pull-Request 75, 2c198db, Issue 74), P6b (78, cb094cf), P7a (76, b4ce927), P7b (80, c2f4c07).
  main c2f4c07 geprüft: 956 passed, 10 skipped, 4 xfailed, EXIT 0 (Auswahl-Lauf `-n 3`, kein
  Voll-Lauf). Offen für den Abschluss von Etappe 1: Install c2f4c07 läuft gerade; „Fertig“ 2–7 noch
  zu belegen; `docs/ABNAHME_L1.md` fehlt noch; PC-Lauf der Ober-Aufsicht (Sammel-Issue 68).
- **Etappe 3a erledigt:** `docs/PRUEFTOR_VERGLEICH.md` (Pull-Request 82, cda7107), unabhängig
  gegengelesen. Empfehlung Variante 1 (zustandslos: statischer Graph + Regeln) plus Variante 3
  (Voll-Lauf schneller); testmon (Variante 2) nicht bauen. 3b startet mit der Mess-Vorstufe 3b-0.
- **Entscheidung der Leit-Session zur Plan-Frage aus dem Vergleich** (Abbruch-Kriterium 3b-0,
  Abschnitt 6.4): Wird für DuoPlus auf „Voll-Lauf schneller, keine Auswahl“ gewechselt, gilt M12 für
  DuoPlus als „nicht anwendbar, weil keine Auswahl läuft“ und wird in `docs/ABNAHME_L1.md` genau so
  benannt (kein Schein-Nachweis). M12 muss dann im Probe-Repo mit zweiter, unbekannter Fehlerreihe
  (≥ 20 Fehler) voll erfüllt sein. Grund: Davids L3 „nachweislich gut … nur das was mindestens
  notwendig ist“ (abgeleitet aus Lastenheft L3).
