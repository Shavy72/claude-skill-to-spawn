# Klasse-2-Antwort vom Handy mit fälschungssicherem Beweis (#312)

## Anlass

Pull-Request 310 (#278 P3b) hatte einen Kommentar-Weg für Klasse-2-Fragen (Live, Kosten,
Konten echter Personen). Review und Prüfung zeigten eine grundlegende Lücke: Bau-Sessions
und Aufseher kommentieren mit dem GitHub-Konto des Besitzers — jede Session auf der
Maschine konnte ihre eigene Klasse-2-Frage selbst freigeben. Der Kommentar-Weg wurde
deshalb wieder entfernt; Klasse-2-Fragen ließen sich danach nur noch lokal im Terminal
(`nutzer-antwort`) bzw. über das lokale Live-Ja (`ja`) beantworten, nicht aber vom Handy.

## Bedrohungsmodell (Abnahme 1)

Als Angreifer gilt eine Session (Bau-Session oder Aufseher) mit **allen Rechten des
Besitzer-Kontos auf derselben Maschine**:

- Sie kann Umgebungsvariablen beliebig setzen oder löschen (`env -u TO_SPAWN_TICKET`,
  umgebogenes `HOME`/`USERPROFILE`/`PATH`).
- Sie kann alle Dateien lesen, die dem Besitzer-Konto zugänglich sind, sowie lokale
  Arbeitsbaum-Dateien und `.to-spawn/entscheidung-<N>.json` direkt überschreiben.
- Sie kann ein Schein-Terminal öffnen (`script -qc`), Python-Funktionen direkt importieren
  und mit dem GitHub-Konto des Besitzers Issue-Kommentare schreiben.

## Variantenvergleich vor dem Bau

### Variante (a): Asymmetrischer Beweis außerhalb der Maschine (Ed25519-Signatur, privater Schlüssel nur am Handy)

Der Nutzer erzeugt auf dem Handy ein Ed25519-Schlüsselpaar. Der **private Schlüssel**
verlässt das Handy nie und liegt niemals auf dem Bau-Server, dem PC oder im Repo. Auf der
Maschine bzw. auf dem geschützten Hauptzweig (`origin/<hauptzweig>:.to-spawn/config.json`
oder in einer für das Besitzer-Konto nur lesbaren System-Schlüsseldatei unter
`/etc/to-spawn/klasse2_schluessel.json` bzw. `ProgramData\to-spawn\klasse2_schluessel.json`)
liegt **ausschließlich der öffentliche Schlüssel**.

Jede Klasse-2-Frage erhält beim Einwerfen eine zufällige 128-Bit-`challenge` zusätzlich zur
`id`. Auf dem Handy signiert der Nutzer die kanonische Nutzlast mit:

- Protokoll-Domäne und Version (`to-spawn:klasse2:v1`),
- `ticket`, `id` und `challenge` (einmalige Frage-Bindung gegen Replay),
- `frage_sha256` (SHA-256 des exakten Fragetexts gegen Fragen-Tausch),
- `wahl`, `grund`, `ts` und `key_id` (Bindung der exakten Entscheidung).

Das kompakte einzeilige Beweis-Token (`TO-SPAWN-K2-V1.<payload_b64url>.<sig_b64url>`)
kann über beliebige Kanäle vom Handy zurückkommen (z. B. GitHub-App-Kommentar, Nachricht,
direkter Aufruf) und wird über `to_spawn.py beweis-antwort <N> --beweis <Token>` in den
Briefkasten geschrieben. Entscheidend: Sowohl beim **Schreiben** als auch bei jedem **Lesen**
(`briefkasten.lesen`, `briefkasten.offene`, `briefkasten.liste`) prüft das reine
Python-Modul `to_spawn/klasse2_beweis.py` (RFC 8032, nur Standardbibliothek, kein
Subprozess, kein `PATH`-Zugriff) die Ed25519-Signatur und alle Bindungen.

### Variante (b): Einmal-Code (OTP / geheimer Code)

Die Frage zeigt dem Nutzer einen Einmal-Code auf einem Kanal, den Sessions nicht lesen
sollen; nur mit diesem Code gilt die Antwort.

**Sicherheitslücken gegenüber dem Bedrohungsmodell:**

1. **Keine kryptographische Trennung auf der Maschine:** Wird der Einmal-Code (oder ein
   symmetrischer HMAC/TOTP-Schlüssel) auf der Bau-Maschine erzeugt oder dort zur Prüfung
   gespeichert, kann eine Session mit allen Rechten des Besitzer-Kontos das Geheimnis bzw.
   den Versandkanal mitlesen oder den lokal abgelegten Soll-Hash in der Briefkasten-Datei
   durch den Hash eines selbst gewählten Codes ersetzen.
2. **Fehlende Entscheidungs-Bindung:** Ein nackter Einmal-Code bindet kryptographisch weder
   an die gewählte Entscheidung (`wahl="nein"` vs. `wahl="ja"`) noch an den exakten
   Fragetext. Ein Angreifer, der den Code abfängt, könnte die Entscheidung umschreiben.
3. **Zusätzliche Infrastruktur bei Auslagerung:** Nur ein eigener, dauerhaft betriebener,
   von der Bau-Maschine getrennter OTP-Dienst könnte das Geheimnis fernhalten — das schafft
   Betriebsaufwand, Verfügbarkeits- und neue Angriffsflächen, ohne die starke
   Entscheidungs-Bindung von Variante (a) zu erreichen.

### Gegenüberstellung und Entscheidung

| Kriterium | Variante (a): Ed25519-Signatur (Handy-Schlüssel) | Variante (b): Einmal-Code |
|---|---|---|
| Privates Geheimnis auf der Bau-Maschine | **Nie** (nur öffentlicher Schlüssel) | Ja (Code/Hash/HMAC) oder eigener Server nötig |
| Schutz gegen Session mit Besitzer-Rechten | **Erfüllt** (Signatur mathematisch nicht fälschbar) | **Nicht erfüllt** bei lokalem Code/Hash/Secret |
| Schutz gegen umgebaute Umgebung (`env -u`, `PATH`, `HOME`) | **Erfüllt** (reines Python, keine Schlüssel-Env-Vars) | Schwach (Versand- und Prüfpfad angreifbar) |
| Schutz gegen direktes Überschreiben von `entscheidung-<N>.json` | **Erfüllt** (`lesen` prüft Signatur bei jedem Zugriff) | **Nicht erfüllt** (Hash/Status lokal änderbar) |
| Bindung an Ticket, Frage-Text, `id`, `challenge`, `wahl`, `grund` | **Vollständig** (Replay und Wahl-Tausch unmöglich) | Nein (nackter Code bindet keine Wahl) |
| Laufzeit Linux & Windows ohne Zusatzpakete | **Ja** (RFC 8032 in reiner Python-Standardbibliothek) | Ja, aber nur mit schwacher Sicherheit |

**Gewählte Variante: Variante (a) — asymmetrischer Ed25519-Beweis mit privatem Schlüssel
ausschließlich auf dem Handy.** Sie ist nachweislich die sicherere Lösung, erfüllt Abnahme 1
auch gegen direktes Dateischreiben und umgebaute Umgebungen und braucht keinen eigenen
Dienst.

## Bedienung (Handy-Ablauf, Abnahme 2)

1. **Einmalig einrichten:** Auf dem Handy ein Ed25519-Schlüsselpaar erzeugen (der private
   Schlüssel bleibt auf dem Handy). Den öffentlichen Schlüssel (`ed25519:<base64url>` oder
   OpenSSH `ssh-ed25519 <base64>`) auf dem geschützten Hauptzweig in `.to-spawn/config.json`
   unter `klasse_2.oeffentliche_schluessel` eintragen — oder noch stärker in die für das
   Besitzer-Konto nur lesbare System-Datei `/etc/to-spawn/klasse2_schluessel.json` (Linux)
   bzw. `C:\ProgramData\to-spawn\klasse2_schluessel.json` (Windows).
2. **Frage stellen:** Die Bau-Session wirft die Klasse-2-Frage wie gewohnt ein
   (`to_spawn.py frage <N> --klasse 2 …`) und wartet (`to_spawn.py antwort <N> --id <id>`
   liefert Exit 2 `NUTZER`).
3. **Prüf-Anforderung ans Handy:** Der Aufseher liest die Daten mit
   `to_spawn.py beweis-anfrage <N> --id <id>` und meldet Frage, Ticket, `id`, `challenge`
   und `frage_sha256` an den Nutzer.
4. **Am Handy signieren:** Der Nutzer prüft die Frage am Handy und signiert mit seinem
   privaten Ed25519-Schlüssel; das Ergebnis ist ein einzeiliges Token
   `TO-SPAWN-K2-V1.<payload_b64url>.<sig_b64url>`.
5. **Beweis einreichen:** Das Token wird über
   `to_spawn.py beweis-antwort <N> --beweis <Token>` (kein Terminal nötig, da der
   kryptographische Beweis selbst fälschungssicher ist) in den Briefkasten eingetragen.
6. **Weiterbauen:** `to_spawn.py antwort <N> --id <id>` prüft den gespeicherten Beweis
   erneut, liefert Exit 0 mit Wahl und Grund des Nutzers, und die wartende Bau-Session läuft
   weiter.

## Bewusste Grenzen (ehrlich)

- **Ohne eingetragenen öffentlichen Schlüssel** bleibt der bisherige lokale Weg
  (`nutzer-antwort` im Terminal / `ja` mit PreToolUse-Sperre) für bestehende Repos erhalten;
  `beweis-antwort` verweigert ohne Schlüssel immer fail-closed.
- **Schutz des öffentlichen Schlüssels:** Im Git-Repo wird `origin/<hauptzweig>:.to-spawn/config.json`
  gelesen, sodass Änderungen im Arbeitsbaum wirkungslos bleiben. Den stärksten Schutz gegen
  eine Session mit vollen Rechten des Besitzer-Kontos (einschließlich lokalem `git update-ref`)
  bietet die System-Schlüsseldatei (`/etc/to-spawn/klasse2_schluessel.json` mit Besitzer `root`,
  Modus `0644` bzw. unter Windows `ProgramData\to-spawn\klasse2_schluessel.json` mit
  Admin-Schreibrechten), die das Besitzer-Konto nur lesen, aber nicht ändern kann.
- **Unveränderter Prüfcode:** Die Garantie gilt gegenüber dem unveränderten Skill-Code
  (`to_spawn/briefkasten.py`, `to_spawn/klasse2_beweis.py`).
- **Echte Handy-Einrichtung / Live-Durchlauf:** Das reale Signier-Werkzeug auf dem Handy
  und der Live-End-to-End-Test mit echtem Konto sind bewusst nicht Teil dieses Bau-Pakets.
