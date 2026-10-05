"""Thermo-Review einer fertigen Spec: Aufbau-Prüfung planen und Befunde im Marker sammeln.

Aufruf:
``python thermo_lauf.py plan <S> [--repo <pfad>] [--basis <sha>] [--kopf <ref>]``
``python thermo_lauf.py sammeln <S> [--repo <pfad>]`` (``--ohne-github`` ohne Wirkung seit E15)

``plan`` bestimmt den Code-Diff der ganzen Spec, teilt ihn auf parallele Prüfer (Subagenten mit
``model: opus``) auf und gibt JSON mit fertigen Prompts aus. Jeder Prompt nennt die neuen Namen der
ganzen Spec (alle Teile), damit der Prüfer ticket-übergreifend Dopplungen findet (E15, #582). Exit 0 = Teile geplant, 2 = keine
Code-Änderung, 4 = schon erledigt (Marker da) oder läuft schon (Sperre jünger als 120 min), 1 = Fehler.

``sammeln`` liest ``<befunde_ordner>/teil-*.json``, dedupliziert, sortiert und schreibt den Marker
``docs/agents/thermo_<S>.md`` — er trägt die Code-Befunde für die Abschluss-Mail, kein GitHub-Issue
(E15, #582). Nur Befunde — kein Umbau in der Spec.
Exit 0 = fertig, 1 = Fehler, 3 = mindestens ein Teil fehlt (kein Marker, ``lauf.json`` bleibt liegen —
fehlenden Teil erneut starten, dann ``sammeln`` wiederholen).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("thermo_lauf")

MAX_DATEIEN = 8
MAX_ZEILEN = 800
MAX_TEILE = 4
SPERRE_MIN = 120
SCHWERE = ("hoch", "mittel", "niedrig")

_CODE_ENDUNGEN = frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".css", ".html", ".sh", ".ps1"})
_RAUS_ORDNER = frozenset({"tests", "docs", "archive", "graphify-out"})
# Alte Marker (vor E15) trugen eine Issue-URL — bleibt für Idempotenz lesbar.
_ISSUE_URL = re.compile(r"^Issue: (https://\S+)", re.M)
MAX_NAMEN = 150
_PY_ENDUNGEN = frozenset({".py"})
_JS_ENDUNGEN = frozenset({".js", ".ts", ".tsx", ".jsx", ".mjs"})
_PY_NAME = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")
_JS_IDENT = r"[A-Za-z_$][\w$]*"
_JS_NAME = re.compile(
    rf"\bfunction\s*\*?\s*({_JS_IDENT})\s*\(|\bclass\s+({_JS_IDENT})"
    rf"|\b(?:const|let)\s+({_JS_IDENT})\s*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|{_JS_IDENT}\s*=>)"
)


class Fehler(Exception):
    """Abbruch mit klarer Meldung (Exit 1)."""


@dataclass
class Grenzen:
    max_dateien: int = MAX_DATEIEN
    max_zeilen: int = MAX_ZEILEN
    max_teile: int = MAX_TEILE


def _git(repo: Path, *args: str) -> str:
    lauf = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if lauf.returncode != 0:
        raise Fehler(f"git {' '.join(args)} fehlgeschlagen: {lauf.stderr.strip()}")
    return lauf.stdout


def befunde_ordner(repo: Path, spec: int) -> Path:
    return repo / ".to-spawn" / f"thermo_{spec}"


def marker_pfad(repo: Path, spec: int) -> Path:
    return repo / "docs" / "agents" / f"thermo_{spec}.md"


def lade_grenzen(repo: Path) -> Grenzen:
    """Grenzen aus ``.to-spawn/config.json`` → ``thermo``, sonst Konstanten."""
    pfad = repo / ".to-spawn" / "config.json"
    try:
        abschnitt = json.loads(pfad.read_text(encoding="utf-8")).get("thermo") or {}
    except FileNotFoundError:
        return Grenzen()
    except (OSError, ValueError, AttributeError) as fehler:
        log.warning("config.json nicht lesbar (%s) — nehme Vorgaben", fehler)
        return Grenzen()
    g = Grenzen()
    for name in ("max_dateien", "max_zeilen", "max_teile"):
        wert = abschnitt.get(name)
        if isinstance(wert, int) and wert > 0:
            setattr(g, name, wert)
    return g


def ist_code(pfad: str) -> bool:
    teile = pfad.split("/")
    name = teile[-1]
    if Path(name).suffix.lower() not in _CODE_ENDUNGEN:
        return False
    if _RAUS_ORDNER.intersection(teile[:-1]):
        return False
    stamm = name.rsplit(".", 1)[0]
    return not (name.startswith("test_") or stamm.endswith("_test") or ".spec." in name)


def lade_manifest(repo: Path, spec: int) -> tuple[Path, dict[str, Any]]:
    pfad = repo / "docs" / "agents" / "manifests" / f"spec-{spec}.json"
    try:
        tickets = json.loads(pfad.read_text(encoding="utf-8")).get("tickets") or {}
    except (OSError, ValueError, AttributeError) as fehler:
        raise Fehler(f"Manifest {pfad} nicht lesbar: {fehler}") from fehler
    if not isinstance(tickets, dict):
        raise Fehler(f"Manifest {pfad}: 'tickets' ist kein dict")
    return pfad, tickets


def basis_aus_manifest(repo: Path, manifest: Path) -> str:
    """Eltern-Commit des Commits, der das Manifest angelegt hat."""
    rel = manifest.relative_to(repo).as_posix()
    anleger = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", rel).split()
    if not anleger:
        raise Fehler(f"Manifest {rel} ist nicht committet — --basis angeben")
    return _git(repo, "rev-parse", "--verify", f"{anleger[-1]}^").strip()


def ticket_dateien(repo: Path, basis: str, kopf: str, nummern: list[str]) -> set[str]:
    """Dateien aus Commits ``basis..kopf``, deren Nachricht ``(N)`` oder ``#N`` nennt."""
    if not nummern:
        return set()
    alt = "|".join(re.escape(n) for n in nummern)
    muster = re.compile(rf"\(#?(?:{alt})\)|(?<![\w#])#(?:{alt})(?!\d)")
    roh = _git(repo, "log", "--no-renames", "--format=%x1e%B%x1f", "--name-only", f"{basis}..{kopf}")
    dateien: set[str] = set()
    for block in roh.split("\x1e")[1:]:
        nachricht, _, namen = block.partition("\x1f")
        if muster.search(nachricht):
            dateien.update(z.strip() for z in namen.splitlines() if z.strip())
    return dateien


def diff_zeilen(repo: Path, basis: str, kopf: str) -> dict[str, int]:
    zeilen: dict[str, int] = {}
    for z in _git(repo, "diff", "--no-renames", "--numstat", f"{basis}..{kopf}").splitlines():
        teile = z.split("\t")
        if len(teile) == 3 and teile[0].isdigit() and teile[1].isdigit():
            zeilen[teile[2]] = int(teile[0]) + int(teile[1])
    return zeilen


def aufteilen(dateien: dict[str, int], g: Grenzen) -> tuple[list[list[str]], list[str]]:
    """Größte zuerst; zu große Datei = eigener Teil; Überlauf über ``max_teile`` → ausgelassen."""
    teile: list[list[str]] = []
    summen: list[int] = []
    for datei, n in sorted(dateien.items(), key=lambda p: (-p[1], p[0])):
        ziel = next(
            (
                i
                for i, t in enumerate(teile)
                if n <= g.max_zeilen and len(t) < g.max_dateien and summen[i] + n <= g.max_zeilen
            ),
            None,
        )
        if ziel is None:
            teile.append([datei])
            summen.append(n)
        else:
            teile[ziel].append(datei)
            summen[ziel] += n
    ausgelassen = [d for t in teile[g.max_teile :] for d in t]
    return teile[: g.max_teile], ausgelassen


def neue_namen(repo: Path, basis: str, kopf: str, dateien: list[str]) -> dict[str, list[str]]:
    """Neu definierte Namen (Python def/class, JS/TS function/class/Pfeil-const) je Datei.

    Heuristik über ``+``-Zeilen des Diffs; Namen, die auch in ``-``-Zeilen derselben Datei stehen
    (geänderte Signatur, verschobene Funktion), gelten als alt und fallen weg.
    """
    if not dateien:
        return {}
    plus: dict[str, set[str]] = {}
    minus: dict[str, set[str]] = {}
    aktuell: str | None = None
    im_kopf = False
    roh = _git(
        repo, "diff", "-U0", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", f"{basis}..{kopf}", "--", *dateien
    )
    for zeile in roh.splitlines():
        if zeile.startswith("diff --git "):
            aktuell, im_kopf = None, True
            continue
        if im_kopf:
            if zeile.startswith("+++ "):
                # Leerzeichen im Pfad: git hängt einen Tab an.
                aktuell = zeile[6:].rstrip("\t") if zeile.startswith("+++ b/") else None
            elif zeile.startswith("@@"):
                im_kopf = False
            continue
        if aktuell is None or not zeile or zeile[0] not in "+-":
            continue
        endung = Path(aktuell).suffix.lower()
        if endung in _PY_ENDUNGEN:
            treffer = [m.group(1)] if (m := _PY_NAME.match(zeile[1:])) else []
        elif endung in _JS_ENDUNGEN:
            treffer = [g for m in _JS_NAME.finditer(zeile[1:]) for g in m.groups() if g]
        else:
            continue
        if treffer:
            (plus if zeile[0] == "+" else minus).setdefault(aktuell, set()).update(treffer)
    namen = {d: sorted(n - minus.get(d, set())) for d, n in sorted(plus.items())}
    return {d: n for d, n in namen.items() if n}


def _namen_zeilen(namen: dict[str, list[str]] | None) -> list[str]:
    """Prompt-Block mit den neuen Namen der ganzen Spec, gedeckelt auf ``MAX_NAMEN``."""
    if namen is None:
        return ["Neue Namen der ganzen Spec: nicht ermittelt (git-Fehler) — Dopplung trotzdem per rg -n prüfen."]
    if not namen:
        return ["Neue Namen der ganzen Spec: keine gefunden (ausgewertet: Python, JS/TS)"]
    zeilen = ["Neue Namen der ganzen Spec (alle Teile):"]
    rest = MAX_NAMEN
    weggelassen = 0
    for datei, liste in namen.items():
        if rest <= 0:
            weggelassen += len(liste)
            continue
        zeilen.append(f"- {datei}: {', '.join(liste[:rest])}")
        weggelassen += max(0, len(liste) - rest)
        rest -= len(liste)
    if weggelassen:
        zeilen.append(f"- … {weggelassen} weitere")
    zeilen.append(
        "Prüfe über Teil-Grenzen hinweg und im übrigen Code (rg -n <name>), ob es das schon gibt: "
        "doppelte Helfer, zwei Namen für dasselbe Ding, auseinanderlaufende Schnittstellen."
    )
    return zeilen


def prompt(
    repo: Path,
    spec: int,
    nr: int,
    gesamt: int,
    basis: str,
    kopf: str,
    dateien: list[str],
    namen: dict[str, list[str]] | None,
) -> str:
    return "\n".join(
        [
            "Schätzung: ~60k Token · Lese-Budget: nur der Diff unten + je Datei max 300 Zeilen Umfeld "
            "+ rg -n für die Namensliste",
            f"Rolle: Prüfer Thermo, Teil {nr}/{gesamt}, Spec #{spec}.",
            "Lies ~/.claude/skills/thermo-nuclear-code-quality-review/SKILL.md und befolge es nur als Prüfer.",
            "Verbote: nie Dateien ändern (kein Edit/Write), keine Git-Schreibbefehle, kein stash/checkout, "
            "keine Hintergrund-Befehle.",
            f"Diff: git -C {repo} diff {basis}..{kopf} -- {' '.join(dateien)}",
            "Fokus Aufbau: Riesen-Dateien, verstreute Sonder-ifs, dünne Wrapper, Umbau-Chancen mit "
            "Verhaltensgleichheit. KEINE Bug-Jagd, kein Stil-Kleinkram (macht review-dirigent).",
            *_namen_zeilen(namen),
            "Höchstens 8 Befunde.",
            "Antwort NUR JSON:",
            f'{{"teil": {nr}, "befunde": [{{"datei": "...", "zeile": 123, "schwere": "hoch|mittel|niedrig", '
            '"titel": "...", "vorschlag": "..."}]}',
        ]
    )


def sperre_aktiv(sperre: Path) -> bool:
    """``True``, solange die Sperre jünger als ``SPERRE_MIN`` ist (Startzeit im JSON, sonst Dateizeit)."""
    try:
        start = datetime.fromisoformat(json.loads(sperre.read_text(encoding="utf-8"))["start"])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError) as fehler:
        log.warning("Sperre %s unlesbar (%s) — nehme Dateizeit", sperre, fehler)
        start = datetime.fromtimestamp(sperre.stat().st_mtime).astimezone()
    return datetime.now().astimezone() - start.astimezone() < timedelta(minutes=SPERRE_MIN)


_sperre_aktiv = sperre_aktiv  # alter Name


def _issue_url(marker: Path) -> str | None:
    try:
        treffer = _ISSUE_URL.search(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return treffer.group(1) if treffer else None


def plan(repo: Path, spec: int, basis: str | None, kopf_ref: str) -> int:
    ordner = befunde_ordner(repo, spec)
    marker = marker_pfad(repo, spec)
    if marker.exists():
        print(f"Schon erledigt: {marker}" + (f" · Issue: {url}" if (url := _issue_url(marker)) else ""))
        return 4
    sperre = ordner / "lauf.json"
    if sperre_aktiv(sperre):
        print(f"Thermo-Lauf Spec #{spec} läuft schon (Sperre {sperre}, jünger als {SPERRE_MIN} min).")
        return 4
    manifest, tickets = lade_manifest(repo, spec)
    basis = _git(repo, "rev-parse", "--verify", f"{basis}^{{commit}}").strip() if basis else None
    basis = basis or basis_aus_manifest(repo, manifest)
    kopf = _git(repo, "rev-parse", "--verify", f"{kopf_ref}^{{commit}}").strip()
    kandidaten = {
        f for t in tickets.values() if isinstance(t, dict) for f in t.get("files") or [] if isinstance(f, str)
    }
    kandidaten |= ticket_dateien(repo, basis, kopf, [str(n) for n in tickets])
    geaendert = diff_zeilen(repo, basis, kopf)
    dateien = {f: geaendert[f] for f in kandidaten if f in geaendert and ist_code(f)}
    if not dateien:
        print(f"Keine Code-Änderung in Spec #{spec} ({basis[:7]}..{kopf[:7]}) — kein Thermo-Lauf.")
        return 2
    gruppen, ausgelassen = aufteilen(dateien, lade_grenzen(repo))
    try:
        namen: dict[str, list[str]] | None = neue_namen(repo, basis, kopf, sorted(dateien))
    except Fehler as fehler:
        log.warning("Namensliste nicht ermittelt (%s) — plane ohne", fehler)
        namen = None
    teile = [
        {
            "nr": nr,
            "dateien": gruppe,
            "diff_zeilen": sum(dateien[d] for d in gruppe),
            "prompt": prompt(repo, spec, nr, len(gruppen), basis, kopf, gruppe, namen),
        }
        for nr, gruppe in enumerate(gruppen, 1)
    ]
    ordner.mkdir(parents=True, exist_ok=True)
    # Neuer Lauf: Antworten eines abgebrochenen Vorlaufs dürfen nicht als vorhanden zählen.
    for alt in ordner.glob("teil-*.json"):
        alt.unlink()
    lauf = {
        "start": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec": spec,
        "basis": basis,
        "kopf": kopf,
        "teile": len(teile),
        "teil_details": [{"nr": t["nr"], "prompt": t["prompt"]} for t in teile],
        "ausgelassen": ausgelassen,
    }
    sperre.write_text(json.dumps(lauf, ensure_ascii=False, indent=2), encoding="utf-8")
    ausgabe = {
        "spec": spec,
        "basis": basis,
        "kopf": kopf,
        "befunde_ordner": str(ordner),
        "teile": teile,
        "ausgelassen": ausgelassen,
    }
    print(json.dumps(ausgabe, ensure_ascii=False, indent=2))
    return 0


def _lies_teil(pfad: Path) -> tuple[list[dict[str, Any]], str]:
    """Befunde einer ``teil-*.json`` — kaputt → ``([], Vermerk)``."""
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
        befunde = roh["befunde"]
        if not isinstance(befunde, list):
            raise TypeError("'befunde' ist keine Liste")
    except (OSError, ValueError, KeyError, TypeError) as fehler:
        return [], f"kaputt ({type(fehler).__name__}: {fehler})"
    gut: list[dict[str, Any]] = []
    for b in befunde:
        if not isinstance(b, dict):
            continue
        schwere = str(b.get("schwere", "")).strip().lower()
        zeile = b.get("zeile")
        gut.append(
            {
                "datei": str(b.get("datei", "")).strip(),
                "zeile": zeile if isinstance(zeile, int) else 0,
                "schwere": schwere if schwere in SCHWERE else "niedrig",
                "titel": str(b.get("titel", "")).strip(),
                "vorschlag": str(b.get("vorschlag", "")).strip(),
            }
        )
    return gut, f"{len(gut)} Befunde"


def _zelle(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _tabelle(befunde: list[dict[str, Any]]) -> list[str]:
    zeilen = ["| Datei:Zeile | Schwere | Titel | Vorschlag |", "|---|---|---|---|"]
    zeilen += [
        f"| `{_zelle(b['datei'])}:{b['zeile']}` | {b['schwere']} | {_zelle(b['titel'])} | {_zelle(b['vorschlag'])} |"
        for b in befunde
    ]
    return zeilen


def _marker_text(
    spec: int, lauf: dict[str, Any], befunde: list[dict[str, Any]], vermerke: list[str]
) -> str:
    basis, kopf = str(lauf.get("basis") or "?")[:7], str(lauf.get("kopf") or "?")[:7]
    zeilen = [
        f"# Thermo-Review Spec #{spec}",
        "",
        f"Stand: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Basis..Kopf: `{basis}..{kopf}`",
        "Code-Befunde: gehen als dritter Teil in die Abschluss-Mail (#586, E15).",
        "",
        f"## Befunde ({len(befunde)})",
        "",
    ]
    zeilen += _tabelle(befunde) if befunde else ["Keine Befunde."]
    zeilen += ["", "## Teile", ""] + [f"- {v}" for v in vermerke]
    ausgelassen = lauf.get("ausgelassen") or []
    zeilen += ["", "## Ausgelassene Dateien", ""] + ([f"- `{d}`" for d in ausgelassen] or ["Keine."])
    return "\n".join(zeilen) + "\n"


_TEIL_DATEI = re.compile(r"teil-(\d+)\.json")


def _erwartete_teile(lauf: dict[str, Any]) -> set[int] | None:
    """Teil-Nummern aus ``lauf.json``, oder ``None`` wenn unbekannt (kein Abgleich möglich)."""
    details = lauf.get("teil_details")
    if isinstance(details, list) and details:
        nrs = {d.get("nr") for d in details if isinstance(d, dict) and isinstance(d.get("nr"), int)}
        if nrs:
            return nrs
    anzahl = lauf.get("teile")
    if isinstance(anzahl, int) and anzahl > 0:
        return set(range(1, anzahl + 1))
    return None


def _vorhandene_teile(ordner: Path) -> set[int]:
    gefunden = set()
    for pfad in ordner.glob("teil-*.json"):
        treffer = _TEIL_DATEI.fullmatch(pfad.name)
        if treffer:
            gefunden.add(int(treffer.group(1)))
    return gefunden


def _fehlende_teile_meldung(fehlend: list[int], lauf: dict[str, Any]) -> str:
    prompts = {
        d.get("nr"): d.get("prompt")
        for d in (lauf.get("teil_details") or [])
        if isinstance(d, dict) and isinstance(d.get("nr"), int)
    }
    bloecke = []
    for nr in fehlend:
        zeile = f"Teil {nr} fehlt — Subagent für diesen Teil erneut starten, dann sammeln erneut aufrufen."
        prompt_text = prompts.get(nr)
        if prompt_text:
            zeile += f"\nPrompt Teil {nr}:\n{prompt_text}"
        bloecke.append(zeile)
    return "\n\n".join(bloecke)


def sammeln(repo: Path, spec: int) -> int:
    ordner = befunde_ordner(repo, spec)
    marker = marker_pfad(repo, spec)
    sperre = ordner / "lauf.json"
    if url := _issue_url(marker):
        print(url)
        return 0
    try:
        lauf = json.loads(sperre.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("lauf.json fehlt/unlesbar (%s) — Basis/Kopf unbekannt", fehler)
        lauf = {}
    erwartet = _erwartete_teile(lauf)
    if erwartet is not None:
        fehlend = sorted(erwartet - _vorhandene_teile(ordner))
        if fehlend:
            print(_fehlende_teile_meldung(fehlend, lauf))
            return 3
    try:
        befunde: list[dict[str, Any]] = []
        vermerke: list[str] = []
        gesehen: set[tuple[str, int, str]] = set()
        for pfad in sorted(ordner.glob("teil-*.json")):
            teil, vermerk = _lies_teil(pfad)
            vermerke.append(f"{pfad.name}: {vermerk}")
            for b in teil:
                schluessel = (b["datei"], b["zeile"], b["titel"].lower())
                if schluessel not in gesehen:
                    gesehen.add(schluessel)
                    befunde.append(b)
        befunde.sort(key=lambda b: (SCHWERE.index(b["schwere"]), b["datei"], b["zeile"]))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_marker_text(spec, lauf, befunde, vermerke), encoding="utf-8")
        print(f"Marker: {marker} · {len(befunde)} Befunde")
        return 0
    finally:
        sperre.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Thermo-Review einer Spec planen und Befunde sammeln.")
    unter = parser.add_subparsers(dest="befehl", required=True)
    p_plan = unter.add_parser("plan")
    p_plan.add_argument("spec", type=int)
    p_plan.add_argument("--repo", default=".")
    p_plan.add_argument("--basis")
    p_plan.add_argument("--kopf", default="origin/master")
    p_sam = unter.add_parser("sammeln")
    p_sam.add_argument("spec", type=int)
    p_sam.add_argument("--repo", default=".")
    p_sam.add_argument("--ohne-github", action="store_true", help="ohne Wirkung seit E15 (kein Issue mehr)")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.befehl == "plan":
            return plan(repo, args.spec, args.basis, args.kopf)
        return sammeln(repo, args.spec)
    except Fehler as fehler:
        log.error("%s", fehler)
        return 1


if __name__ == "__main__":
    sys.exit(main())
