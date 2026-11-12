"""Wiederaufnahme nach Server-Neustart (#1184): je offener Spec einmal den Aufseher.

Nach einem Neustart ist der tmux-Server weg, also sieht der Aufpasser-Cron keine
Sitzung ``spec-<S>`` mehr und stellt nichts wieder her. Dieses Modul legt je offener
Spec genau die Sitzung ``spec-<S>`` mit dem Fenster ``wache <S>`` an — mit
``--resume <id>``, wenn ``.to-spawn/sessions/wache-<S>.json`` samt Transkript liegt.
Alles Weitere bleibt beim Aufpasser (eine Stelle, keine Kopie): sein nächster Lauf
startet die fehlenden ``bau <N>``-Fenster mit ``--resume``, gestaffelt über
``speicher.zulassen`` und ohne Tickets mit Stopp-Label.

Offene Spec = Klon ``~/<name>-<S>`` mit ``.to-spawn/sessions/`` UND Spec-Issue ``#<S>``
laut ``gh`` offen. Gestartet wird über tmux mit ``sitzung_env.ohne_sitzung()`` —
Claude startet erst ``wache.py`` im Fenster.

Aufruf: ``python -m to_spawn.wiederaufnahme [--heim <ordner>] [--dry-run]``
(systemd-User-Unit ``nest/bau-wiederaufnahme.service``).
"""

from __future__ import annotations

import argparse
import logging
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from to_spawn import aufpasser, config, gh, sitzung_env, speicher

log = logging.getLogger("to_spawn.wiederaufnahme")

#: Spec-Klon: beliebiger Name, Bindestrich, Spec-Nummer (z. B. ``duoplus-1109``).
KLON_MUSTER = re.compile(r".+-(\d+)")


#: Wiederholungen der gh-Abfrage nach dem Boot (Netz/gh oft noch nicht bereit, #1184 S7).
GH_VERSUCHE = 5
GH_TAKT_S = 60.0


def offene_specs(
    heim: Path,
    ist_offen: Callable[[str, Path], bool | None] | None = None,
    *,
    versuche: int = 1,
    takt_s: float = GH_TAKT_S,
    schlafen: Callable[[float], object] = time.sleep,
    unlesbar: list[str] | None = None,
) -> list[tuple[str, Path]]:
    """``(spec, klon)`` je Klon unter ``heim`` mit ``.to-spawn/sessions/`` und offenem
    Spec-Issue. Nicht lesbares GitHub zählt als nicht offen (kein Blindstart); solche Specs
    werden bis zu ``versuche``-mal im Abstand ``takt_s`` erneut gefragt. Was danach noch
    unlesbar ist, landet in ``unlesbar`` (falls übergeben)."""
    pruefen = ist_offen or (lambda spec, klon: gh.ticket_offen(spec, cwd=klon))
    rest: list[tuple[str, Path]] = []
    for klon in sorted(heim.iterdir() if heim.is_dir() else []):
        treffer = KLON_MUSTER.fullmatch(klon.name)
        if treffer and (klon / ".to-spawn" / "sessions").is_dir():
            rest.append((treffer.group(1), klon))
    gefunden: list[tuple[str, Path]] = []
    for versuch in range(1, max(1, versuche) + 1):
        if versuch > 1:
            log.info(
                "GitHub nicht lesbar für Spec %s — Versuch %d/%d in %g s",
                ", ".join(f"#{s}" for s, _ in rest), versuch, versuche, takt_s,
            )  # fmt: skip
            schlafen(takt_s)
        offen_unklar: list[tuple[str, Path]] = []
        for spec, klon in rest:
            offen = pruefen(spec, klon)
            if offen is None:
                offen_unklar.append((spec, klon))
            elif offen:
                gefunden.append((spec, klon))
            else:
                log.info("Spec #%s (%s): Issue zu — nichts zu tun", spec, klon)
        rest = offen_unklar
        if not rest:
            break
    for spec, klon in rest:
        log.warning("Spec #%s (%s): GitHub nicht lesbar — übersprungen", spec, klon)
    if unlesbar is not None:
        unlesbar.extend(spec for spec, _ in rest)
    return sorted(gefunden, key=lambda t: t[1])


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=sitzung_env.ohne_sitzung(),
    )


def tmux_dienst_abwarten(warten_s: float = 120, takt_s: float = 5) -> None:
    """Gibt es ``tmux-bau.service`` (Nest, OOM-Schutz), erst dessen Server abwarten —
    sonst legte ``new-session`` einen eigenen, ungeschützten tmux-Server an."""
    ende = time.monotonic() + warten_s
    while True:
        try:
            rc = subprocess.run(
                ["systemctl", "is-active", "--quiet", "tmux-bau.service"], check=False
            ).returncode
        except OSError:
            return  # kein systemd: nichts abzuwarten
        if rc == 0:
            return
        if rc == 4:  # Unit unbekannt (Server ohne Nest-Dienst)
            return
        if time.monotonic() >= ende:
            log.warning("tmux-bau.service nach %g s nicht aktiv — starte trotzdem", warten_s)
            return
        time.sleep(takt_s)


def sitzung_da(spec: str) -> bool:
    return _tmux("has-session", "-t", f"=spec-{spec}").returncode == 0


def aufseher_starten(spec: str, klon: Path, *, trocken: bool = False) -> bool:
    """Sitzung ``spec-<S>`` mit Fenster ``wache <S>`` anlegen (Befehl wie im Aufpasser)."""
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=aufpasser.zustand_standard(), trocken=trocken))
    gemerkt = a.resume_aus_datei(klon, f"wache-{spec}")
    sid, cwd = gemerkt if gemerkt else (None, klon)
    befehl = a.wache_befehl(klon, spec, resume=sid)
    if trocken:
        print(f"[trocken] spec-{spec}: Fenster „wache {spec}“ in {cwd}: {befehl}")
        return True
    lauf = _tmux(
        "new-session", "-d", "-s", f"spec-{spec}", "-n", f"wache {spec}",
        "-c", str(cwd), f"bash -lc {shlex.quote(befehl)}",
    )  # fmt: skip
    if lauf.returncode != 0:
        log.error("spec-%s: tmux new-session fehlgeschlagen: %s", spec, lauf.stderr.strip())
        return False
    log.info("spec-%s: Aufseher %s", spec, f"fortgesetzt ({sid[:8]}…)" if sid else "frisch gestartet")
    return True


def wiederaufnehmen(
    heim: Path,
    *,
    trocken: bool = False,
    versuche: int = GH_VERSUCHE,
    takt_s: float = GH_TAKT_S,
    schlafen: Callable[[float], object] = time.sleep,
) -> int:
    """Je offener Spec ohne tmux-Sitzung einmal den Aufseher; Exit 1, wenn ein Start
    scheiterte oder GitHub für eine Spec auch nach ``versuche`` Abfragen unlesbar blieb
    (die Unit startet dann neu, statt still nichts zu tun)."""
    code = 0
    gestartet = 0
    unlesbar: list[str] = []
    specs = offene_specs(heim, versuche=versuche, takt_s=takt_s, schlafen=schlafen, unlesbar=unlesbar)
    if unlesbar:
        log.warning(
            "Wiederaufnahme unvollständig: GitHub nach %d Versuchen nicht lesbar für Spec %s",
            versuche, ", ".join(f"#{s}" for s in unlesbar),
        )  # fmt: skip
        code = 1
    if specs and not trocken:
        tmux_dienst_abwarten()
    for spec, klon in specs:
        if sitzung_da(spec):
            log.info("spec-%s läuft schon — kein zweiter Aufseher", spec)
            continue
        if gestartet and not trocken:
            # Staffel wie beim Aufpasser: Claude-Starts nicht stapeln (OOM 21.09.).
            time.sleep(speicher.staffel_s(config.lade(klon)))
        if aufseher_starten(spec, klon, trocken=trocken):
            gestartet += 1
        else:
            code = 1
    log.info("Wiederaufnahme: %d Aufseher gestartet", gestartet)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bau nach Server-Neustart wiederaufnehmen (#1184)")
    ap.add_argument("--heim", type=Path, default=Path.home(), help="Ordner der Spec-Klone (~)")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts starten")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return wiederaufnehmen(args.heim, trocken=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
