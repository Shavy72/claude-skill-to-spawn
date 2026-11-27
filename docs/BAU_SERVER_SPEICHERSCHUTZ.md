# Bau-Server: Speicherschutz, Swap 4 GB & Wiederaufnahme nach Neustart (#887)

Dokumentiert die Schutz-Architektur gegen Speichermangel (OOM-Abstürze), die sichere
Verkleinerung des Swaps auf 4 GB auf einem bestehenden Bau-Server sowie die Abnahme
(Lasttest + Neustart-Probe) samt Messprotokoll-Vorlage.

## 1. Zwei-Schichten-Schutz

1. **Betriebssystem-Boden (Variante A, `nest/nest_server.sh` Abschnitt 5b):**
   - **`bau-sessions.slice`** (`nest/bau-sessions.slice`): gemeinsamer cgroup-Speicherdeckel
     für alle Bau-Sessions (`MemoryAccounting=yes`, `MemoryHigh=10G`, `MemoryMax=12G`,
     `ManagedOOMMemoryPressure=kill`, `ManagedOOMMemoryPressureLimit=60%`).
   - **`bau-gate.slice`** (`nest/bau-gate.slice`): eigener cgroup-Speicherbereich für das
     Deploy-Gate (`MemoryAccounting=yes`, `MemoryMax=6G`, bewusst **ohne**
     `ManagedOOMMemoryPressure=kill`), strikt getrennt von `bau-sessions.slice`.
   - **cgroup-Delegation**: `/etc/systemd/system/user@.service.d/delegate.conf` mit
     `Delegate=cpu cpuset io memory pids`, damit User-Slices Speichergrenzen durchsetzen.
   - **`systemd-oomd`** (`/etc/systemd/oomd.conf.d/bau.conf`,
     `DefaultMemoryPressureDurationSec=20s`): greift bereits bei Thrashing und beendet
     gezielt nur Prozesse in `bau-sessions.slice`.
   - **Geschützte Dienste**: `ssh.service` (`/etc/systemd/system/ssh.service.d/oom.conf`)
     und `tmux-bau.service` laufen mit `OOMScoreAdjust=-900` (`tmux-bau.service` mit
     `OOMPolicy=continue`, `exit-empty off`).
   - **Swap auf 4 GB**: verhindert minutenlanges Zappeln/Thrashing bei 16 GB Swap.
   - **Temp-Ordner auf Platte**: `to_spawn/temp_ordner.py` legt `TMPDIR` auf die Platte
     statt in die RAM-Platte.
2. **Starter- und Aufpasser-Schicht (Variante B):**
   - `to_spawn/speicher.py`: prüft `MemAvailable`, zählt nur arbeitende Sessions (stille
     Sessions über `~/.claude/sessions/<pid>.json` ausgenommen), vergibt Plätze unter
     Datei-Sperre, wartet gestaffelt (`auf_platz_warten`), setzt für Bau-Sessions das
     Slice-Präfix (`slice_praefix` → `systemd-run --user --scope --slice=bau-sessions.slice … choom -n 300 --`)
     und fürs Gate `gate_slice_praefix` (`--slice=bau-gate.slice`).
   - `to_spawn/startstelle.py` + `to_spawn/ticket_zustand.py`: Pause-Guard verweigert Start
     und Fortsetzung bei Label `bau:pause` oder `wontfix` (auch nach Wartezeiten).
   - `to_spawn/aufpasser.py` + `to_spawn/hausmeister.py`: schließen fertige Sessions und
     räumen alte Worktrees, Temp-Ordner und Docker-Reste auf.
3. **Wiederaufnahme nach Neustart:**
   - `to_spawn/wiederaufnahme.py` + `nest/bau-wiederaufnahme.service`: startet nach einem
     Neustart (via `loginctl enable-linger`) je offenem Spec-Klon mit `.to-spawn/sessions/`
     einmal den Aufseher (`wache <S>`, mit `--resume` wenn Transkript vorhanden, unter
     Beachtung von `auswahl` und `tmux_aufruf.sitzung_aufloesen`). Der Aufpasser-Cron
     nimmt anschließend die fehlenden `bau <N>`-Fenster gestaffelt mit `--resume` wieder auf.

## 2. Swap auf bestehendem Bau-Server auf 4 GB verkleinern

Nur ausführen, wenn auf dem Bau-Server gerade kein Live-/Staging-Deploy läuft und genug
freier RAM vorhanden ist (so viel frei wie aktuell ausgelagerter Swap belegt):

```bash
# 1. Ausgangsstand prüfen
free -m
swapon --show

# 2. Sicherstellen, dass MemAvailable > Swap-Used ist, dann alten Swap abschalten
sudo swapoff /swapfile

# 3. Neue 4-GB-Swapdatei anlegen und aktivieren
sudo rm -f /swapfile
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 4. /etc/fstab und neuen Stand prüfen (erwartet: ~4096 MiB)
grep '^/swapfile' /etc/fstab
free -m
swapon --show
```

## 3. cgroup-Slices, `systemd-oomd` und Wiederaufnahme einspielen & prüfen

Nach Merge des Skill-Stands entweder `nest/nest_server.sh` erneut als `root` ausführen
(idempotent) oder die Units gezielt einspielen:

```bash
# Als root: Delegation, SSH-OOM, systemd-oomd, Linger
sudo install -d -m 755 /etc/systemd/system/ssh.service.d /etc/systemd/system/user@.service.d /etc/systemd/oomd.conf.d
printf '[Service]\nOOMScoreAdjust=-900\n' | sudo tee /etc/systemd/system/ssh.service.d/oom.conf >/dev/null
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf >/dev/null
printf '[OOM]\nDefaultMemoryPressureDurationSec=20s\n' | sudo tee /etc/systemd/oomd.conf.d/bau.conf >/dev/null
sudo apt-get install -y systemd-oomd
sudo systemctl daemon-reload
sudo systemctl enable --now systemd-oomd.service
sudo loginctl enable-linger "$USER"

# Als Bau-Nutzer: User-Units installieren und laden
mkdir -p ~/.config/systemd/user
cp ~/.claude/skills/to-spawn/nest/bau-sessions.slice \
   ~/.claude/skills/to-spawn/nest/bau-gate.slice \
   ~/.claude/skills/to-spawn/nest/bau-wiederaufnahme.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable bau-wiederaufnahme.service

# Verifikation
systemctl show ssh -p OOMScoreAdjust
systemctl show tmux-bau.service -p OOMScoreAdjust -p OOMPolicy
systemctl is-active systemd-oomd.service
oomctl
systemctl --user show bau-sessions.slice -p MemoryHigh -p MemoryMax -p ManagedOOMMemoryPressure
systemctl --user show bau-gate.slice -p MemoryMax -p ManagedOOMMemoryPressure
python3 -m to_spawn.wiederaufnahme --dry-run
```

## 4. Abnahme: Lasttest + Neustart-Probe (Messprotokoll)

### Durchführung Lasttest

1. Messschleife von einem zweiten Terminal starten (alle 10 s: SSH-Antwortzeit, Staging-HTTP-Code,
   RAM/Swap, Slice-Belegung):
   ```bash
   while true; do
     ts="$(date -Is)"
     t0="$(date +%s%3N)"
     ssh -o ConnectTimeout=5 bau-server "free -m | awk '/Mem:|Swap:/{printf \"%s %s/%sMiB \", \$1, \$3, \$2}'"
     rc_ssh=$?
     t1="$(date +%s%3N)"
     http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$STAGING_URL" || echo 000)"
     echo "$ts ssh_rc=$rc_ssh ssh_ms=$((t1 - t0)) staging=$http_code"
     sleep 10
   done
   ```
2. Parallel auf dem Bau-Server Last in `bau-sessions.slice` erzeugen (Bau-Sessions + Testläufe,
   bis `MemoryHigh`/`MemoryMax` bzw. `systemd-oomd` greift) und gleichzeitig einen Gate-Lauf
   in `bau-gate.slice` starten.
3. Nach dem Lauf Kernel-/oomd-Log sichern:
   ```bash
   journalctl -u systemd-oomd --since "-30 min" --no-pager
   dmesg -T | grep -iE 'oom|killed process' | tail -n 30
   ```

**Erwartete Abnahmekriterien (Lasttest):**
- SSH antwortet in jedem 10-s-Takt in **< 5 s** (`ssh_rc=0`, `ssh_ms < 5000`).
- Staging liefert durchgehend **HTTP 200**.
- `systemd-oomd` beendet nur Prozesse in `bau-sessions.slice`.
- `ssh.service`, `tmux-bau.service`, Aufseher und Deploy-Gate (`bau-gate.slice`) überleben
  und das Gate läuft grün durch.

### Durchführung Neustart-Probe

1. Mindestens eine offene Spec mit aktivem `wache <S>`- und `bau <N>`-Fenster (mit
   `.to-spawn/sessions/*.json`) notieren.
2. Bau-Server kontrolliert neu starten (`sudo reboot` nach Freigabe, außerhalb laufender Deploys).
3. Nach dem Neustart prüfen:
   ```bash
   systemctl is-active tmux-bau.service systemd-oomd.service
   journalctl --user -u bau-wiederaufnahme.service -b --no-pager
   tmux list-sessions
   tmux list-windows -a
   ```
   Nach einem Aufpasser-Cron-Takt (oder manuellem Lauf `python3 ~/.claude/skills/to-spawn/skripte/aufpasser.py`)
   prüfen, dass die offenen `bau <N>`-Fenster gestaffelt mit `--resume` zurückgekehrt sind.

### Messprotokoll-Vorlage

| Feld | Soll | Gemessen (Datum/Stand eintragen) | Status |
| --- | --- | --- | --- |
| Swap-Größe (`free -m`) | 4096 MiB (4 GB) | | offen |
| `ssh.service` `OOMScoreAdjust` | `-900` | | offen |
| `tmux-bau.service` `OOMScoreAdjust` / `OOMPolicy` | `-900` / `continue` | | offen |
| `bau-sessions.slice` (`MemoryHigh` / `MemoryMax` / `ManagedOOMMemoryPressure`) | `10G` / `12G` / `kill` | | offen |
| `bau-gate.slice` (`MemoryMax`, getrennt) | `6G` (kein `kill`) | | offen |
| `systemd-oomd` aktiv (`oomctl`) | aktiv | | offen |
| Lasttest: max. SSH-Antwortzeit (alle 10 s) | `< 5000 ms` | | offen |
| Lasttest: Staging-HTTP-Status | durchgehend `200` | | offen |
| Lasttest: Ziel der OOM-/oomd-Eingriffe | nur `bau-sessions.slice` | | offen |
| Lasttest: Deploy-Gate in `bau-gate.slice` | läuft durch | | offen |
| Neustart-Probe: `bau-wiederaufnahme.service` | startet Aufseher mit `--resume` | | offen |
| Neustart-Probe: Aufpasser-Folgelauf | nimmt `bau <N>` gestaffelt mit `--resume` wieder auf | | offen |
