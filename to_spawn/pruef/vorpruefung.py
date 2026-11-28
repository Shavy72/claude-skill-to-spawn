"""Vorprüfung vor der Schlange (Stufe 0–3) und Gate-Platz-Vergabe (L3 3b-6, #106).

Ein Ticket durchläuft vor Eintritt in die Deploy-Schlange genau die schnellen Stufen 0–3:

- **Stufe 0 (Cache):** War genau dieser Stand schon grün (voll oder Vorprüfung), sofort frei.
- **Stufe 1 (Sofort-Prüfung):** ``ruff``/ESLint auf geänderte Dateien plus Python-Syntax-Netz
  (``ast.parse``-Rückfall, falls ``ruff`` fehlt) sowie Beleg-Schlüssel-Sperre. Rot weist das Ticket
  sofort ab — ohne jemals einen Gate-Platz zu belegen.
- **Stufe 2 (Nur Doku):** Erklärte Doku-Änderung → grün, kein Testlauf, kein Gate-Lauf nötig.
- **Stufe 3 (Betroffene Tests):** Statische Auswahl je erkanntem Adapter. Rot weist vor der
  Schlange ab; Rückfälle (R1–R3, LF, AB, Auswahl nicht möglich) starten in der Vorprüfung **nie**
  Stufe 4 (voll), sondern übergeben das Ticket geordnet an das Gate (``weiter_an_gate=True``).

Zweiter Gate-Platz (Schlangen-Regel nach 3b-5 #105 / Vergleich 6.3):
- Nur zulässig, wenn die gemessene Bremse bei 2 gleichzeitigen Gates ``<= 1,5`` liegt
  (:data:`SCHWELLE_ZWEITER_PLATZ`, in #105 mit ``1,29×–1,34×`` belegt).
- Zusätzlich geschützt durch RAM-Grenze (``>= 4096 MiB`` frei für 2 Plätze, ``>= 2048 MiB`` für
  1 Platz), 1-Minuten-Last-Grenze (``< 6,0`` für Start des 2. Platzes, ``< 12,0`` hart) und
  aktive Bau-Sessions (``<= 2`` bei 2 Plätzen, gegen Kern-Überbuchung #290).
- Bündel höchstens :data:`MAX_BUENDEL_GROESSE` (2), keine Spuren nach Ordner.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from to_spawn import config, konsole, prozessbaum, speicher, sperre, temp_ordner
from to_spawn.pruef import (
    adapter_node,
    adapter_py,
    ausfuehren,
    cache,
    leiter,
    protokoll,
    regeln,
    sofort,
)

log = logging.getLogger("to_spawn.pruef.vorpruefung")

#: Schwelle aus Vergleich 6.3 / 3b-5 (#105): zweiter Gate-Platz nur bei Bremse <= 1,5.
SCHWELLE_ZWEITER_PLATZ: float = 1.5
#: Höchstzahl paralleler Gate-Plätze auf dem Bau-Server (3b-6).
MAX_GATE_PLAETZE: int = 2
#: Höchste Bündel-Größe (Vergleich 6.3 / Schnitt 3b-6: 1–2, keine Spuren).
MAX_BUENDEL_GROESSE: int = 2
#: Mindestens so viel freier RAM (MiB) für 1 Gate-Platz (wie ``config.DEFAULTS["speicher"]["min_frei_mib"]``).
MIN_FREI_MIB_EIN_PLATZ: int = int(config.DEFAULTS["speicher"]["min_frei_mib"])
#: Mindestens so viel freier RAM (MiB) für 2 parallele Gate-Plätze (2048 MiB Reserve + 4×512 MiB).
MIN_FREI_MIB_ZWEI_PLAETZE: int = MIN_FREI_MIB_EIN_PLATZ + 4 * ausfuehren.JE_WORKER_MIB
#: Harte 1-Minuten-Last-Obergrenze für jeden Gate-Start (Last < 12).
MAX_LAST_START: float = 12.0
#: Last-Grenze für den 2. Gate-Platz (#105: 2 Gates allein heben Last auf ~11,5 → Start nur < 6,0).
MAX_LAST_ZWEITER_PLATZ: float = 6.0
#: Höchstens so viele aktive Bau-Sessions bei 2 parallelen Gates (#105 / #290).
MAX_BAU_SESSIONS_ZWEI_PLAETZE: int = 2
#: Pause zwischen Sperr-Versuchen beim Warten auf einen Gate-Platz (Sekunden).
PLATZ_TAKT_S: float = 0.05

_ADAPTER: tuple[leiter.Adapter, ...] = (adapter_py.ADAPTER, adapter_node.ADAPTER)
_PY_ENDUNGEN = frozenset({".py", ".pyi"})
_WORKTREE_SPERRE = threading.Lock()


@dataclass(frozen=True)
class VorpruefUrteil:
    """Ergebnis der Vorprüfung (Stufe 0–3) vor Eintritt in die Schlange."""

    exit: int
    zugelassen: bool
    weiter_an_gate: bool
    grund: str
    zeile: dict[str, Any]


@dataclass(frozen=True)
class GateKapazitaet:
    """Entscheidung, wie viele Gate-Plätze (0, 1 oder 2) gerade zulässig sind."""

    plaetze: int
    grund: str
    bremse_faktor: float | None
    frei_mib: int | None
    last_1m: float | None
    bau_sessions: int | None


@dataclass(frozen=True)
class EinlassErgebnis:
    """Ergebnis des Eintritts in den Gate-Pool (Vorprüfung zuerst, Gate-Platz danach)."""

    zugelassen: bool
    gate_platz_belegt: bool
    platz_nr: int | None
    warte_s: float
    vorpruefung: VorpruefUrteil
    kapazitaet: GateKapazitaet | None
    grund: str


def _sha(repo: Path, ref: str) -> str | None:
    try:
        erg = konsole.lauf_daten(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=repo,
            check=False,
        )
    except OSError:
        return None
    if erg is None:
        return None
    return erg.stdout.strip() if erg.returncode == 0 else None


@contextlib.contextmanager
def _arbeits_repo(repo: Path, ziel_sha: str, kopf_sha: str | None, *, isoliert: bool) -> Iterator[tuple[Path, bool]]:
    """Stellt den geprüften Stand bereit: direkt im Repo oder im isolierten Temp-Worktree.

    Liefert ``(ordner, ist_temp_worktree)``. Der Temp-Worktree wird im ``finally`` garantiert
    über ``git worktree remove --force`` entfernt (nie Reste im Hauptrepo).
    """
    if not isoliert and ziel_sha == kopf_sha:
        yield repo, False
        return
    with tempfile.TemporaryDirectory(prefix="to_spawn_vorpruef_") as huelle:
        ziel_ordner = Path(huelle) / "wt"
        with _WORKTREE_SPERRE:
            anlegen = subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", "--quiet", str(ziel_ordner), ziel_sha],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **prozessbaum.fensterlos(),
            )
        if anlegen.returncode != 0:
            fehler = (anlegen.stderr or anlegen.stdout or f"Exit {anlegen.returncode}").strip()
            raise RuntimeError(f"Temp-Worktree für {ziel_sha[:7]} gescheitert: {fehler}")
        try:
            yield ziel_ordner, True
        finally:
            with _WORKTREE_SPERRE:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", str(ziel_ordner)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    **prozessbaum.fensterlos(),
                )
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "prune"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    **prozessbaum.fensterlos(),
                )


def _python_syntax_netz(repo: Path, pfade: list[str]) -> leiter.Stufe | None:
    """Prüft geänderte Python-Dateien per ``ast.parse``, falls ``ruff`` nicht lief.

    So fängt Stufe 1 jeden Python-Syntax-Fehler auch in minimalen Umgebungen ohne ``ruff``.
    """
    py_dateien = [p for p in pfade if PurePosixPath(p).suffix.lower() in _PY_ENDUNGEN and (repo / p).is_file()]
    if not py_dateien:
        return None
    start = time.monotonic()
    fehler: list[str] = []
    for rel in py_dateien:
        datei = repo / rel
        try:
            quelle = datei.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            fehler.append(f"{rel}: unlesbar ({err})")
            continue
        try:
            ast.parse(quelle, filename=rel)
        except SyntaxError as err:
            zeile = err.lineno or 1
            fehler.append(f"{rel}:{zeile}: SyntaxError: {err.msg}")
    dauer = round(time.monotonic() - start, 2)
    if fehler:
        return leiter.Stufe(
            "sofort_syntax",
            "py",
            True,
            f"Python-Syntax-Fehler ({len(fehler)} Datei(en))",
            dauer,
            "ast.parse",
            "python-ast",
            leiter.ROT,
            "\n".join(fehler),
        )
    return leiter.Stufe(
        "sofort_syntax",
        "py",
        True,
        f"{len(py_dateien)} Python-Datei(en) syntaktisch sauber",
        dauer,
        "ast.parse",
        "python-ast",
        leiter.GRUEN,
        "",
    )


def _stufe3_vorpruefung(
    arbeits_repo: Path,
    protokoll_repo: Path,
    adapter: leiter.Adapter,
    basis_sha: str,
    pfade: list[str] | None,
    *,
    ist_temp_worktree: bool,
) -> tuple[leiter.Stufe, bool]:
    """Führt Stufe 3 (Auswahl) für einen Adapter aus; nie Stufe 4 (voll).

    Rückgabe: ``(stufe, weiter_an_gate)``. Muss ein Rückfall greifen (R1–R3, LF, AB, Auswahl
    nicht möglich), bleibt die Stufe ausgelassen und ``weiter_an_gate=True`` übergibt ans Gate.
    """
    doku = sofort.erklaerte_doku(arbeits_repo, basis_sha, pfade or [])
    kann_rote = leiter._unterstuetzt_rote(adapter)
    # In einem isolierten Ticket-Worktree darf kein vorheriges fremdes Rot aus dem Hauptrepo
    # die Auswahl dieses Tickets blockieren; im selben Worktree gilt die normale Nach-Rot-Regel.
    letztes_urteil = "" if ist_temp_worktree else protokoll.letztes_urteil(protokoll_repo)
    rote = () if ist_temp_worktree else (protokoll.letzte_rote(protokoll_repo, adapter.name) if kann_rote else ())

    grund = regeln.vorab(
        pfade,
        adapter.name,
        regeln.risiko_pfade(arbeits_repo),
        letztes_urteil,
        doku=doku,
        offen=sofort.ungespeichert(arbeits_repo),
        rote_tests=rote,
    )
    if grund:
        return (
            leiter.Stufe(
                "auswahl",
                adapter.name,
                False,
                f"Vorprüfung: weiter an Gate (Stufe 4 nötig — {grund})",
                0.0,
                "",
                "",
                leiter.AUSGELASSEN,
                "",
                auswahl=leiter._wahl(adapter, grund),
            ),
            True,
        )

    if kann_rote:
        plan = adapter.auswahl(arbeits_repo, basis_sha, extra_tests=rote, pfade=pfade)  # type: ignore[call-arg]
    else:
        plan = adapter.auswahl(arbeits_repo, basis_sha)
    if plan.fehler:
        text = f"Auswahl nicht möglich: {plan.fehler}"
        return (
            leiter.Stufe(
                "auswahl",
                adapter.name,
                False,
                f"Vorprüfung: weiter an Gate ({text})",
                0.0,
                "",
                plan.werkzeug,
                leiter.AUSGELASSEN,
                "",
                auswahl=leiter._wahl(adapter, text),
            ),
            True,
        )
    if not plan.befehl:
        erg = ausfuehren.Lauf(0, "", 0.0)
        n: int | None = 0
        gesamt: int | None = None
    else:
        erg = ausfuehren.lauf(plan.befehl, arbeits_repo)
        n, gesamt = adapter.zaehle(erg.code, erg.ausgabe)
    if n is None and adapter.deute(erg.code) == leiter.ROT:
        wahl = leiter._wahl(adapter, "", None, gesamt)
        stufe = leiter._gedeutet(
            arbeits_repo,
            adapter,
            plan,
            erg,
            leiter.Stufe(
                "auswahl", adapter.name, True, "Auswahl rot (Sammelfehler)", 0.0, "", "", leiter.ROT, "", auswahl=wahl
            ),
        )
        return stufe, False

    nach_grund = regeln.nach_auswahl(n, pfade or [], adapter.name, doku)
    if nach_grund:
        return (
            leiter.Stufe(
                "auswahl",
                adapter.name,
                False,
                f"Vorprüfung: weiter an Gate (Stufe 4 nötig — {nach_grund})",
                0.0,
                "",
                plan.werkzeug,
                leiter.AUSGELASSEN,
                "",
                auswahl=leiter._wahl(adapter, nach_grund, n, gesamt),
            ),
            True,
        )

    wahl = leiter._wahl(adapter, "", n, gesamt)
    if n == 0:
        return (
            leiter.Stufe(
                "auswahl",
                adapter.name,
                True,
                "0 betroffene Tests",
                round(erg.dauer_s, 2),
                " ".join(plan.befehl),
                plan.werkzeug,
                leiter.GRUEN,
                "",
                auswahl=wahl,
            ),
            False,
        )
    text_grund = f"Nach Rot: {len(rote)} zuletzt rote + Auswahl ({n} Tests)" if rote else f"{n} betroffene Tests"
    stufe = leiter._gedeutet(
        arbeits_repo,
        adapter,
        plan,
        erg,
        leiter.Stufe("auswahl", adapter.name, True, text_grund, 0.0, "", "", leiter.GRUEN, "", auswahl=wahl),
    )
    return stufe, stufe.ergebnis == leiter.GRUEN


def _protokoll_schreiben(repo: Path, zeile: dict[str, Any]) -> None:
    zeile["art"] = "vorpruefung"
    log.info("Vorprüfung abgeschlossen: %.2f s (Urteil %s)", zeile["gesamt_s"], zeile["urteil"])
    protokoll.schreibe(repo, zeile)


def _gate_fehler_urteil(repo: Path, basis: str, ziel: str, grund: str, start: float) -> VorpruefUrteil:
    zeile = protokoll.zeile(
        basis=basis,
        ziel=ziel,
        exit_code=leiter.GATE_FEHLER,
        stufen=[],
        werkzeug=[],
        fehler_kurz=grund,
        gesamt_s=time.monotonic() - start,
    )
    _protokoll_schreiben(repo, zeile)
    return VorpruefUrteil(
        exit=leiter.GATE_FEHLER,
        zugelassen=False,
        weiter_an_gate=False,
        grund=grund,
        zeile=zeile,
    )


def vorpruefen(
    repo: Path,
    basis: str,
    ziel: str,
    *,
    isoliert: bool = False,
) -> VorpruefUrteil:
    """Prüft ``ziel`` gegen ``basis`` in Stufe 0–3 vor Eintritt in die Schlange (nie Stufe 4).

    Ist ``ziel`` nicht ausgecheckt (oder ``isoliert=True``), läuft die Vorprüfung in einem
    temporären, losgelösten Git-Worktree, ohne den Arbeitsbaum von ``repo`` anzufassen.
    """
    start = time.monotonic()
    wurzel = config.repo_wurzel(repo)
    basis_sha, ziel_sha, kopf = _sha(wurzel, basis), _sha(wurzel, ziel), _sha(wurzel, "HEAD")
    if basis_sha is None or ziel_sha is None:
        fehlt = basis if basis_sha is None else ziel
        return _gate_fehler_urteil(wurzel, basis, ziel, f"Stand „{fehlt}“ in {wurzel} nicht gefunden (git)", start)

    try:
        with _arbeits_repo(wurzel, ziel_sha, kopf, isoliert=isoliert) as (arbeits_repo, ist_temp):
            if not ist_temp:
                offen = sofort.ungespeichert(arbeits_repo)
                if offen:
                    return _gate_fehler_urteil(
                        wurzel,
                        basis_sha,
                        ziel_sha,
                        f"Arbeitsbaum nicht gespeichert (erst committen): {', '.join(offen[:5])}",
                        start,
                    )

            # Stufe 0: Cache (voll-grün oder Vorprüfung schon grün)
            basis_schluessel = cache.schluessel(arbeits_repo, basis_sha, ziel_sha)
            schluessel_doku = f"{basis_schluessel}-vorpruef-doku" if basis_schluessel else None
            schluessel_gate = f"{basis_schluessel}-vorpruef-gate" if basis_schluessel else None

            treffer_art: str | None = None
            if cache.treffer(basis_schluessel):
                treffer_art = "voll"
            elif cache.treffer(schluessel_doku):
                treffer_art = "doku"
            elif cache.treffer(schluessel_gate):
                treffer_art = "gate"

            if treffer_art is not None:
                weiter = treffer_art == "gate"
                grund_text = (
                    "Stand schon voll grün geprüft (Stufe 0)"
                    if treffer_art == "voll"
                    else "Vorprüfung für diesen Stand schon grün (Stufe 0)"
                )
                zeile = protokoll.zeile(
                    basis=basis_sha,
                    ziel=ziel_sha,
                    exit_code=leiter.GRUEN,
                    stufen=[
                        {
                            "name": "cache",
                            "adapter": "",
                            "lief": True,
                            "grund": grund_text,
                            "dauer_s": round(time.monotonic() - start, 2),
                            "befehl": "",
                        }
                    ],
                    werkzeug=[],
                    fehler_kurz="",
                    gesamt_s=time.monotonic() - start,
                    cache_treffer=True,
                )
                _protokoll_schreiben(wurzel, zeile)
                return VorpruefUrteil(
                    exit=leiter.GRUEN,
                    zugelassen=True,
                    weiter_an_gate=weiter,
                    grund=grund_text,
                    zeile=zeile,
                )

            with temp_ordner.lauf(arbeits_repo, von="pruef"):
                erkannt = [adapter for adapter in _ADAPTER if adapter.erkennt(arbeits_repo)]
                vorn, halt = sofort.stufen(arbeits_repo, basis_sha)
                pfade = sofort.geaendert(arbeits_repo, basis_sha)

                # Zusätzliches Python-Syntax-Netz in Stufe 1, falls ruff nicht gelaufen ist.
                py_lint_lief = any(s.name == "sofort" and s.adapter == "py" and s.lief for s in vorn)
                if not halt and not py_lint_lief and pfade:
                    syntax_stufe = _python_syntax_netz(arbeits_repo, pfade)
                    if syntax_stufe is not None:
                        vorn.append(syntax_stufe)
                        if syntax_stufe.ergebnis == leiter.ROT:
                            halt = sofort.ROT_HALT

                liste: list[leiter.Stufe] = list(vorn)
                weiter_an_gate = False
                if halt in (sofort.ROT_HALT, sofort.NUR_DOKU):
                    weiter_an_gate = False
                elif not erkannt:
                    return _gate_fehler_urteil(
                        wurzel,
                        basis_sha,
                        ziel_sha,
                        "keine Sprache erkannt (weder Python noch Node)",
                        start,
                    )
                else:
                    for adapter in erkannt:
                        stufe, braucht_gate = _stufe3_vorpruefung(
                            arbeits_repo,
                            wurzel,
                            adapter,
                            basis_sha,
                            pfade,
                            ist_temp_worktree=ist_temp,
                        )
                        liste.append(stufe)
                        if braucht_gate:
                            weiter_an_gate = True

            exit_code, fehler_kurz = leiter.ergebnis(liste)
            zugelassen = exit_code == leiter.GRUEN
            if not zugelassen:
                weiter_an_gate = False

            ziel_schluessel = (schluessel_gate if weiter_an_gate else schluessel_doku) if basis_schluessel else None
            zeile = protokoll.zeile(
                basis=basis_sha,
                ziel=ziel_sha,
                exit_code=exit_code,
                stufen=[
                    {
                        "name": stufe.name,
                        "adapter": stufe.adapter,
                        "lief": stufe.lief,
                        "grund": stufe.grund,
                        "dauer_s": stufe.dauer_s,
                        "befehl": stufe.befehl,
                        "wiederholung": stufe.wiederholung,
                        "wiederholung_s": stufe.wiederholung_s,
                        "voll_nochmal_s": stufe.voll_nochmal_s,
                    }
                    for stufe in liste
                ],
                werkzeug=[stufe.werkzeug for stufe in liste if stufe.werkzeug] + [regeln.stand()],
                fehler_kurz=fehler_kurz,
                gesamt_s=time.monotonic() - start,
                auswahl=[stufe.auswahl for stufe in liste if stufe.auswahl] or None,
                wackler=[{"test": test, "adapter": stufe.adapter} for stufe in liste for test in stufe.wackler],
                rote_tests=[
                    {"test": test, "adapter": stufe.adapter}
                    for stufe in liste
                    if stufe.ergebnis == leiter.ROT
                    for test in stufe.rote_tests
                ],
                schluessel=ziel_schluessel,
            )
            _protokoll_schreiben(wurzel, zeile)
            if zugelassen and ziel_schluessel:
                cache.merke(ziel_schluessel, zeile)

            if not zugelassen:
                grund_text = f"Vorprüfung abgewiesen ({zeile['urteil']}): {fehler_kurz or 'Fehler in Stufe 1–3'}"
            elif halt == sofort.NUR_DOKU:
                grund_text = "Vorprüfung grün (Stufe 2: nur Doku, kein Gate-Lauf nötig)"
            elif weiter_an_gate:
                grund_text = "Vorprüfung bestanden (Stufe 0–3) — zugelassen für Gate-Platz"
            else:
                grund_text = "Vorprüfung grün (Stufe 0–3)"

            return VorpruefUrteil(
                exit=exit_code,
                zugelassen=zugelassen,
                weiter_an_gate=weiter_an_gate,
                grund=grund_text,
                zeile=zeile,
            )
    except RuntimeError as fehler:
        return _gate_fehler_urteil(wurzel, basis_sha, ziel_sha, str(fehler), start)


def erlaubte_gate_plaetze(
    *,
    bremse_faktor: float | None,
    frei_mib: int | None = None,
    last_1m: float | None = None,
    bau_sessions: int | None = None,
    min_frei_mib_ein_platz: int = MIN_FREI_MIB_EIN_PLATZ,
    min_frei_mib_zwei_plaetze: int = MIN_FREI_MIB_ZWEI_PLAETZE,
    max_last_start: float = MAX_LAST_START,
    max_last_zweiter_platz: float = MAX_LAST_ZWEITER_PLATZ,
    max_bau_sessions_zwei_plaetze: int = MAX_BAU_SESSIONS_ZWEI_PLAETZE,
) -> GateKapazitaet:
    """Entscheidet, wie viele Gate-Plätze (0, 1 oder 2) auf dem Rechner gerade zulässig sind.

    - 0 Plätze bei hartem RAM- (< ``min_frei_mib_ein_platz``) oder Last-Schutz (>= ``max_last_start``).
    - 2 Plätze **nur**, wenn die gemessene Bremse ``<= 1,5`` vorliegt **und** RAM, Last und aktive
      Bau-Sessions genügend Spielraum für 2 gleichzeitige ``-n 4``-Gates lassen (#105, #290).
    - Sonst 1 Gate-Platz mit eindeutigem Grund.
    """
    if frei_mib is not None and frei_mib < min_frei_mib_ein_platz:
        return GateKapazitaet(
            plaetze=0,
            grund=f"RAM-Schutz: nur {frei_mib} MiB frei (Mindestmaß für 1 Gate: {min_frei_mib_ein_platz} MiB)",
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if last_1m is not None and last_1m >= max_last_start:
        return GateKapazitaet(
            plaetze=0,
            grund=f"Last-Schutz: 1-Minuten-Last {last_1m:.2f} >= Grenze {max_last_start:.1f}",
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if bremse_faktor is None:
        return GateKapazitaet(
            plaetze=1,
            grund="1 Gate-Platz: Bremse-Faktor für 2 Gates nicht belegt (Schwelle <= 1,5)",
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if bremse_faktor > SCHWELLE_ZWEITER_PLATZ:
        return GateKapazitaet(
            plaetze=1,
            grund=f"1 Gate-Platz: Bremse {bremse_faktor:.2f}× > Schwelle {SCHWELLE_ZWEITER_PLATZ:.1f}×",
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if frei_mib is not None and frei_mib < min_frei_mib_zwei_plaetze:
        return GateKapazitaet(
            plaetze=1,
            grund=(
                f"1 Gate-Platz: {frei_mib} MiB frei < {min_frei_mib_zwei_plaetze} MiB "
                "für 2 gleichzeitige Gates (RAM-Schutz)"
            ),
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if last_1m is not None and last_1m >= max_last_zweiter_platz:
        return GateKapazitaet(
            plaetze=1,
            grund=(
                f"1 Gate-Platz: 1-Minuten-Last {last_1m:.2f} >= {max_last_zweiter_platz:.1f} "
                "(2. Platz gesperrt gegen Last >= 12)"
            ),
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    if bau_sessions is not None and bau_sessions > max_bau_sessions_zwei_plaetze:
        return GateKapazitaet(
            plaetze=1,
            grund=(
                f"1 Gate-Platz: {bau_sessions} aktive Bau-Sessions > {max_bau_sessions_zwei_plaetze} "
                "(Schutz gegen Kern-Überbuchung)"
            ),
            bremse_faktor=bremse_faktor,
            frei_mib=frei_mib,
            last_1m=last_1m,
            bau_sessions=bau_sessions,
        )
    return GateKapazitaet(
        plaetze=MAX_GATE_PLAETZE,
        grund=f"2 Gate-Plätze frei (Bremse {bremse_faktor:.2f}× <= {SCHWELLE_ZWEITER_PLATZ:.1f}×, Last/RAM im Rahmen)",
        bremse_faktor=bremse_faktor,
        frei_mib=frei_mib,
        last_1m=last_1m,
        bau_sessions=bau_sessions,
    )


class GatePool:
    """Verwaltet 1–2 Gate-Plätze per Datei-Sperre (:mod:`to_spawn.sperre`) hinter der Vorprüfung.

    Reihenfolge ist fest verdrahtet: :meth:`einlass` führt immer erst :func:`vorpruefen` aus und
    belegt nur bei bestandener Vorprüfung einen freien Gate-Platz. Ein Ticket mit Syntax-Fehler
    oder roter Stufe-3-Auswahl belegt nie einen Gate-Platz.
    """

    def __init__(
        self,
        sperr_ordner: Path,
        *,
        bremse_faktor: float | None = None,
        frei_mib_fn: Callable[[], int | None] | None = None,
        last_1m_fn: Callable[[], float | None] | None = None,
        bau_sessions_fn: Callable[[], int | None] | None = None,
    ) -> None:
        self.sperr_ordner = Path(sperr_ordner)
        self.bremse_faktor = bremse_faktor
        self._frei_mib_fn = frei_mib_fn or speicher.frei_mib
        self._last_1m_fn = last_1m_fn or speicher.last_1m
        self._bau_sessions_fn = bau_sessions_fn or speicher.laufende_sessions
        self._lock = threading.Lock()
        self._versuche = 0
        self._abgewiesen_vorpruefung = 0
        self._belegungen = 0
        self._aktiv = 0
        self._max_gleichzeitig = 0

    @property
    def versuche(self) -> int:
        with self._lock:
            return self._versuche

    @property
    def abgewiesen_vorpruefung(self) -> int:
        with self._lock:
            return self._abgewiesen_vorpruefung

    @property
    def belegungen(self) -> int:
        with self._lock:
            return self._belegungen

    @property
    def max_gleichzeitig(self) -> int:
        with self._lock:
            return self._max_gleichzeitig

    def kapazitaet(self) -> GateKapazitaet:
        """Aktuelle Gate-Kapazität anhand Bremse-Faktor, RAM, 1-Minuten-Last und Bau-Sessions."""
        return erlaubte_gate_plaetze(
            bremse_faktor=self.bremse_faktor,
            frei_mib=self._frei_mib_fn(),
            last_1m=self._last_1m_fn(),
            bau_sessions=self._bau_sessions_fn(),
        )

    @contextlib.contextmanager
    def belege_platz(self, *, warten_s: float = 0.0) -> Iterator[int | None]:
        """Belegt einen freien Gate-Platz (``1`` oder ``2``) unter Beachtung von :meth:`kapazitaet`.

        Liefert die Platz-Nummer (1-basiert) oder ``None``, wenn bis ``warten_s`` kein Platz frei wurde.
        """
        ende = time.monotonic() + max(warten_s, 0.0)
        with contextlib.ExitStack() as stack:
            platz_nr: int | None = None
            while True:
                kap = self.kapazitaet()
                for nr in range(1, kap.plaetze + 1):
                    datei = self.sperr_ordner / f"gate-platz-{nr}.lock"
                    cm = sperre.halte(datei, warten=0.0)
                    gehalten = stack.enter_context(cm)
                    if gehalten:
                        platz_nr = nr
                        break
                    # Nicht gehaltener Kontext sofort wieder schließen, kein FD-Leck beim Warten.
                    stack.pop_all().close()
                if platz_nr is not None:
                    break
                if time.monotonic() >= ende:
                    yield None
                    return
                time.sleep(PLATZ_TAKT_S)
            with self._lock:
                self._belegungen += 1
                self._aktiv += 1
                self._max_gleichzeitig = max(self._max_gleichzeitig, self._aktiv)
            try:
                yield platz_nr
            finally:
                with self._lock:
                    self._aktiv -= 1

    @contextlib.contextmanager
    def einlass(
        self,
        repo: Path,
        basis: str,
        ziel: str,
        *,
        warten_s: float = 0.0,
        isoliert: bool = False,
    ) -> Iterator[EinlassErgebnis]:
        """Führt erst die Vorprüfung (Stufe 0–3) aus und vergibt nur bei Erfolg einen Gate-Platz."""
        with self._lock:
            self._versuche += 1
        urteil = vorpruefen(repo, basis, ziel, isoliert=isoliert)
        if not urteil.zugelassen:
            with self._lock:
                self._abgewiesen_vorpruefung += 1
            yield EinlassErgebnis(
                zugelassen=False,
                gate_platz_belegt=False,
                platz_nr=None,
                warte_s=0.0,
                vorpruefung=urteil,
                kapazitaet=None,
                grund=urteil.grund,
            )
            return

        if not urteil.weiter_an_gate:
            yield EinlassErgebnis(
                zugelassen=True,
                gate_platz_belegt=False,
                platz_nr=None,
                warte_s=0.0,
                vorpruefung=urteil,
                kapazitaet=self.kapazitaet(),
                grund=urteil.grund,
            )
            return

        warte_start = time.monotonic()
        with self.belege_platz(warten_s=warten_s) as platz_nr:
            warte_s = round(time.monotonic() - warte_start, 3)
            kap = self.kapazitaet()
            if platz_nr is None:
                yield EinlassErgebnis(
                    zugelassen=True,
                    gate_platz_belegt=False,
                    platz_nr=None,
                    warte_s=warte_s,
                    vorpruefung=urteil,
                    kapazitaet=kap,
                    grund=f"Vorprüfung bestanden, aber kein Gate-Platz frei ({kap.grund})",
                )
                return
            yield EinlassErgebnis(
                zugelassen=True,
                gate_platz_belegt=True,
                platz_nr=platz_nr,
                warte_s=warte_s,
                vorpruefung=urteil,
                kapazitaet=kap,
                grund=f"Vorprüfung bestanden — Gate-Platz {platz_nr} belegt ({kap.grund})",
            )
