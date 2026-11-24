"""Komplett-Prüfung genau einmal je Spec (#425 Teil C, #455). Einzige Stelle des Merkers.

Für Aufrufer (Abschluss-Paket, Aufseher): an jedem Spec-Moment nur :func:`komplett_einmal` rufen und
bei ``ok=False`` mit ``grund`` anhalten. Ob die Prüfung dran ist, sagt :mod:`to_spawn.pruefplan`;
dieses Modul führt den Merker „Komplett-Prüfung lief“ und lässt den Befehl höchstens einmal je
Spec und Kopf-Stand laufen.

Merker ``<klon>/.to-spawn/pruef/komplett-<S>.json`` (Format nur hier, Lesen über :func:`lies_merker`)::

    {"spec": 425, "exit": 0, "dauer_s": 12.34, "start": "2026-11-24T10:00:00+00:00",
     "ende": "2026-11-24T10:00:12+00:00", "kopf": "<voller SHA>", "moment": "alle_auf_staging",
     "grund": ""}

``grund`` (#455) = letzte Ausgabezeile bei Rot, ``"Zeitgrenze"`` nach Abbruch, Werkzeug-Grund bei
Gate-Fehler; fehlt in alten Merkern.

Regeln: Grün (Exit 0) mit gleichem Kopf-SHA = „lief“, nie zweimal. Rot je Kopf (#455): im Takt
(``takt=True``) gilt ein roter Merker mit gleichem Kopf als geprüft — neu erst bei neuem Kopf oder
von Hand (``to_spawn.py komplett <S>`` = ``takt=False``, prüft Rot erneut). Gate-Fehler (Werkzeug)
zählen nie als geprüft. Neuer Kopf nach grünem Lauf (Nachbesserung) macht den Merker ungültig.
Vorher zieht jeder Aufruf (Takt wie von Hand) den Klon per fast-forward auf ``origin/<Hauptzweig>``
nach (:func:`to_spawn.arbeitsstand.sicherstellen`); geht das nicht (abgewichen, würde lokale Dateien
überschreiben), kein Lauf, kein Merker, ``ok=False`` mit Handlung.
Geprüft wird der Arbeitsbaum am Kopf des Klons: ungespeicherte Änderungen
(:func:`to_spawn.pruef.sofort.ungespeichert`) oder ein Kopf hinter dem geholten Stand (``@{u}``
nach fetch) → kein Lauf, kein Merker, ``ok=False`` mit Handlung. Zeitgrenze aus
:func:`to_spawn.config.komplett_zeitgrenze_s`; Ablauf = rot mit Grund „Zeitgrenze“. Jeder Lauf
steht zusätzlich als Zeile mit ``art: "komplett"`` im Prüf-Protokoll.
Übergangsregel: Repo ohne Gruppe ``pruefung`` (:func:`pruefplan.eingerichtet`) → nichts ausführen,
``ok=True`` (alter Ablauf).

Takt (#455): mit ``takt=True`` läuft der Befehl in einem eigenen Hintergrund-Prozess
(``to_spawn.py komplett <S> --aus-takt`` → :func:`takt_kind`); der Takt wartet höchstens
:data:`TAKT_WARTEN_S` und meldet sonst ``laeuft``. Doppelstart schließt die Sperre aus: nur unter
ihr wird gelaufen, und unter ihr wird der Merker erneut gelesen. Verworfen: Prüfung nur außerhalb
der Zustandssperre im selben Prozess — der Aufseher-Takt (``waechter_takt``) ruft capo im selben
Prozess nacheinander, er stünde trotzdem so lange.

Zwei Varianten erwogen: getrennte Türen ``soll_laufen()``/``merken()`` (jeder Aufrufer muss
Reihenfolge, Kopf-Vergleich und Rot-Regel selbst richtig zusammensetzen) gegen eine Tür
:func:`komplett_einmal` — gebaut ist die zweite, weil Merker-Format und „einmal“-Regel so in genau
einem Modul leben und kein Aufrufer sie falsch kombinieren kann.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from to_spawn import arbeitsstand, config, manifest, prozessbaum, pruefplan, starter
from to_spawn import sperre as _sperre
from to_spawn.pruef import leiter, protokoll, sofort
from to_spawn.pruefplan import Moment, SpecStand

log = logging.getLogger(__name__)

#: So lange wartet ein Takt höchstens auf den Hintergrund-Lauf, dann geht er weiter (#455).
#: Kurze Prüfungen (Probe-Repo: Sekunden) melden so ihr Ergebnis noch im selben Takt.
TAKT_WARTEN_S = 30.0
_SKRIPT = Path(__file__).resolve().parent.parent / "to_spawn.py"


@dataclass(frozen=True)
class Ergebnis:
    """Antwort von :func:`komplett_einmal`.

    ``gelaufen`` = der Befehl lief bei diesem Aufruf; ``ok`` = Aufrufer darf weiter (grün, schon
    grün gelaufen, nicht dran oder nicht eingerichtet); ``grund`` = ein Satz, bei ``ok=False`` mit
    Handlung; ``dauer_s`` nur, wenn gelaufen; ``kopf`` = geprüfter bzw. aktueller Kopf-SHA.
    ``laeuft`` = ein anderer Aufruf prüft gerade (Sperre belegt) oder der Hintergrund-Lauf des
    Takts ist noch nicht fertig (``ok=True``): kein Fehler, aber noch kein grüner Stand — Aufrufer
    gehen in diesem Durchgang nicht weiter (kein Fertig, kein Live).
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
    grund: str = ""

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
            grund=str(roh.get("grund") or ""),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as fehler:
        log.warning(
            "komplett_lauf: Merker %s unlesbar, gilt als nicht gelaufen: %s",
            datei,
            fehler,
        )
        return None


def _git(klon: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(klon), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as fehler:
        log.warning("komplett_lauf: git fehlt: %s", fehler)
        return None


def _kopf(klon: Path) -> str | None:
    erg = _git(klon, "rev-parse", "HEAD")
    if erg is None:
        return None
    return erg.stdout.strip() if erg.returncode == 0 and erg.stdout.strip() else None


def _stand_halt(klon: Path, kopf: str) -> str:
    """Grund, warum der Arbeitsbaum nicht für ``kopf`` steht; ``""`` = prüfbar."""
    # Die Konfig trägt nur den Prüf-Befehl, keinen geprüften Code: zählt hier nicht als schmutzig.
    offen = [
        pfad
        for pfad in sofort.ungespeichert(klon)
        if pfad != config.KONFIG_PFAD.as_posix()
    ]
    if offen:
        liste = ", ".join(offen[:3]) + (" …" if len(offen) > 3 else "")
        return (
            f"Klon {klon} hat ungespeicherte Änderungen ({liste}) — Komplett-Prüfung nicht gestartet, "
            "kein Merker. Handlung: committen oder verwerfen, dann erneut rufen."
        )
    hinten = _git(klon, "rev-list", "--count", "HEAD..@{u}")
    if hinten is None or hinten.returncode != 0:
        return ""  # kein Upstream (z. B. reiner Test-Klon): nichts zu vergleichen
    anzahl = int(hinten.stdout.strip() or 0)
    if anzahl:
        return (
            f"Klon-Kopf {kopf[:7]} liegt {anzahl} Commit(s) hinter origin (Stand nach fetch) — geprüft würde "
            "ein alter Stand, Komplett-Prüfung nicht gestartet. Handlung: im Klon `git merge --ff-only @{u}`, "
            "dann erneut rufen."
        )
    return ""


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _gemerkt(merker: Merker | None, kopf: str, rot_zaehlt: bool) -> Merker | None:
    """Merker, der für ``kopf`` als geprüft gilt: grün immer, rot nur mit ``rot_zaehlt``."""
    if merker is None or merker.kopf != kopf:
        return None
    if merker.gruen or (rot_zaehlt and merker.exit == leiter.ROT):
        return merker
    return None


def komplett_einmal(
    klon: Path, spec: int, moment: Moment, konfig: dict[str, Any], *, takt: bool = False
) -> Ergebnis:
    """Komplett-Prüfung der Spec ``spec`` im Klon laufen lassen, wenn sie jetzt dran ist — höchstens einmal.

    ``moment`` = wo die Spec steht (``Moment.ALLE_AUF_STAGING`` oder ``Moment.VOR_LIVE``; ``TICKET``
    ist nie ein Komplett-Auftrag). ``konfig`` = ``config.lade(klon)``. Wirft nicht für Prüf- oder
    git-Fehler; die stehen als ``ok=False`` mit Handlung in ``grund``.
    ``takt=True`` (capo-Takt, #455): Rot gilt je Kopf als geprüft, der Lauf geht in einen
    Hintergrund-Prozess, der Aufruf kehrt nach höchstens :data:`TAKT_WARTEN_S` zurück.
    ``takt=False`` (von Hand, Tür vor Live): läuft im Aufruf, Rot wird erneut geprüft.
    """
    return _pruefen(
        klon, spec, Moment(moment), konfig, rot_zaehlt=takt, hintergrund=takt
    )


def takt_kind(
    klon: Path, spec: int, moment: Moment, konfig: dict[str, Any]
) -> Ergebnis:
    """Nur für den Hintergrund-Prozess aus ``komplett_einmal(takt=True)`` (``--aus-takt``).

    Läuft im Aufruf wie von Hand, aber mit der Takt-Regel „rot je Kopf nur einmal“.
    """
    return _pruefen(
        klon, spec, Moment(moment), konfig, rot_zaehlt=True, hintergrund=False
    )


def _pruefen(
    klon: Path,
    spec: int,
    moment: Moment,
    konfig: dict[str, Any],
    *,
    rot_zaehlt: bool,
    hintergrund: bool,
) -> Ergebnis:
    if not pruefplan.eingerichtet(konfig):
        # Zuerst und ohne git: nicht eingerichtete Repos (laufende Specs) merken nichts davon.
        log.info("komplett_lauf: Spec %s %s", spec, pruefplan.NICHT_EINGERICHTET)
        return Ergebnis(
            False, True, "Prüfung nicht eingerichtet, alter Ablauf", None, ""
        )
    # Vor dem Kopf lesen nachziehen (Review a62e67a): der capo-Takt macht nur fetch; ohne das hielte
    # ein Klon hinter origin die Komplett-Prüfung jeden Takt an, und ein neuer Kopf liefe nie.
    stand = arbeitsstand.sicherstellen(klon, nachziehen=True)
    if not stand.ok:
        return Ergebnis(
            False,
            False,
            f"Komplett-Prüfung nicht gestartet, kein Merker: {stand.text}",
            None,
            _kopf(klon) or "",
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
    gemerkt = _gemerkt(lies_merker(klon, spec), kopf, rot_zaehlt)
    lief = gemerkt is not None and gemerkt.gruen
    art = pruefplan.welche(konfig, SpecStand(moment, komplett_lief=lief))
    if art != "komplett":
        grund = (
            f"Komplett-Prüfung für Stand {kopf[:7]} schon grün gelaufen"
            if lief
            else f"Komplett-Prüfung bei Moment {moment.value} nicht dran"
        )
        return Ergebnis(False, True, grund, None, kopf)
    if gemerkt is not None:
        return _aus_merker(gemerkt, spec, gelaufen=False)
    befehl = pruefplan.befehl(konfig, "komplett")
    if not befehl:
        return Ergebnis(
            False,
            False,
            "pruefung.komplett fehlt in .to-spawn/config.json. Handlung: Befehl im Einrichtungs-Ticket eintragen.",
            None,
            kopf,
        )
    halt = _stand_halt(klon, kopf)
    if halt:
        return Ergebnis(False, False, halt, None, kopf)
    # Sperre gegen Doppellauf (capo-Takt + Tür vor Live): flock fällt mit dem Prozess, ein
    # abgestürzter Lauf sperrt also nie dauerhaft.
    sperr_datei = _datei(klon, spec).with_suffix(".lock")
    belegt = f"Komplett-Prüfung läuft schon (Sperre {sperr_datei.name}). Handlung: später erneut rufen."
    try:
        with _sperre.halte(sperr_datei) as frei:
            if not frei:
                return Ergebnis(False, True, belegt, None, kopf, laeuft=True)
            if not hintergrund:
                # der andere Lauf kann eben fertig geworden sein
                gemerkt = _gemerkt(lies_merker(klon, spec), kopf, rot_zaehlt)
                if gemerkt is not None:
                    return _aus_merker(gemerkt, spec, gelaufen=False)
                return _lauf(
                    klon,
                    spec,
                    moment,
                    befehl,
                    kopf,
                    config.komplett_zeitgrenze_s(konfig),
                )
    except _sperre.SperreNichtOeffenbar as fehler:
        return Ergebnis(
            False,
            False,
            f"Sperre nicht öffenbar ({fehler}). Handlung: Ordner-Rechte prüfen, dann erneut rufen.",
            None,
            kopf,
        )
    # Sperre frei, nur kurz geprüft: der Hintergrund-Prozess nimmt sie selbst und liest den Merker unter ihr.
    return _im_hintergrund(klon, spec, moment, kopf)


def _im_hintergrund(klon: Path, spec: int, moment: Moment, kopf: str) -> Ergebnis:
    """``to_spawn.py komplett <S> --aus-takt`` als eigenen Prozess starten, kurz auf ihn warten."""
    ausgabe = _datei(klon, spec).with_suffix(".log")
    argv = [
        sys.executable, str(_SKRIPT), "komplett", str(int(spec)),
        "--moment", moment.value.replace("_", "-"), "--aus-takt",
    ]  # fmt: skip
    eigen = (
        prozessbaum.ohne_fenster()
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    try:
        ausgabe.parent.mkdir(parents=True, exist_ok=True)
        with ausgabe.open("a", encoding="utf-8") as datei:
            prozess = starter.starte(
                argv,
                cwd=klon,
                notnagel=None,
                env={**os.environ, "TO_SPAWN_REPO": str(klon)},
                stdin=subprocess.DEVNULL,
                stdout=datei,
                stderr=subprocess.STDOUT,
                **eigen,
            )
    except OSError as fehler:
        log.error(
            "komplett_lauf: Hintergrund-Lauf Spec %s nicht gestartet: %s", spec, fehler
        )
        return Ergebnis(
            False,
            False,
            f"Komplett-Prüfung nicht gestartet ({fehler}). Handlung: Python/Skill prüfen, dann erneut rufen.",
            None,
            kopf,
        )
    try:
        code = prozess.wait(timeout=TAKT_WARTEN_S)
    except subprocess.TimeoutExpired:
        log.info(
            "komplett_lauf: Spec %s Kopf %s läuft im Hintergrund (PID %s)",
            spec,
            kopf[:7],
            prozess.pid,
        )
        return Ergebnis(
            False,
            True,
            f"Komplett-Prüfung läuft im Hintergrund (Stand {kopf[:7]}, Ausgabe {ausgabe.name}).",
            None,
            kopf,
            laeuft=True,
        )
    gemerkt = _gemerkt(lies_merker(klon, spec), kopf, True)
    if gemerkt is not None:
        return _aus_merker(gemerkt, spec, gelaufen=True)
    letzte = _letzte_zeile(ausgabe)
    return Ergebnis(
        False,
        False,
        f"Komplett-Prüfung im Hintergrund ohne Ergebnis beendet (Exit {code}): {letzte} "
        f"Handlung: {ausgabe} lesen, dann erneut rufen.",
        None,
        kopf,
    )


def _letzte_zeile(datei: Path) -> str:
    try:
        zeilen = (
            datei.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        )
    except OSError:
        return ""
    return zeilen[-1][:200] if zeilen else ""


def _lauf(
    klon: Path, spec: int, moment: Moment, befehl: str, kopf: str, zeitgrenze_s: float
) -> Ergebnis:
    """Befehl ausführen, Merker + Protokoll-Zeile schreiben; nur unter der Sperre aus :func:`_pruefen`."""
    start_zeit, start = _jetzt(), time.monotonic()
    stufe = leiter.eigener_befehl(klon, befehl, "komplett", zeitgrenze_s)
    dauer = round(time.monotonic() - start, 2)
    exit_code = stufe.ergebnis
    if exit_code == leiter.GRUEN:
        grund = ""
    elif stufe.grund.startswith(leiter.ZEITGRENZE):
        grund = leiter.ZEITGRENZE
    elif exit_code == leiter.ROT:
        grund = ((stufe.ausgabe or "").strip().splitlines()[-1:] or [""])[0][:200]
    else:
        grund = stufe.grund
    daten = {
        "spec": int(spec),
        "exit": exit_code,
        "dauer_s": dauer,
        "start": start_zeit,
        "ende": _jetzt(),
        "kopf": kopf,
        "moment": moment.value,
        "grund": grund,
    }
    _protokoll(klon, spec, moment, stufe, kopf, dauer)
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
    return _aus_merker(Merker(**daten), spec, gelaufen=True)  # type: ignore[arg-type]


def _protokoll(
    klon: Path, spec: int, moment: Moment, stufe: leiter.Stufe, kopf: str, dauer: float
) -> None:
    """Eine Zeile ``art: "komplett"`` im Prüf-Protokoll (#455); Schreibfehler bleiben eine Warnung."""
    zeile = protokoll.zeile(
        basis=kopf,
        ziel=kopf,
        exit_code=stufe.ergebnis,
        stufen=[
            {
                "name": stufe.name,
                "adapter": stufe.adapter,
                "lief": stufe.lief,
                "grund": stufe.grund,
                "dauer_s": stufe.dauer_s,
                "befehl": stufe.befehl,
            }
        ],
        werkzeug=[stufe.werkzeug] if stufe.werkzeug else [],
        fehler_kurz=(stufe.ausgabe or "").strip()[-500:],
        gesamt_s=dauer,
    )
    zeile.update(art="komplett", spec=int(spec), moment=moment.value)
    protokoll.schreibe(klon, zeile)


def _aus_merker(merker: Merker, spec: int, *, gelaufen: bool) -> Ergebnis:
    """Ergebnis-Satz aus einem Merker (eben gelaufen oder je Kopf schon geprüft)."""
    kurz, dauer = merker.kopf[:7], merker.dauer_s if gelaufen else None
    if merker.gruen:
        grund = (
            f"Komplett-Prüfung grün ({merker.dauer_s:.1f} s, Stand {kurz})"
            if gelaufen
            else f"Komplett-Prüfung für Stand {kurz} schon grün gelaufen"
        )
        return Ergebnis(gelaufen, True, grund, dauer, merker.kopf)
    if merker.exit == leiter.ROT:
        handlung = (
            "Handlung: roten Test im Klon beheben und committen (neuer Kopf läuft im Takt neu) oder von Hand "
            f"`to_spawn.py komplett {int(spec)} --moment {merker.moment.replace('_', '-')}`."
        )
    else:
        handlung = (
            f"Handlung: Werkzeug-Fehler beheben ({merker.grund}), dann erneut rufen."
        )
    if gelaufen:
        text = f"Komplett-Prüfung rot (Exit {merker.exit}) für Stand {kurz}: {merker.grund} {handlung}"
    else:
        text = f"Komplett-Prüfung für Stand {kurz} schon rot geprüft (Exit {merker.exit}: {merker.grund}). {handlung}"
    return Ergebnis(gelaufen, False, text.replace("  ", " "), dauer, merker.kopf)
