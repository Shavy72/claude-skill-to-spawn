# Wiedereinstieg — hier anfangen, wenn der Bau steht

Dieses Dokument ist der eine Einstieg für jede Session und jeden Menschen, der den Bau des
Bau-Systems („USB-Stick“, Lastenheft L1–L10) fortsetzen soll — nach einem Abbruch, einem
Maschinen-Ausfall oder einer langen Pause. Es braucht keinen Chat und keine Übergabe-Datei.

Pflege: Die führende Leit-Session hält den Abschnitt „Stand“ aktuell (nach jedem gemergten
Paket einer Etappe und bei jeder Ablösung). Regel dazu: Lastenheft L5. Alle Uhrzeiten in diesem
Dokument gelten in **Europe/Berlin (MEZ/MESZ)** (ältere Verlaufs-Einträge tragen teils noch
„Server-Uhr“/„PC-Uhr“). Der Test `tests/test_uebergabe_sichern_145.py` prüft deterministisch
Format, Reihenfolge und Konsistenz des obersten Stand-Eintrags mit seinem zitierten `main`-Commit
(und mit `TO_SPAWN_PRUEFE_STAND_FRISCHE=1` zusätzlich gegen `origin/main`); die laufende
24-Stunden-Frische gegen `origin/main` (einschließlich Squash-Merges) meldet
`to_spawn.py sichern --pruefen` über `to_spawn.uebergabe_sichern.wiedereinstieg_stand_hinweis` als
Hinweis, damit fremde Merges die Code-Suite nicht blockieren.

**Ablösungs-Regel (Leit-Session, Aufseher, Ober-Aufsicht):** vor dem Start der Nachfolgerin
1. hier im Abschnitt „Stand“ einen Eintrag schreiben (Stand, als Nächstes),
2. `python <skill>/to_spawn.py sichern <Übergabe-Datei> [<Entscheidungs-Log>]` aufrufen
   (`<skill>` = `~/.claude/skills/to-spawn` bzw. der Skill-Klon; Standard-Dateinamen je Repo sind
   `.claude-handoff*.md` und `entscheidungen.md` — sie werden auch ohne Argument eingesammelt und
   nie ins öffentliche Repo committet). Das kopiert Übergabe und Entscheidungs-Log in den privaten
   Sicherungs-Ordner der Maschine (Vorgabe: Notiz-Tresor `to-spawn/sicherung/<Rechnername>/`, ohne
   Tresor `~/.local/share/to-spawn/sicherung/<Rechnername>/`; eigener Ort: Maschinen-Liste
   `~/.config/to-spawn/maschine.json`, Gruppe `sicherung`, Schlüssel `ziel`) und lädt angefangene
   Zweige des Skill-Repos hoch (nie `main`/`master`, nie `ticket/*`). Ein Ziel in einem öffentlichen
   Repo wird verweigert. Ob `sicherung.dateien` und `sicherung.ziel` auf einer Maschine stimmen,
   prüft `python <skill>/to_spawn.py sichern --pruefen` rein lesend (Exit 0 = ok, 1 = Lücke).

Die Aufseher-Ablösung und jeder Hausmeister-Rundgang rufen dieselbe Sicherung von selbst auf.

## 1. Das Ziel in vier Sätzen

In jedem Repo, auf jeder Maschine (Windows-PC und Linux-Bau-Server) läuft die Kette
`grill-with-docs → /to-spec → /to-tickets → /to-spawn` ohne einen Handgriff vom Menschen bis
„alle Tickets auf Staging“. Der Skill bringt alles mit, richtet fremde Repos selbst ein, hält sie
aktuell und räumt hinter sich auf. Prüfen und Gate sind schnell und prüfen nur das Nötige.
„Fertig“ ist es erst, wenn jede Zeile M1–M15 in `docs/ABNAHME_L1.md` einen Beleg hat.

## 2. Wo was liegt

| Was | Ort | Wozu |
|---|---|---|
| Auftrag, Wortlaut des Menschen 1:1 | `docs/LASTENHEFT.md` (L1–L10, A1–A69, N1–N25) | Was gewollt ist und was nicht. Bei Zweifel gilt der Wortlaut. |
| Plan, Design, Etappen 0–5, Stand | `docs/PLAN_L1_usb_stick.md` | Wie gebaut wird, in welcher Reihenfolge, was zuletzt geschah (Abschnitt „Stand“). |
| Abnahme mit Belegen | `docs/ABNAHME_L1.md` | Was bewiesen ist. Tabelle M1–M15: die erste Zeile ohne Beleg ist das nächste Ziel. |
| Prüftor-Vergleich | `docs/PRUEFTOR_VERGLEICH.md` | Messwerte, Quellen, Empfehlung und Bau-Pakete für den Prüf-Werkzeugkasten. |
| Arbeitspakete | Issues und Pull-Requests dieses Repos | Jede offene Arbeit ist ein Issue oder ein Pull-Request. Sammel-Issues je Etappe: 49–53. |
| Angefangene Arbeit | Zweige dieses Repos (`l1/…`, `fix/…`) | Wird mindestens bei jeder Ablösung hochgeladen, auch unfertig. |
| Laufende Übergaben, Entscheidungs-Log | privater Notiz-Tresor (Obsidian-Vault; auf dem Bau-Server `~/obsidian/`), Ordner `projects/bau-system/` | Tagesgenauer Verlauf, Fallen, Entscheidungen mit Grund. Komfort, nicht Voraussetzung. |

Sind die Übergaben verloren, reicht dieses Repo: Auftrag, Plan, Abnahme und Issues tragen alles,
was zum Weiterbauen nötig ist.

## 3. Stand

Chronologisch absteigend (neuester Eintrag oben, ältere Einträge als Verlauf darunter; alle Uhrzeiten Europe/Berlin).

- **2026-11-25 19:30 (Europe/Berlin, main 7005f9c):** Welle-13-Pakete gemergt und Windows-Teil von L5 (#756) am PC belegt:
  - Seit `0741c91` gemergt: Pull-Requests 764 (Issue 762, Windows-Test-Härtung), 765 (Issue 755,
    `sichern --pruefen` und Bau-Server-Beleg), 766 (Issue 578, Werkzeug-Fehler-Log), 768 (Issue 760,
    Linux-Ort-Wahl für große Testläufe), 769 (Issue 757, Probe-1330-Befunde und `crontab`-Pfadschutz),
    770 (Issue 257, Windows-PID-Grenze), 771 (Issue 347, `zweig_ticket_repo` in `maschine.json`),
    772 (Issue 353, feste Wartezeiten in `spawn_srv`-Tests abgebaut), 773 (Issue 412, Skill-/MCP-Filter
    im Aufseher) und 774 (Issue 660, `agy`-Kontext-Auswertung und `--runner`).
  - Windows-PC (#756): `~/.config/to-spawn/maschine.json` angelegt (`sicherung.dateien` auf
    Ober-Aufsicht-Übergabe + Entscheidungs-Log, `sicherung.ziel` auf privaten Notiz-Tresor), per
    `to_spawn.py sichern --pruefen`, `--trocken` und zwei idempotenten Läufen (Exit 0) belegt sowie
    Windows-Kaltstart-Probe M15 (Lauf 4) bestanden und in `docs/ABNAHME_L1.md` dokumentiert.
  - Offen im Pull-Request (laut `gh pr list --state open`): 777 (Issue 641), 776 (Issue 761), 775
    (Issue 325), 767 (Issue 259), 678 (Issue 122, Windows-Job-Objekt).
  - Als Nächstes: offene Pull-Requests (777, 776, 775, 767, 678) prüfen und mergen → offene Anomalien
    aus Probe-Lauf 3 (u. a. 624, 641) → Windows-Seite (#68) und offene M-Zeilen M1–M15 in
    `docs/ABNAHME_L1.md` abnehmen; kurz vor „fertig“ Abschluss-Kaltstart-Probe M15 auf dem Bau-Server
    (#755).

- **2026-11-25 17:45 (Europe/Berlin, main 0741c91):** Großes Bau-Paket (Wellen 1–12) gemergt, #145 in Linux (#755) und Windows (#756) geteilt:
  - Seit `f3c85b1` gemergt (Auswahl): Pull-Requests 659 (Windows-Unterprozesse ohne Konsole), 662
    (Issue 660, Bau-Session wahlweise über `agy`), 666 (Issue 145, Stand-Prüfung mit Squash-Merges
    und Kaltstart-Probe M15 Lauf 3), 673 (Issue 149, Wackler-Voll-Lauf), 679 (Issue 216, Einrichter
    und `STAND.json`), 703/738 (Issues 136/177, Temp-Ordner L4), 705 (Issues 243/257/259,
    Windows-Prozessbaum), 737 (Issue 282, Wiederanlauf), 741 (Issues 215/261, Aufpasser-Sperre,
    `-z`-Geheimnisprüfung, `ticket_repo`), 744 (Issues 262/287, Deploy-Wache/Wecker Linux), 748/749
    (Issues 615/424/707, Hausmeister-Räumung und Klon-Ordner) sowie 751 (Issue 190, einheitliche
    Ruff-Formatierung).
  - Issue 145 (L5) wurde in #755 (Linux-Teil Bau-Server) und #756 (Windows-Teil PC) aufgeteilt.
    Auf dem Bau-Server sind `sicherung.dateien` und `sicherung.ziel` (Vorgabe Notiz-Tresor) in
    `~/.config/to-spawn/maschine.json` geprüft und über `to_spawn.py sichern --pruefen` belegt;
    offen bleibt für #755 nur die Abschluss-Probe M15 „vor fertig“ kurz vor Abschluss aller M1–M15.
  - Offen im Pull-Request (laut `gh pr list --state open`): 754 (Issue 204), 678 (Issue 122,
    Windows-Job-Objekt), 250 (Issue 149 Rest).
  - Als Nächstes: offene Pull-Requests (754, 678, 250) und offene Welle-13-Pakete (u. a. 755, 757–762)
    prüfen und mergen → offene Anomalien aus Probe-Lauf 3 (u. a. 624, 641) → Windows-Seite (#68, #756)
    und offene M-Zeilen M1–M15 in `docs/ABNAHME_L1.md` abnehmen; kurz vor „fertig“ zweite
    Kaltstart-Probe M15 (#755).

- **2026-11-25 04:55 (Europe/Berlin, main f3c85b1):** Stand nach Probe-Lauf 3 und Windows-Härtung:
  - Probe-Lauf 3 am 24./25.11.2026 auf dem Bau-Server in `docs/ABNAHME_L1.md` dokumentiert
    (Pull-Request 661, `main f3c85b1`): M1 teilweise belegt (`-mit` 2 Anomalien, `-ohne` 3 Anomalien;
    Abnahme durch den Nutzer 04:17 ohne Wartezeit im Minuten-Takt). Messlatte „null Anomalien“ noch
    nicht bestanden — offene Hauptursache: Issue 624 (fertige stille Fenster blockieren Plätze).
  - Jüngste Merges auf `main`: Pull-Request 657 (Issue 641, Worktree weicht bei gleicher Ticket-Nummer
    in zwei Repos aus), Pull-Request 650 (Issue 284, Platz-Grenzen je Rechner aus Messung) sowie
    Windows-Härtung und Terminal-/Freigabe-Sperren (Issues 283, 313, 553, 602; Pull-Requests 627, 629,
    633, 634, 635, 639, 640, 644, 648, 651, 652, 653).
  - Offen im Pull-Request (laut `gh pr list --state open`): 662 (Issue 660, Bau-Session wahlweise über
    agy), 659 (Issue 658, Windows-Unterprozesse ohne Konsolenfenster), 250 (Issue 149 Rest,
    Wackel-Prüfung).
  - Als Nächstes: offene Pull-Requests 659, 662 und 250 fertig prüfen und mergen → offene Anomalien
    aus Probe-Lauf 3 beheben (Issue 624, Reste zu 641) → Windows-Seite (Issue 68) und offene M-Zeilen
    in `docs/ABNAHME_L1.md` abnehmen.

- **2026-11-23 22:16 (Server-Uhr, main fff86c1):** Gemergt und am 23.11. 22:11 auf dem Bau-Server installiert
  (Install-Pfad `~/wt/skill-install-101`; `~/claude-skill-to-spawn` steht auf altem `7dc3ae8`, nicht benutzen):
  - Pull-Request 317 und 378 gemergt. Issues 273, 274, 376, 364, 366 geschlossen.
  - Offen: Issue 372 (Code installiert, echter Durchlauf folgt mit der Test-Spec) · Issue 283 (Server-Teil
    fertig, wartet auf Windows-Gegenprobe).
  - Issue 282 ist kein Pflicht-Punkt mehr vor der Test-Spec (Entscheidung des Eigentümers 23.11. ca. 21:25),
    wird als nächstes Paket gebaut. Issues 271 und 288 sind nicht Teil der Probe.
  - Die Test-Spec startet nur auf ausdrückliches Wort des Eigentümers.

- **2026-11-23 15:15 (Server-Uhr, main 5c9144b):** Gemergt und auf dem Bau-Server installiert (Stand 5c9144b):
  - Pull-Request 348 (Takt-Bremse: Test-Lücken und Merker-Randfall, Issue 346).
  - Pull-Request 350 (Rollenwort statt festem Personennamen, Namens-Reste Teil 5, Issue 325).
  - Pull-Request 351 (eigener kurzer pytest-Temp-Ordner je Lauf, Issue 349).
  - Pull-Request 352 (Hausmeister: Test-Lücken, Datums-Sperre, `ext::`-Umleitung, Reflog-Prüfung,
    Issues 347 und 254; main 5c9144b).
  - Offen: Pull-Request 354 (alte `pt-*`-Temp-Reste räumen, Refs Issue 177, Fix-Runde läuft) ·
    Issues 343 und 218 auf Kopf `0854e0a` doppelt geprüft (Review: 0 harte Befunde, unabhängige
    Prüfung „Hartes: nein“), es fehlt nur eine gültige volle Suite · Pull-Request 317 (274 P1)
    merge-reif, gesperrt, bis im App-Repo `docs/agents/bau_zusatz.md` liegt.
  - Vorfall 23.11. ca. 15:08: `/tmp` meldete „No space left“, Ursache war der Datei-Zähler (Inodes)
    bei 99 % und nur 41 % belegtem Platz; die laufende volle Suite wurde dadurch ungültig.
    Merksatz: bei „No space left“ zuerst `df -i /tmp` lesen. Folge-Issues: 353 (volle Suite von
    rund 24 auf rund 5 Minuten) und 355 (Erzeuger von Temp-Ordnern räumen selbst, Hausmeister
    prüft den Datei-Zähler).

- **2026-11-23 11:57 (Server-Uhr, main eb42e9e):** Gemergt und auf dem Bau-Server installiert (Stand eb42e9e):
  - Pull-Request 337 (Issue 315, Bremse für offene Briefkasten-Fragen im Aufseher-Takt, eigener
    Exit-Code bei Aufruf-Fehler; main cb6c1e5).
  - Pull-Request 334 per Squash (Issue 329 Teil 1, Review-Werkzeug als versionierte Quelle `review/`;
    main eb42e9e). Issue 329 bleibt offen für Teil 2, erst nach Merge von Pull-Request 317.
  - Offen: Pull-Request 317 (274 P1) weiter gesperrt, bis im App-Repo `docs/agents/bau_zusatz.md`
    liegt · in Arbeit: Issue 339 (Reste der Takt-Bremse), danach Prüf-Bündel 238/224/219/204/246.
  - Als Nächstes: 317 mergen, sobald entsperrt; dann 274 Teil 2, 282 Teil 2, 329 Teil 2.

- **2026-11-23 11:20 (Server-Uhr, main 95d561b):** Gemergt und auf dem Bau-Server installiert (Stand 95d561b):
  - Pull-Request 336 (Issue 325 Teil 4, Rollenwort statt Vorname). Reste Teil 5 stehen als Kommentar
    im Issue 325.
  - Issue 135 geschlossen (belegt durch Pull-Request 244). Issue 136 und 288 bleiben offen, Reste je
    als Kommentar im Issue.
  - Offen: Pull-Request 337 (Issue 315, Aufseher-Takt Bremse/Exit-Codes) in Prüfung ·
    Pull-Request 334 (329 P1, Review-Werkzeug als Quelle) in Fix-Runde (roter Ordner-Test), Merge nur
    als Squash · Pull-Request 317 (274 P1) merge-reif geprüft, wartet auf die Datei
    `docs/agents/bau_zusatz.md` im App-Repo.

- **2026-11-23 (main 9d0df5f):** Gemergt und auf dem Bau-Server installiert (Stand 9d0df5f):
  - Pull-Request 333 (Issue 325 Teil 3, Rollenwort statt Personenname, u. a. Hausmeister-Aufgabe
    und Leitstand-Seite). Volle Suite 3398 bestanden; Prüfer und Review ohne harte Befunde.
  - Offen: Pull-Request 334 (Issue 329 P1, Review-Quelle als Ordner `review/` im Repo) in Prüfung ·
    Pull-Request 317 (274 P1) merge-reif, wartet auf ein App-Ticket · in Bau: Issue 315 und
    Issue 325 Teil 4.

- **2026-11-23 10:29 (Server-Uhr, main 0b3d1db):** Seit dem Eintrag von 09:56 gemergt und auf dem
  Bau-Server installiert (Stand 0b3d1db):
  - Pull-Request 331 (Issue 325 Teil 2, Rollenwort statt festem Personennamen in
    `to_spawn/aufpasser.py`, `capo.py`, `hausmeister.py`, `skripte/leitstand.py`,
    `skripte/abschluss_paket.py`, `SKILL.md`; main 0b3d1db). Volle Suite 3396 bestanden, 81
    übersprungen, 18 xfailed. Issue 325 bleibt offen (Teil 3, Reste im Issue-Kommentar).
  - Als Nächstes: Issue 329 (Review-Werkzeug bekommt versionierte Quelle) · Issue 325 Teil 3 ·
    Pull-Request 317 (274 P1) wartet weiter auf das App-Ticket A1, danach 273 schließen, 274 P2, 282 P2.

- **2026-11-23 09:56 (Server-Uhr, main d23c7c0):** Seit dem Eintrag von 09:44 gemergt und auf dem
  Bau-Server installiert (Stand d23c7c0):
  - Pull-Request 328 (Issue 325 Teil 1, Rollenwort statt festem Personennamen in
    `to_spawn/ticketstand.py`, `to_spawn/melder.py`, `SKILL.md`; main d23c7c0). Volle Suite 3395
    bestanden, 81 übersprungen, 18 xfailed. Issue 325 bleibt offen (Teil 2 in Arbeit: `aufpasser.py`,
    `capo.py`, `hausmeister.py`, `leitstand.py`, `abschluss_paket.py`).
  - Issue 326 geschlossen: Rot-Probe des Review-Werkzeugs meldet bei reinem Kommentar-/Test-Diff
    „nicht beurteilbar“ statt eines harten Befunds (Fix liegt im Review-Ordner des Benutzers, nicht in
    diesem Repo).
  - Neues Issue 329: Review-Werkzeug hat keine versionierte Quelle.
  - Unverändert offen: Pull-Request 317 (274 P1) wartet auf die Zusatz-Datei im App-Repo.

- **2026-11-23 09:44 (Server-Uhr, main 599782c):** Seit dem Eintrag von 09:14 gemergt und auf dem
  Bau-Server installiert (Stand 599782c):
  - Pull-Request 321 (Issue 282 P3, Abschluss-Session wird nicht mehr abgewürgt, Wiederanlauf-Frage in
    der Lage; main e95b154). Unabhängige Prüfung ohne harten Befund, Review grün, volle Suite 3384
    bestanden, 81 übersprungen, 18 xfailed. Weiche Punkte als Kommentar in Issue 282, Pflicht für 282 P2.
    Teil C (Tab unter Windows schließt bei Exit 0) nicht gebaut, nur unter Windows prüfbar.
  - Pull-Request 322 (Issue 320, eine Commit-Suche `gh.letzter_commit`, Git-Zeitgrenzen nur in `gh.py`,
    Ersatz für gh nur im Test; main 4da3992). Volle Suite 3394 bestanden, 81 übersprungen, 18 xfailed.
    Issue 320 geschlossen.
  - Pull-Request 324 (Issue 323, Rollenwort statt festem Personennamen in `waechter_takt.PROMPT`,
    `skripte/wache.py`, `anleitung.MINDSET`; main 599782c). Volle Suite 3395 bestanden, 81 übersprungen,
    18 xfailed. Issue 323 geschlossen, Rest-Stellen in Issue 325.
  - Offen: Pull-Request 317 (274 P1) fertig geprüft (Prüfer, Review, Suite grün auf a930ac3), wartet auf
    die Zusatz-Datei im nutzenden Repo (`docs/agents/bau_zusatz.md`); vor dem Merge main neu einmergen
    und Suite wiederholen. Issue 273 ist durch 317 erledigt (nach Merge kommentieren + schließen).
  - Neue Issues: 325 (Namens-Reste), 326 (rot_probe des Review-Dirigenten meldet falsch „Test kann nicht
    rot werden“).
  - Nächste Schritte: 317 mergen → 282 P2 (Startstelle fragt den Baustein) → 274 P2.
  - Unverändert: Abnahme M1/M2 und die Test-Spec starten erst auf ausdrückliches Wort des Nutzers.

- **2026-11-23 09:14 (Server-Uhr, main aa123a0):** Seit dem Eintrag von 08:43 gemergt und auf dem
  Bau-Server installiert (Stand aa123a0): Pull-Request 318 (Issue 282 P1, neuer Baustein Wiederanlauf:
  `to_spawn/wiederanlauf.py`, `to_spawn/belege.py`, Tür `to_spawn.py ticket-lage <N>` meldet
  neu/fortsetzen/abschluss/nichts, bei Unklarheit fail-closed). Beleg: volle Suite 3358 bestanden.
  - In Arbeit: 282 P3 (Leiter beendet keine Session, der nur ein Abschluss-Schritt fehlt) · Pull-Request
    317 (274 P1) fertig geprüft bis auf Review + Suite, Merge wartet auf die Zusatz-Datei im nutzenden Repo.
  - Nächste Schritte: 282 P2 (Startstelle fragt den Baustein) → 274 P2.
  - Unverändert: Abnahme M1/M2 und die Test-Spec starten erst auf ausdrückliches Wort des Nutzers.

- **2026-11-23 08:43 (Server-Uhr, main d5e3806):** Seit dem Eintrag von 08:27 gemergt und auf dem
  Bau-Server installiert (Stand d5e3806): Pull-Request 310 (Issue 278 P3b, Aufseher liest und
  beantwortet den Entscheidungs-Briefkasten). Geschlossen: Issue 278 (Entscheidungs-Befugnis:
  Befugnis-Klassen + Entscheidungs-Briefkasten, alle Pakete drin) und Issue 279 (Rest = Issue 313,
  Lauf-Test `spawn_local.ps1` unter Windows). Belege: unabhängiger Prüfer ohne harte Befunde, Review
  grün, volle Suite 3330 bestanden, 81 übersprungen, 18 xfailed.
  - Pflicht-Liste vor der Test-Spec: 275 ✅ · 276 ✅ · 277 ✅ · 278 ✅ · 279 ✅ · 281 ✅ · 285 ✅ · 274
    (mit 273): P1 (Start-Text nur aus dem Skill + Repo-Zusatz) wird gebaut, Merge erst, wenn das
    Ziel-Repo seine Zusatz-Datei hat · 282 offen · 283 baut eine Windows-Session. Weiche Reste aus
    278 stehen in Issue 312 und Issue 315.
  - Nächste Schritte: 274 P1 bauen → 274 P2–P3 → 282.
  - Unverändert: Abnahme M1/M2 und die Test-Spec starten erst auf ausdrückliches Wort des Nutzers.

- **2026-11-23 08:27 (Server-Uhr, main 2db133a):** Seit dem Eintrag von 07:36 gemergt und auf dem
  Bau-Server installiert (Stand 2db133a): Pull-Request 307 (Issue 279 P6b, Anzeige) und
  Pull-Request 311 (Issue 281: Start-Text wird erst beim Loslassen gebaut; volle Suite 3306 bestanden,
  Rot-Probe belegt). Geschlossen: Issue 279 (alle Pakete P1–P6b drin, Abschluss-Prüfung im Issue; Rest
  = Issue 313, Lauf-Test `spawn_local.ps1` unter Windows) und Issue 281.
  - Pflicht-Liste vor der Test-Spec: 275 ✅ · 276 ✅ · 277 ✅ · 279 ✅ · 281 ✅ · 285 ✅ · 278: Pakete
    P1, P2, P3a gemergt, P3b = Pull-Request 310 in zweiter Fix-Runde (1 Test rot, 6 Punkte) · 274
    (mit 273): Bestandsaufnahme fertig, Pakete P1–P3 geschnitten, noch nichts gebaut, P1 als Nächstes,
    weiche Punkte aus 281 stehen als Kommentar in Issue 274 · 282 offen · 283 baut eine Windows-Session.
  - Nächste Schritte: 310 fertig prüfen und mergen → Issue 278 schließen → 274 P1–P3 → 282.
  - Unverändert: Abnahme M1/M2 und die Test-Spec starten erst auf ausdrückliches Wort des Nutzers.

- **2026-11-23 07:36 (Server-Uhr, main 056fac1):** Seit dem Eintrag von 04:49 gemergt und auf dem
  Bau-Server installiert (Stand 056fac1): Issue 276 (Bau-Session löst sich ab eigenem Handoff selbst
  ab), Issue 277 (Startzeile ohne .bashrc/.venv), Issue 279 Pakete P1–P5 und P6a (ein Baustein
  `ticket_zustand` sagt für jede Stelle, wie weit ein Ticket ist; fertig gebautes Ticket mit stillem
  Fenster wird beendet statt neu gestartet), Issue 278 Pakete P1, P2 und P3a (Befugnis-Klassen im
  Start-Auftrag, Entscheidungs-Briefkasten, kaputte Briefkasten-Datei sperrt sichtbar statt still).
  Letzte Merges: Pull-Request 306 → 03adf48, Pull-Request 308 → 056fac1; volle Suite auf dem Stand vor
  dem Merge 308: 3276 bestanden, 81 übersprungen, 18 xfailed, 0 rot.
  - In Arbeit: Pull-Request 307 (Issue 279 P6b, Anzeige im Leitstand; Fix-Runde nach Prüfer-Befund)
    und Issue 278 P3b (Aufseher liest und beantwortet den Briefkasten).
  - Danach: Issue 274 (mit 273 + 281), 282. Windows-Tickets 283 + 285 baut eine PC-Session.
  - Test-Durchlauf im Probe-Repo startet nur auf ausdrückliches Wort des Nutzers.

- **2026-11-23 04:49 (Server-Uhr, main d8df77a):** Pull-Request 294 (Issue 275) gemergt und auf dem
  Bau-Server installiert: Der Aufseher-Check kennt `staging:fertig`, öffnet je Ticket und Verstoß nur
  einmal wieder, und `staging:fertig` startet keinen Bau.
  - Offen, kurz vor Merge: Pull-Request 295 (Issue 276, Bau-Session löst sich ab eigenem Handoff selbst
    ab) und 293 (Issue 277, Startzeile ohne .bashrc/.venv).
  - In Arbeit: Issue 279 (Baustein Ticket-Zustand je Stelle, Paket P1), danach Issue 278
    (Entscheidungs-Befugnis im Start-Auftrag), 274 (mit 273 + 281), 282. Windows-Tickets 283 + 285
    baut eine PC-Session.
  - Test-Durchlauf im Probe-Repo startet nur auf ausdrückliches Wort des Nutzers.

- **22.11.2026 16:10 (Server-Uhr, main 8acfed7):** Seit dem Haltepunkt 760ed4f gemergt: Pull-Request 265
  (Issue 245, Zeilenenden in den Prüf-Regeln) → 5af043a · Pull-Request 233 (Hausmeister unter Windows,
  Issue 68 bleibt offen) → f134819 · Pull-Request 268 (Issue 267: Warte-Prüfung der Bau-Session fragt
  dieselbe Regel wie die Start-Schranke, neuer Befehl `to_spawn.py blocker <N>`) → 8acfed7.
  Auf dem Bau-Server installiert: 8acfed7, volle Suite 2794 bestanden, 75 übersprungen, 18 xfailed, 0 rot.
  Offen: Issue 269 (Folge zu 267: Vorfall `blocker_falsch_gewartet`, Aufseher-Test schärfen) · Pull-Request 250
  (Merge-Sperre, Fix-Runde 3) · Pull-Request 255 (Review am PC) · M1 Probe-Spec erst nach ausdrücklichem
  Wort des Auftraggebers. Nächster Schritt: Beobachtungs-Lauf an einem echten Fall für Issue 267
  (Abnahme „echter Lauf“ von 267 steht noch aus).

- **Haltepunkt 22.11.2026 04:05 (Server-Uhr, main b4be778):** Der Bau ruht hier, bis der Auftraggeber „weiter“ sagt.
  Seit dem letzten Eintrag gemergt: 237 (Review-Hook im Repo + Installation, Teil A von Issue 214; Issue 214 bleibt für
  Teil B offen) und 264 (Doku).
  Auf dem Bau-Server installiert: b4be778, volle Suite 2779 bestanden, 66 übersprungen, 18 xfailed, 0 rot.
  Review-Hook: ok (Dateien schon aktuell, nichts überschrieben).
  Offen liegen geblieben, mit Grund:
  - 250 (Issue 149 Rest, Kopf 2b0377c): unabhängiger Nach-Prüfer meldet „Hartes: JA“. Die Test-Kennung aus dem JUnit-`file`
    ist die Definitionsdatei; ein Zwilling, der die Testfunktion importiert, plus Füller-Test ergibt „wackler“ mit Exit 0.
    Merge-Sperre als Kommentar. Nächster Schritt: Fix-Runde 3 — ein zweiter voller Lauf schreibt die genaue
    pytest-Knoten-Kennung und das Ergebnis über ein eigenes kleines Plugin statt JUnit-Rückrechnung; Mutations-Abdeckung
    verbreitern; danach neuer Nach-Prüfer.
  - 233 (Hausmeister unter Windows): Review „Hartes: nein“, es fehlt nur der Linux-Lauf und der Merge.
  - 265 (Issue 245): Review „Hartes: nein“, es fehlt der Linux-Lauf von `tests/test_pruef_auswahl_3b.py` und der Merge.
  - 255 (Issue 251, Deploy-Wache ohne `pgrep`): noch im Review, nicht mergen.
  Neue Issues: 260 (volle Suiten nebeneinander wackeln → Suiten nacheinander fahren), 261 (Maschinen-Liste: gesetztes
  `ticket_repo` nicht änderbar; Hausmeister: 4 gelöscht, 173 bleiben).
  Weiterbauen nach dem Haltepunkt, in dieser Reihenfolge:
  1. 233 und 265 mergen (je Linux-Lauf).
  2. Fix-Runde 3 für 250.
  3. Issue 214 Teil B.
  4. Hausmeister-Reihe: 241, 242, 261, 234, 177, 215, 225, 163, 129, 179, 178, 247, 248, 252–254.
  5. Prüf-Reihe nach Merge von 250: 204, 218, 238, 246, 224, 104.

- **2026-11-22 03:50 (Server-Uhr, main 9dc4f72):** Seit dem letzten Eintrag gemergt (Pull-Requests 239, 240, 244, 249, 256).
  Auf dem Bau-Server installiert: c454545, volle Suite 2754 bestanden, 0 rot.
  - 239: Doku-Pull-Request (Stand 85).
  - 240: PC, Prüftor-Tests laufen unter Windows (P7f).
  - 244: Issue 235 Teil B. Der Hausmeister räumt Skill-Helfer-Ordner über das richtige Ticket-Repo.
    Erster Lauf nach der Installation: 4 gelöscht, 173 bleiben, 0 Fehler.
  - 249: Messung `doku_pfade` fürs Skill-Repo, Ergebnis „keine sichere Abkürzung“.
  - 256: PC, Windows-Ausgabe-Kodierung und Einzel-Tests (P7f).
  Offen im Pull-Request (alle vier mergbar):
  - 237 (Issue 214 Teil A, Review-Hook ins Repo): Merge-Lauf läuft.
  - 250 (Issue 149 Rest): Fix-Runde 2, Nach-Prüfer läuft.
  - 233 (Hausmeister unter Windows): Review „Hartes: nein“, wartet auf Linux-Lauf und Merge.
  - 255 (Deploy-Wache ohne `pgrep`, Issue 251): im Review, noch nicht mergen.
  Neue Folge-Issues: 252, 253, 254 (Folge aus 235), 257, 258, 259 (Windows), 260 (volle Suite wackelt bei
  parallelen Läufen), 261 (Maschinen-Liste: gesetztes `ticket_repo` nicht änderbar).
  Als Nächstes:
  - Hausmeister-Reihe: 241, 242, 261, 234, 177, 215, 225, 163, 129, 179, 178, 247, 248, 252–254.
  - Prüf-Reihe nach Merge 250: 204, 218, 238, 246, 224, 104, 245.
  - Issue 214 Teil B nach Merge 237.

- **2026-11-22 02:09 (Server-Uhr, main 9c7cda9):** Seit dem letzten Eintrag gemergt (Pull-Requests 231, 228, 236).
  Auf dem Bau-Server installiert: 0dc03ae, volle Suite 2704 passed / 0 failed / 66 skipped.
  - 231 (4be7f68): PC, die Windows-Tests laufen. Issue 68 bleibt offen.
  - 228 (9c7cda9): Teil von Issue 218. Die Schmutz-Erkennung steht an einer Stelle, die Regel „Werkzeug-Rest“
    nur in `cache.ist_werkzeug_rest`. Issue 218 bleibt offen.
  - 236: Doku-Pull-Request (Stand 84).
  In Arbeit:
  - Pull-Request 237 (Issue 214 Teil A, Review-Hook ins Repo + Installation): Fix-Runde 1 nach Prüfer-Urteil
    „Hartes: ja“ (zwei Sammel-Tests rot).
  - Issue 235 Teil B im Bau: Der Hausmeister räumt Helfer-Ordner des Skill-Repos nicht. Echter Lauf: 0 gelöscht,
    174 bleiben, 1 Fehler → M14 offen.
  - Paket `pruefen.doku_pfade` (Zweig `l3/doku-pfade-skill-repo`) im Bau.
  - Pull-Request 233 (PC) wartet auf Review.
  Neue Folge-Issues: 238 (Muster dürfen nicht über Ordnergrenzen greifen), 234, 235, 232.
  **Was als Nächstes kommt (in dieser Reihenfolge):**
  1. 237: Nach-Prüfer, Merge, dann Issue 214 Teil B.
  2. 235: Prüfer, Merge, Installation und echter Hausmeister-Lauf.
  3. Hausmeister-Reihe: Folge-Issues aus 235, 234, 177, 215, 225, 163, 129, 179, 178.
  4. Prüf-Reihe: doku_pfade, Rest 149, 204, 218 (Punkte 2–5), 238, 224, 104.
  5. Issue 232.
  6. Issue 190.
  7. Issue 216.

- **2026-11-22 01:53 (Server-Uhr, main 0dc03ae):** Seit dem letzten Eintrag gemergt (Pull-Requests 221, 222, 223, 227, 229, 230).
  Volle Suite zuletzt 2697 passed / 0 failed / 66 skipped auf 8592045 (main grün).
  - 221 (379f6f2): Die Leitstand-Tests laufen unter Windows grün (P7f).
  - 222 (1ae01fb): Issue 196 zu. Der Hausmeister erkennt eingetragene Arbeitsordner außerhalb der Wurzeln. Rest → Issue 225.
  - 223 (e65195e): Issue 184 zu. Cache-Reste des Prüf-Caches (aus 173) behoben. Rest → Issue 219 und 224.
  - 227 (8592045): Zwei rote main-Tests behoben (feste Namen, Start-Tür).
  - 229 (328c2b1): Die Starter-Tests laufen unter Windows grün (P7f).
  - 230 (0dc03ae): Issue 176 zu. Der Hausmeister bestimmt das Ticket-Repo je Ordner.
  Neu offen: 232 (Start-Tests laufen mit echtem Heimordner), 234 (Hausmeister: Zweig-Pfad prüft die Nummer nicht im
  eigenen Repo), 235 (Hausmeister räumt Helfer-Ordner des Skill-Repos nicht), dazu 225, 226, 224, 219, 218, 216, 215, 214, 212.
  **Was als Nächstes kommt (in dieser Reihenfolge):**
  1. Pull-Request 228 (Teil von Issue 218), in Fix-Runde nach Prüfer-Befund.
  2. Issue 214 in zwei Teilen: A Umzug `review-hook/` + Installation, B Verhalten.
  3. Prüf-Reihe: Rest Issue 149, 204, 218 (Punkte 2–5), 224, 104.
  4. Hausmeister-Reihe: 235 zuerst, dann 234, 177, 215, 225, 163, 129, 179, 178.
  5. Pull-Request 231 und 233 (Windows), warten auf den Prüf-Kommentar des PC.
  6. Issue 232 (eine Test-Welt für Start-Tests) nach 214 Teil B.
  7. Danach 190, 216.

- **2026-11-22 (main bd948ab, Stand bis Pull-Request 213):** Gemergt, je Pull-Request in Alltagsworten:
  - 194: Windows ruft bash nur noch an einer Stelle auf (Reste aus Review 181).
  - 209: Leitstand-Datei einheitlich formatiert, Verhalten unverändert.
  - 200: Tests für den Prozessbaum räumen ihre Kindprozesse hart ab, es bleiben keine Reste zurück.
  - 201: Die Tests für „startklar“ und „respawn“ laufen unter Windows grün.
  - 210: Die Konfig-Tests von Hausmeister und Aufpasser laufen unter Windows grün (P7f).
  - 208: Issue 169 zu. Der Probelauf der Sicherung lädt nichts mehr hoch, Zweige mit Übergaben
    werden nicht mehr angefasst (die Sicherung verweigert). Rest → Issue 215.
  - 207: Issue 101 zu. Die Prüfung läuft nur noch über die betroffenen Tests, mit Rückfall-Regeln
    R1–R3 auf den vollen Lauf. Rest → Issue 218.
  - 217: Roter Test auf main behoben (Attrappe in `test_pruef_doku_zu_weit_164` plattformneutral).
  - 211: nest, vorfall und inventur laufen unter Windows grün (P7f).
  - 213: Die Installer-Tests laufen unter Windows grün (P7f).
  Offen, laut `gh issue list`: 219 (Prüf-Cache, Treffer trotz wechselnder Laufausgaben, Rest aus 184),
  212 (CLI stürzt beim Import ohne Heimat-Ordner ab).
  **Was als Nächstes kommt (in dieser Reihenfolge):**
  1. In Arbeit: Issue 196 (Zweig `l4/196-worktrees-ausserhalb`: eingetragene Arbeitsordner außerhalb
     des konfigurierten Ordners erkennen) und Issue 184 (Zweig `l3/184-cache-reste`).
  2. Hausmeister-Reihe, nacheinander, weil alle dieselbe Datei `to_spawn/hausmeister.py` ändern:
     176 (vorgezogen: Hausmeister räumt auf dem Bau-Server nichts, solange das Ticket-Repo nicht
     bestimmbar ist), 177, 215, 163, 129, 179, 178.
  3. Prüf-Reihe, nacheinander (`to_spawn/pruef/`): `pruefen.doku_pfade` eintragen und messen, Rest
     Issue 149, 204, 218, 104.
  4. Issue 190 ist entschieden: einmal das ganze Repo mit `ruff.toml` (Zeilenlänge 120) formatieren
     plus Prüf-Test. Erst bauen, wenn kein Bau-Zweig offen ist.
  5. Neue Issues: 214 (Stop-Hook verlangt nach reinem Merge von main nur die Konflikt-Dateien zum
     Review) und 216 (`einrichten --pruefen` meldet „nicht eingerichtet“ trotz Soll-Stand).
  6. Windows-Seite: Issue 68 und 197 (baut der PC).
  7. Issue 96: Das App-Repo nutzt die Kette aus dem Skill.
  Issue 186 wird nicht gebaut.

- **Nachtrag (main 989ddbf):** Gemergt: Pull-Request 187 (Issue 164 zu: Erlaubnisliste der Sofort-Stufe in
  `to_spawn/pruef/sofort.py`; Folge-Issue 204 offen), 198 (kleiner Schnitt aus Issue 136: Temp-Ordner der
  Testläufe weg von der RAM-Platte; Issue 136 bleibt offen: Hausmeister räumt Temp-Reste geschlossener
  Tickets), 199 (Teil von Issue 149: Fortsetzen-Nachweis im Aufpasser ohne Wackler unter Last; Issue 149
  bleibt offen: Befunde 1–4 und `PYTEST_ADDOPTS` in `to_spawn/pruef/`), 202 (Issue 191 Teil 1: Startstelle
  meldet „Stand unbekannt“ nach 3 Versuchen mit Exit 2, Zeitgrenze für gh, Prüf-Test erkennt den tmux-Weg;
  Issue 191 bleibt offen: Befunde 4–6), 205 (Issue 195 zu: `_probe_frei` im Leitstand wertet die
  Fehlernummer statt der Fehlerklasse aus).
  Offen / in Arbeit: Pull-Request 194 (bash-Tür Windows, Fix-Runde 1 in Prüfung), Issue 101 (nur betroffene
  Tests plus Rückfall-Regeln R1–R3, im Bau), Pull-Requests 200 und 201 (Windows-Reste, baut der PC).
  Als Nächstes: 184, `pruefen.doku_pfade`, Rest 149, 204, 104, danach 169, 196, 177.

- **2026-11-22 00:05 (Server-Uhr, main ffddfd0):** Gemergt: Pull-Request 185 (main aac4c1f, Issue 165 zu:
  nicht öffenbare Sperr-Datei ist ein Fehler statt „belegt“; Folge-Issue 195), 192 (main cf4baca, Issue 188
  bleibt offen: `coproc` und Windows-Präfix fehlen noch), 193 (main ffddfd0, Issue 97 zu: eine Start-Stelle
  mit Sperre, geschlossene Tickets starten nie; Rest in Issue 191).
  Offen / in Arbeit: Pull-Request 202 (Issue 191 Teil 1), 194 (bash-Tür, Fix-Runde läuft), 187 (Issue 164,
  Fix-Runde 2), 198 (kleiner Schnitt aus Issue 136), 199 (Issue 149).

- **2026-11-21 23:40 (Server-Uhr, main 96575a8):** Gemergt: Pull-Request 173 (main b2fe884, Issue 102 zu:
  Prüf-Cache; Folge-Issue 184), 181 (main d8e9b0d, P7f: eine Stelle wählt die bash, Windows-Teil vom PC;
  Folge-Issue 186), 183 (main 96575a8, Issue 175 zu: Git-Sperre, vier Umgehungen; Folge-Issue 188).
  Läuft: Pull-Request 185 (Issue 165), 187 (Issue 164), 148 (Issue 136); Bau zu Issues 97, 149, 188.

- **2026-11-21 23:30 (Server-Uhr, main 877bbf2):** Gemergt: Pull-Request 134 (Issue 93 zu: Test-Attrappen
  plattformneutral über `conftest.attrappe`, Prüf-Test `tests/test_attrappen_plattformneutral_93.py`), 180
  (main wieder grün: feste Namen; `OEFFENTLICHE_SLUGS` steht in `to_spawn/vorgaben.py`, Verhalten
  unverändert), 171 (Issue 95: Sperre Teil b, alle Sperren laufen über `to_spawn/sperre.py`, einzige Datei
  mit `fcntl`/`msvcrt`). Offen im Pull-Request: 148 (Issue 136), 173 (Issue 102). Im Bau: Issues 175, 149,
  165, 97. Neu: Issues 175–179.

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

1. **Lesen** (in dieser Reihenfolge): `docs/LASTENHEFT.md` → `docs/PLAN_L1_usb_stick.md` (Design,
   Etappen, Stand) → `docs/ABNAHME_L1.md`.
2. **Nachsehen, ob noch etwas läuft.** Auf den Bau-Server gelangt man über den eigenen SSH-Alias aus
   `~/.ssh/config` (`ssh <bau-server-alias>`; Adresse und Zugangsdaten liegen nie in diesem öffentlichen
   Repo, sondern nur im privaten Notiz-Tresor `projects/bau-system/WIEDEREINSTIEG_maschinen.md`).
   Bau-Server: `tmux ls` und `tmux list-windows -t leit-bausystem` (höchste `leit <N>` führt).
   PC (Windows): ein Terminal-Tab „Ober-Aufsicht“. Läuft eine Kette, nicht eine zweite starten,
   sondern dieser zuarbeiten.
3. **Skill installieren (frische Maschine) und Wirklichkeit gegen Dokumente abgleichen:**
   - **Voraussetzungen:** `git`, Python 3.12+, `pytest`, angemeldetes `gh`, `claude`; unter Windows
     zusätzlich PowerShell 7 (`pwsh`).
   - **Erstinstallation**, falls `~/.claude/skills/to-spawn` noch nicht vorhanden ist:
     - Linux: `git clone https://github.com/Shavy72/claude-skill-to-spawn ~/claude-skill-to-spawn && bash ~/claude-skill-to-spawn/install.sh`
     - Windows (PowerShell 7): `git clone https://github.com/Shavy72/claude-skill-to-spawn "$HOME\claude-skill-to-spawn"` und `pwsh -File "$HOME\claude-skill-to-spawn\install.ps1"` (dauerhafter Klon-Ordner statt Temp, damit `STAND.json` den Klon für spätere Updates und Zweig-Uploads kennt).
   - **Abgleich:** `gh pr list --state open`, `gh issue list`, `git log origin/main -15`, installierter
     Skill-Stand auf der Maschine (`python ~/.claude/skills/to-spawn/to_spawn.py einrichten --pruefen`,
     unter Windows bei Anführungszeichen oder `cmd.exe` mit `"$HOME\.claude\skills\to-spawn\to_spawn.py"`
     statt `"~/..."`). Meldet `--pruefen` `skill_aktuell: weicht_ab` (bzw. `grosse_luecke` beim
     schreibenden `einrichten`), ist der Skill veraltet: im aktiven Skill-Klon `git pull` und danach
     `install.sh` (Linux) bzw. `install.ps1` (Windows) ausführen. Abweichungen zuerst im Abschnitt
     „3. Stand“ festhalten.
4. **Kette starten, falls keine läuft.** Auf eingerichteten Maschinen über `~/.claude/bin/claude-frisch.sh`
   (Linux) bzw. `"$HOME\.claude\bin\claude-frisch.ps1"` (Windows), nie `claude` mit geerbter Umgebung. Die
   Start-Skripte sind maschinen-lokal und liegen außerhalb dieses Repos (genaue Befehle je Maschine im
   Notiz-Tresor `projects/bau-system/WIEDEREINSTIEG_maschinen.md`). **Rückfall auf einer frischen
   Maschine ohne Start-Skript:** ein neues Terminal außerhalb jeder laufenden Claude-Session öffnen
   (oder geerbte Sitzungs-Variablen wie `CLAUDECODE`, `CLAUDE_CODE_SESSION_*`,
   `CLAUDE_CODE_CHILD_SESSION` vor dem Start entfernen — vollständige Liste in `to_spawn/sitzung_env.py`;
   unter PowerShell z. B. `Remove-Item Env:CLAUDECODE,Env:CLAUDE_CODE_CHILD_SESSION -ErrorAction SilentlyContinue; Get-ChildItem Env:CLAUDE_CODE_SESSION_* | Remove-Item`)
   und `claude` dort frisch starten. Der Startauftrag lautet:
   „Lies `docs/WIEDEREINSTIEG.md` im Skill-Repo und verfolge das Ziel 1:1 weiter, bis es erreicht
   ist.“ Gibt es noch eine Übergabe-Datei, wird sie zusätzlich gelesen; sie wächst fortlaufend, es genügen
   die letzten 200 Zeilen (`tail -200`, unter PowerShell `Get-Content <Datei> -Tail 200`).
5. **Weiterbauen in der Reihenfolge der Prioritäten.** Offene Pull-Requests zuerst fertig machen
   (Review, Linux- und Windows-Lauf, Merge nach der Merge-Regel in Abschnitt 5), dann offene
   System-Anomalien aus dem laufenden Probe-Lauf (L10), dann die offenen Issues der niedrigsten Etappe
   (Schnitt und Reihenfolge in `docs/PLAN_L1_usb_stick.md`, Sammel-Issues: 49 = Etappe 1, 50 = Etappe 2,
   51 = Etappe 3, 52 = Etappe 4, 53 = Etappe 5), dann die erste M-Zeile ohne Beleg in `docs/ABNAHME_L1.md`.

## 5. Regeln, die immer gelten

- Keine Fragen an den Menschen. Entscheidung und Grund ins Entscheidungs-Log, weiterbauen (L1, L2).
  Echte Sperre (kein Zugriff, Secret fehlt): Eintrag im Entscheidungs-Log, Issue anlegen, mit einem
  anderen Paket weiterbauen.
- Der Auftrag wird bei jeder Ablösung im Wortlaut weitergegeben, nie als Zusammenfassung (L2).
- Erst vergleichen und messen, dann bauen; bewährtes Werkzeug vor Eigenbau (L3).
- Aufräumen, RAM, Platte, Neustarts landen nie beim Menschen (L4).
- Kein Wissen nur im Chat oder nur auf einer Maschine: Was zählt, steht hier im Repo, als Issue
  oder im Tresor (L5).
- Merge-Regel: Ein Pull-Request wird nur gemergt, wenn das Prüf-Urteil „Hartes: nein“ lautet. Dieses
  Urteil (keine blockierenden Befunde) erzeugt das Review-Werkzeug unter `review/`
  (Start über `python ~/.claude/review/review_route.py` / `review_beleg.py` / `rot_probe.py`; Ablauf in
  `review/VERTRAG.md`) bzw. eine unabhängige Prüfung. Vorher die letzten Kommentare am Pull-Request
  lesen; bittet dort jemand um Aufschub, nicht mergen. Vor dem Merge `main` in den Zweig hineinmergen
  und die Pflicht-Tests des Zweigs (`python -m pytest -q <betroffene Tests>`) laufen lassen. Kein
  `rebase`, kein `--force`. Befehl: `gh pr merge <N> --merge` (oder `--squash`, wenn Squash vereinbart
  ist).
- App-Live wird nie angefasst. Eine Leit-Kette je Maschine, eine Ober-Aufsicht am PC.

## 6. Glossar

- **Leit-Session / Leit-Kette (`leit <N>`):** Die führende Bau-Session auf dem Bau-Server
  (tmux-Sitzung `leit-bausystem`, Fenster `leit <N>`, höchste Nummer führt); schneidet Pakete, lässt
  bauen, prüft und mergt.
- **Ober-Aufsicht:** Begleitende Leit-Session am Windows-PC; hält die Richtung, prüft die
  Windows-Seite und führt Abnahmen mit.
- **Aufseher (`wache <S>`):** Begleit-Session je Spec; prüft Schnittstellen, Belege und Gates, baut
  selbst keinen Code.
- **Bau-Session (`bau <N>`):** Session für ein einzelnes Ticket; wartet bei Blockern außerhalb der
  KI-Session, baut dann im eigenen Worktree und schließt das Ticket.
- **Hausmeister (`aufraeumen`):** Automatisierter Aufräum- und Sicherungs-Lauf
  (`to_spawn/hausmeister.py`); räumt Worktrees, Zweige und Temp-Reste geschlossener Tickets und ruft
  `uebergabe_sichern.sichern` als Netz auf.
- **Maschinen-Liste (`~/.config/to-spawn/maschine.json`):** Maschinen-lokale Konfig-Datei (Repos,
  Hausmeister-Wurzeln, Sicherungs-Ziel und -Dateimuster); nie im öffentlichen Repo.
- **Probelauf (`--trocken` / `--dry-run`):** Meldet nur, was ein Befehl tun würde, ohne Dateien zu
  schreiben, zu löschen oder Zweige hochzuladen (z. B. `to_spawn.py sichern --trocken`,
  `aufraeumen --trocken`).
- **Tür `pruefen` / Gate:** Einheitlicher Prüf-Werkzeugkasten im Skill (`to_spawn.py pruefen <basis> <ziel>`,
  Etappe 3b); wählt selbst die kleinste ausreichende Prüf-Stufe, bevor ein Zweig weitergeht (Gate).
- **Wackler (Wackel-Test):** Test, der ohne Code-Änderung mal grün und mal rot läuft; wird von der
  Prüfung wiederholt und getrennt gemeldet, nie still übergangen.
- **Schlange (Deploy-Schlange):** Serielle Prüf- und Bereitstellungs-Reihe im App-Repo; Änderungen am
  Skill-Repo laufen davon getrennt.
- **Ereignis-Protokoll (`origin/protokoll`):** Gemeinsames, verwaistes Git-Protokoll für Bau-Ereignisse
  (Start, Übergabe, Schließen, Wiedereröffnen, Hindernis) unter `bau_log/<Ticket>.jsonl` auf dem
  Zweig `protokoll`; wird ausschließlich über die gesperrte Schreib-Tür `bau_log.schreibe()` mit
  lokalem Puffer unter `~/.local/state/to-spawn/protokoll` (nie im Worktree) gebündelt per
  Git-Plumbing hochgeladen, damit beide Maschinen denselben Stand ohne Gate-/Staging-Auslösung sehen.
- **App-Repo / App-Live:** Ein nutzendes Anwendungs-Repo (erstes Beispiel: DuoPlus); dessen
  Produktivbetrieb (`App-Live`) wird im autonomen Lauf nie angefasst.
- **Probe-Varianten (`-mit` / `-ohne`):** Zwei Durchläufe im Probe-Repo — einmal mit bereits
  eingerichteter Ausgangslage (`-mit`), einmal ab leerem Ausgangsstand (`-ohne`).
- **„Hartes: nein“:** Prüf-Urteil ohne blockierende Befunde (aus `review/` bzw. unabhängiger Prüfung),
  Voraussetzung der Merge-Regel.
- **`agy`:** Alternativer CLI-Runner (Antigravity CLI), über den Bau-Sessions wahlweise neben `claude`
  gestartet werden können (Issue 660).
- **Kürzel aus dem Verlauf (`P7c–P7f`, `3b`, Sammel-Issues 49–53):** Paket-Bezeichnungen aus Etappe 1
  (`P7c–P7f` = Windows-Test-Pakete zu Issue 68), Etappe 3 (`3b` = Bau des Prüf-Werkzeugkastens) und
  den Etappen-Sammel-Issues (49 = Etappe 1 bis 53 = Etappe 5).
