"""Exit-Codes und Wartelisten-Pfad von ``skripte/spawn_srv.sh`` — eine Quelle (#604).

Alle Aufrufer von ``spawn_srv.sh`` (Skill-Befehl ``to_spawn/spawn.py``, Umzug
``to_spawn/umzug.py``, Nachstarter in ``skripte/wache.py`` und Aufpasser
``to_spawn/aufpasser.py``) holen Exit-Bedeutung und Wartelisten-Pfad von hier.
"""

from __future__ import annotations

from pathlib import Path

#: Alles gestartet, alles läuft schon, Probelauf oder „wartet auf Platz“ (Aufseher läuft).
EXIT_OK = 0
#: Arbeitsordner nicht auf Hauptzweig-Stand — nichts gestartet.
EXIT_ARBEITSSTAND = 1
#: Aufruf, Manifest, tmux oder Konfig fehlerhaft — nichts gestartet.
EXIT_AUFRUF = 2
#: Regularien-Weigerung — nichts gestartet.
EXIT_WEIGERUNG = 3
#: ``--umzug``: Ticket läuft auf dem Server bereits — Umzug abgebrochen.
EXIT_UMZUG_LAEUFT = 4
#: Speicher- oder Session-Obergrenze voll (kein laufender Aufseher) — Rest bleibt aus.
EXIT_SPEICHER_VOLL = 5
#: Speicherprüfung kaputt oder Hauptzweig nicht ermittelbar.
EXIT_PRUEFUNG_KAPUTT = 6
#: Platte voll (Start-Check des Hausmeisters) — Rest bleibt aus.
EXIT_PLATTE_VOLL = 7
#: tmux-Session ``spec-<S>`` gehört einem anderen Repo — nichts gestartet.
EXIT_FREMD_SITZUNG = 9
#: Start-Sperre je Spec belegt (anderer ``spawn_srv.sh``-Lauf aktiv) — später erneut, kein Fehler.
EXIT_SPERRE = 10

#: Einzige vollständige Übersicht der Exit-Codes von ``skripte/spawn_srv.sh``.
BESCHREIBUNG: dict[int, str] = {
    EXIT_OK: "gestartet oder wartet auf Platz (Aufseher startet nach)",
    EXIT_ARBEITSSTAND: "Arbeitsordner nicht auf Hauptzweig-Stand",
    EXIT_AUFRUF: "Aufruf, Manifest, tmux oder Konfig fehlerhaft",
    EXIT_WEIGERUNG: "Regularien-Weigerung",
    EXIT_UMZUG_LAEUFT: "Ticket läuft auf dem Server bereits (Umzug abgebrochen)",
    EXIT_SPEICHER_VOLL: "Speicher- oder Session-Obergrenze voll",
    EXIT_PRUEFUNG_KAPUTT: "Speicherprüfung kaputt oder Hauptzweig nicht ermittelbar",
    EXIT_PLATTE_VOLL: "Platte voll (Hausmeister-Start-Check)",
    EXIT_FREMD_SITZUNG: "tmux-Session gehört einem anderen Repo",
    EXIT_SPERRE: "Start-Sperre belegt (anderer Lauf aktiv) — später erneut",
}

#: Umgebungsvariable mit dem Pfad der Warteliste (``wartet-auf-platz-<S>``).
WARTE_ENV = "TO_SPAWN_WARTE_DATEI"
#: Zeilenpräfix für wartende ``--umzug``-Fenster in der Warte-Datei (``umzug <N> <ref>``).
UMZUG_PRAEFIX = "umzug"


def ist_spaeter_erneut(code: int) -> bool:
    """``True``, wenn der Exit-Code kein Fehler ist, sondern „später erneut“ bedeutet (Exit 10)."""
    return code == EXIT_SPERRE


def beschreibung(code: int) -> str:
    """Klartext-Beschreibung zu einem ``spawn_srv.sh``-Exit-Code."""
    return BESCHREIBUNG.get(code, f"unbekannter Exit {code}")


def warte_datei(repo: Path, spec: int | str) -> Path:
    """Kanonischer Pfad der Warteliste einer Spec (eine Quelle für spawn_srv, wache, aufpasser)."""
    return Path(repo) / ".to-spawn" / f"wartet-auf-platz-{spec}"


def warteliste_lesen(inhalt: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Zerlegt den Inhalt von ``wartet-auf-platz-<S>`` in normale Tickets und ``--umzug``-Einträge.

    Normale Zeilen enthalten Komma-getrennte Ticketnummern (z. B. ``901,902``).
    Umzug-Zeilen haben das Format ``umzug <ticket> <ref>`` (Ref = ``<branch>@<sha>:<pfad>``).
    """
    normal: list[str] = []
    umzuege: list[tuple[str, str]] = []
    gesehen_norm: set[str] = set()
    gesehen_umzug: set[str] = set()
    for roh in inhalt.splitlines():
        zeile = roh.strip()
        if not zeile:
            continue
        if zeile.startswith(f"{UMZUG_PRAEFIX} "):
            teile = zeile.split(None, 2)
            if len(teile) == 3 and teile[1] not in gesehen_umzug:
                gesehen_umzug.add(teile[1])
                umzuege.append((teile[1], teile[2].strip()))
            continue
        for teil in zeile.split(","):
            nummer = teil.strip()
            if nummer and nummer not in gesehen_norm:
                gesehen_norm.add(nummer)
                normal.append(nummer)
    return normal, umzuege
