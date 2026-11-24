"""Komplett-Prüfung genau einmal je Spec (#425 Teil C). Einzige Stelle des Merkers.

Für Aufrufer (Abschluss-Paket, Aufseher): an jedem Spec-Moment nur :func:`komplett_einmal` rufen und
bei ``ok=False`` mit ``grund`` anhalten. Ob die Prüfung dran ist, sagt :mod:`to_spawn.pruefplan`;
dieses Modul führt den Merker „Komplett-Prüfung lief“ und lässt den Befehl höchstens einmal je
Spec und Kopf-Stand laufen.

Merker ``<klon>/.to-spawn/pruef/komplett-<S>.json`` (Format nur hier, Lesen über :func:`lies_merker`)::

    {"spec": 425, "exit": 0, "dauer_s": 12.34, "start": "2026-11-24T10:00:00+00:00",
     "ende": "2026-11-24T10:00:12+00:00", "kopf": "<voller SHA>", "moment": "alle_auf_staging"}

Regeln: Grün (Exit 0) mit gleichem Kopf-SHA = „lief“, nie zweimal. Rot oder Gate-Fehler gilt nicht
als „lief“ — der nächste Aufruf prüft erneut. Neuer Kopf nach grünem Lauf (Nachbesserung) macht den
Merker ungültig, weil der neue Stand nie komplett geprüft wurde; dann läuft sie einmal neu.
Übergangsregel: Repo ohne Gruppe ``pruefung`` (:func:`pruefplan.eingerichtet`) → nichts ausführen,
``ok=True`` (alter Ablauf).

Zwei Varianten erwogen: getrennte Türen ``soll_laufen()``/``merken()`` (jeder Aufrufer muss
Reihenfolge, Kopf-Vergleich und Rot-Regel selbst richtig zusammensetzen) gegen eine Tür
:func:`komplett_einmal` — gebaut ist die zweite, weil Merker-Format und „einmal“-Regel so in genau
einem Modul leben und kein Aufrufer sie falsch kombinieren kann.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from to_spawn import manifest, pruefplan
from to_spawn import sperre as _sperre
from to_spawn.pruef import leiter, protokoll
from to_spawn.pruefplan import Moment, SpecStand

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ergebnis:
    """Antwort von :func:`komplett_einmal`.

    ``gelaufen`` = der Befehl lief bei diesem Aufruf; ``ok`` = Aufrufer darf weiter (grün, schon
    grün gelaufen, nicht dran oder nicht eingerichtet); ``grund`` = ein Satz, bei ``ok=False`` mit
    Handlung; ``dauer_s`` nur, wenn gelaufen; ``kopf`` = geprüfter bzw. aktueller Kopf-SHA.
    ``laeuft`` = ein anderer Aufruf prüft gerade (Sperre belegt, ``ok=True``): kein Fehler, aber noch
    kein grüner Stand — Aufrufer gehen in diesem Durchgang nicht weiter (kein Fertig, kein Live).
    """

    gelaufen: bool
    ok: bool
    grund: str
    dauer_s: float | None
    kopf: str
    laeuft: bool = False


@dataclass(frozen=True)
class Merker:
    """Inhalt der Merker-Datei einer Spec (letzter Lauf, grün oder rot)."""

    spec: int
    exit: int
    dauer_s: float
    start: str
    ende: str
    kopf: str
    moment: str

    @property
    def gruen(self) -> bool:
        return self.exit == leiter.GRUEN


def _datei(klon: Path, spec: int) -> Path:
    return klon / protokoll.ORDNER / f"komplett-{int(spec)}.json"


def lies_merker(klon: Path, spec: int) -> Merker | None:
    """Letzter Komplett-Lauf der Spec ``spec`` im Klon; ``None`` = keiner oder Datei unlesbar.

    Für Messungen (z. B. „Dauer der Komplett-Prüfung“) und für :func:`komplett_einmal`.
    """
    datei = _datei(klon, spec)
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
        return Merker(
            spec=int(roh["spec"]),
            exit=int(roh["exit"]),
            dauer_s=float(roh["dauer_s"]),
            start=str(roh["start"]),
            ende=str(roh["ende"]),
            kopf=str(roh["kopf"]),
            moment=str(roh["moment"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as fehler:
        log.warning(
            "komplett_lauf: Merker %s unlesbar, gilt als nicht gelaufen: %s",
            datei,
            fehler,
        )
        return None


def _kopf(klon: Path) -> str | None:
    try:
        erg = subprocess.run(
            ["git", "-C", str(klon), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as fehler:
        log.warning("komplett_lauf: git fehlt: %s", fehler)
        return None
    return erg.stdout.strip() if erg.returncode == 0 and erg.stdout.strip() else None


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def komplett_einmal(
    klon: Path, spec: int, moment: Moment, konfig: dict[str, Any]
) -> Ergebnis:
    """Komplett-Prüfung der Spec ``spec`` im Klon laufen lassen, wenn sie jetzt dran ist — höchstens einmal.

    ``moment`` = wo die Spec steht (``Moment.ALLE_AUF_STAGING`` oder ``Moment.VOR_LIVE``; ``TICKET``
    ist nie ein Komplett-Auftrag). ``konfig`` = ``config.lade(klon)``. Wirft nicht für Prüf- oder
    git-Fehler; die stehen als ``ok=False`` mit Handlung in ``grund``.
    """
    moment = Moment(moment)
    if not pruefplan.eingerichtet(konfig):
        # Zuerst und ohne git: nicht eingerichtete Repos (laufende Specs) merken nichts davon.
        log.info("komplett_lauf: Spec %s %s", spec, pruefplan.NICHT_EINGERICHTET)
        return Ergebnis(
            False, True, "Prüfung nicht eingerichtet, alter Ablauf", None, ""
        )
    kopf = _kopf(klon)
    if kopf is None:
        return Ergebnis(
            False,
            False,
            f"Kopf-SHA in {klon} nicht lesbar (git). Handlung: Klon prüfen, dann erneut rufen.",
            None,
            "",
        )
    merker = lies_merker(klon, spec)
    lief = merker is not None and merker.gruen and merker.kopf == kopf
    art = pruefplan.welche(konfig, SpecStand(moment, komplett_lief=lief))
    if art != "komplett":
        grund = (
            f"Komplett-Prüfung für Stand {kopf[:7]} schon grün gelaufen"
            if lief
            else f"Komplett-Prüfung bei Moment {moment.value} nicht dran"
        )
        return Ergebnis(False, True, grund, None, kopf)
    befehl = pruefplan.befehl(konfig, "komplett")
    if not befehl:
        return Ergebnis(
            False,
            False,
            "pruefung.komplett fehlt in .to-spawn/config.json. Handlung: Befehl im Einrichtungs-Ticket eintragen.",
            None,
            kopf,
        )
    # Sperre gegen Doppellauf (capo-Takt + Tür vor Live): flock fällt mit dem Prozess, ein
    # abgestürzter Lauf sperrt also nie dauerhaft.
    sperr_datei = _datei(klon, spec).with_suffix(".lock")
    try:
        with _sperre.halte(sperr_datei) as frei:
            if not frei:
                grund = f"Komplett-Prüfung läuft schon (Sperre {sperr_datei.name}). Handlung: später erneut rufen."
                return Ergebnis(False, True, grund, None, kopf, laeuft=True)
            merker = lies_merker(
                klon, spec
            )  # der andere Lauf kann eben grün geworden sein
            if merker is not None and merker.gruen and merker.kopf == kopf:
                return Ergebnis(
                    False,
                    True,
                    f"Komplett-Prüfung für Stand {kopf[:7]} schon grün gelaufen",
                    None,
                    kopf,
                )
            return _lauf(klon, spec, moment, befehl, kopf)
    except _sperre.SperreNichtOeffenbar as fehler:
        return Ergebnis(
            False,
            False,
            f"Sperre nicht öffenbar ({fehler}). Handlung: Ordner-Rechte prüfen, dann erneut rufen.",
            None,
            kopf,
        )


def _lauf(klon: Path, spec: int, moment: Moment, befehl: str, kopf: str) -> Ergebnis:
    """Befehl ausführen und Merker schreiben; nur unter der Sperre aus :func:`komplett_einmal`."""
    start_zeit, start = _jetzt(), time.monotonic()
    stufe = leiter.eigener_befehl(klon, befehl, "komplett")
    dauer = round(time.monotonic() - start, 2)
    exit_code = stufe.ergebnis
    daten = {
        "spec": int(spec),
        "exit": exit_code,
        "dauer_s": dauer,
        "start": start_zeit,
        "ende": _jetzt(),
        "kopf": kopf,
        "moment": moment.value,
    }
    datei = _datei(klon, spec)
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        manifest.schreibe_atomar(datei, daten)
    except OSError as fehler:
        log.error("komplett_lauf: Merker %s nicht schreibbar: %s", datei, fehler)
        return Ergebnis(
            True,
            False,
            f"Merker {datei} nicht schreibbar ({fehler}). Handlung: Ordner-Rechte prüfen, dann erneut rufen.",
            dauer,
            kopf,
        )
    log.info(
        "komplett_lauf: Spec %s Kopf %s Exit %s in %.2f s",
        spec,
        kopf[:7],
        exit_code,
        dauer,
    )
    if exit_code == leiter.GRUEN:
        return Ergebnis(
            True,
            True,
            f"Komplett-Prüfung grün ({dauer:.1f} s, Stand {kopf[:7]})",
            dauer,
            kopf,
        )
    if exit_code == leiter.ROT:
        handlung = "Handlung: roten Test im Klon beheben, committen, dann erneut rufen (läuft dann neu)."
    else:
        handlung = (
            f"Handlung: Werkzeug-Fehler beheben ({stufe.grund}), dann erneut rufen."
        )
    kurz = (stufe.ausgabe or "").strip().splitlines()[-1:] or [""]
    return Ergebnis(
        True,
        False,
        f"Komplett-Prüfung rot (Exit {exit_code}) für Stand {kopf[:7]}: {kurz[0][:200]} {handlung}".replace(
            "  ", " "
        ),
        dauer,
        kopf,
    )
