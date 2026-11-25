"""Beleg schreiben / prüfen / zusammenfassen (VERTRAG.md §5, §6)."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from _gemeinsam import (
    MOMENTE,
    STUFEN,
    DateiDiff,
    KEIN_SHA,
    EingabeFehler,
    beleg_pfad,
    beleg_status,
    berechne_route,
    datei_hash,
    diff_argumente,
    git,
    jetzt,
    lade_config,
    logging_einrichten,
    repo_wurzel,
)

LOG = logging.getLogger("review.beleg")
SCHWEREN = ("hoch", "mittel", "niedrig")
BEFUND_STATUS = ("behalten", "gestrichen")


def datei_hashes_berechnen(dateien: list[str], diffs: dict[str, DateiDiff]) -> dict[str, str]:
    """Für schreiben()/Aufrufer: datei_hashes-Feld aus den aktuellen Diffs bauen."""
    return {p: datei_hash(diffs[p]) for p in dateien if p in diffs}


def _deckt_ab(beleg: dict[str, Any], feld: str, dateien: list[str], hashes: dict[str, str]) -> bool:
    """Alle Dateien im Beleg und je Datei gleicher Hash in ``beleg[feld]``; kaputte Felder → False."""
    beleg_hashes = beleg.get(feld)
    beleg_dateien = beleg.get("dateien")
    if not isinstance(beleg_hashes, dict) or not beleg_hashes or not isinstance(beleg_dateien, list):
        return False
    bekannt = {p for p in beleg_dateien if isinstance(p, str)}
    return all(p in bekannt and hashes.get(p) and hashes[p] == beleg_hashes.get(p) for p in dateien)


def beleg_gueltig_teilmenge(beleg: dict[str, Any], sha: str, dateien: list[str], datei_hashes: dict[str, str]) -> bool:
    """Beleg deckt Dateimenge B ab: gleiche sha, alle Dateien von B im Beleg, Hashes gleich.
    Beleg ohne datei_hashes (alt) → immer False (abwärtskompatibel, nur fingerprint zählt dann)."""
    if beleg.get("sha") != sha:
        return False
    return _deckt_ab(beleg, "datei_hashes", dateien, datei_hashes)


def beleg_inhalt_gleich(beleg: dict[str, Any], dateien: list[str], inhalt_hashes: dict[str, str]) -> bool:
    """Wie beleg_gueltig_teilmenge, aber über inhalt_hashes und ohne sha-Bindung: für die Suche
    nach Commit. Beleg ohne inhalt_hashes (alt) → False, dann gilt nur der sha-gebundene Weg."""
    return bool(dateien) and _deckt_ab(beleg, "inhalt_hashes", dateien, inhalt_hashes)


def beleg_gueltig_pruefen(
    beleg: dict[str, Any],
    sha: str,
    fingerprint: str,
    dateien: list[str],
    datei_hashes: dict[str, str] | None = None,
) -> bool:
    """Gültig wenn fingerprint gleich ODER Dateimenge B Teilmenge von A mit gleichen Datei-Hashes."""
    if beleg.get("fingerprint") == fingerprint:
        return True
    return beleg_gueltig_teilmenge(beleg, sha, dateien, datei_hashes or {})


def _lade_json(pfad: Path) -> dict[str, Any]:
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as fehler:
        raise EingabeFehler(f"{pfad} unlesbar: {fehler}") from fehler
    if not isinstance(daten, dict):
        raise EingabeFehler(f"{pfad}: JSON-Objekt erwartet")
    return daten


def validieren(entwurf: dict[str, Any]) -> None:
    if entwurf.get("moment") not in MOMENTE:
        raise EingabeFehler(f"moment ungültig: {entwurf.get('moment')!r}")
    if entwurf.get("stufe") not in STUFEN:
        raise EingabeFehler(f"stufe ungültig: {entwurf.get('stufe')!r}")
    for schluessel in ("sha", "fingerprint"):
        if not isinstance(entwurf.get(schluessel), str) or not entwurf[schluessel]:
            raise EingabeFehler(f"{schluessel} fehlt")
    if entwurf.get("richter") is not None and not isinstance(entwurf["richter"], dict):
        raise EingabeFehler("richter muss Objekt oder null sein")
    if "diff_modus" in entwurf:
        dm = entwurf["diff_modus"]
        if (
            not isinstance(dm, dict)
            or not isinstance(dm.get("staged", False), bool)
            or not (dm.get("base") is None or isinstance(dm.get("base"), str))
        ):
            raise EingabeFehler("diff_modus muss {staged: bool, base: Text|null} sein")
    if entwurf.get("repo") is not None and not isinstance(entwurf["repo"], str):
        raise EingabeFehler("repo muss Text sein")
    for liste in ("befunde", "pruefer", "dateien"):
        if not isinstance(entwurf.get(liste, []), list):
            raise EingabeFehler(f"{liste} muss Liste sein")
    for feld in ("datei_hashes", "inhalt_hashes"):
        dh = entwurf.get(feld)
        if dh is not None and (not isinstance(dh, dict) or not all(isinstance(v, str) for v in dh.values())):
            raise EingabeFehler(f"{feld} muss Objekt {{pfad: hash}} sein")
    for i, b in enumerate(entwurf.get("befunde", [])):
        if not isinstance(b, dict):
            raise EingabeFehler(f"befunde[{i}] kein Objekt")
        if b.get("schwere") not in SCHWEREN:
            raise EingabeFehler(f"befunde[{i}].schwere ungültig: {b.get('schwere')!r}")
        if b.get("status") not in BEFUND_STATUS:
            raise EingabeFehler(f"befunde[{i}].status ungültig: {b.get('status')!r}")
        for text_feld in ("pruefer", "grund"):  # pruefer darf "a+b" sein
            if text_feld in b and not isinstance(b[text_feld], str):
                raise EingabeFehler(f"befunde[{i}].{text_feld} muss Text sein")
        konf = b.get("konfidenz")
        if isinstance(konf, bool) or not isinstance(konf, int | float) or not 0 <= konf <= 100:
            raise EingabeFehler(f"befunde[{i}].konfidenz ungültig: {konf!r}")


_MOMENTE_MIT_DIFF_MODUS = ("commit", "ticket", "deploy")


def schreiben(entwurf_pfad: str, repo_arg: str | None) -> int:
    cfg = lade_config()
    entwurf = _lade_json(Path(entwurf_pfad))
    validieren(entwurf)
    # Repo aus --repo, sonst aus dem Entwurf (review_route-JSON), erst zuletzt cwd.
    repo = repo_wurzel(Path(repo_arg or entwurf.get("repo") or Path.cwd()).resolve())
    if repo is None:
        raise EingabeFehler("kein Git-Repo")
    sha = entwurf["sha"]
    if sha != KEIN_SHA and not git(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}", check=False).strip():
        raise EingabeFehler(f"sha {sha} gehört nicht zu Repo {repo.as_posix()} – --repo oder Entwurf-Feld repo setzen")
    diff_modus = entwurf.get("diff_modus")
    # Ohne diff_modus ist bei commit/ticket/deploy unklar, ob staged oder gegen base gehasht
    # werden muss -> nicht nachtragen, nur fingerprint gilt.
    nachtragen = isinstance(diff_modus, dict) or entwurf["moment"] not in _MOMENTE_MIT_DIFF_MODUS
    fehlt_hash = "datei_hashes" not in entwurf or "inhalt_hashes" not in entwurf
    if fehlt_hash and entwurf.get("dateien") and nachtragen:
        # Dirigent muss das Feld nicht kennen: aus dem Diff derselben Dateien im selben Modus nachtragen.
        modus = diff_modus if isinstance(diff_modus, dict) else {}
        try:
            route = berechne_route(
                repo.as_posix(),
                entwurf["moment"],
                base=modus.get("base"),
                staged=bool(modus.get("staged", False)),
                files=list(entwurf["dateien"]),
            )
        except (EingabeFehler, OSError, subprocess.SubprocessError) as fehler:
            LOG.warning("datei_hashes nicht berechenbar: %s", fehler)
        else:
            if route.get("sha") == entwurf.get("sha") and route.get("fingerprint") == entwurf.get("fingerprint"):
                entwurf.setdefault("datei_hashes", route.get("datei_hashes", {}))
                entwurf.setdefault("inhalt_hashes", route.get("inhalt_hashes", {}))
            else:
                LOG.warning(
                    "datei_hashes nicht nachgetragen: Arbeitsbaum hat sich seit dem Entwurf geändert "
                    "(fingerprint weicht ab)"
                )
    beleg = {
        "version": 1,
        "modus": cfg.get("modus", "hinweis"),
        "zeit": jetzt(),
        "befunde": [],
        "pruefer": [],
        "fix_runde": 0,
        **entwurf,
    }
    beleg["status"] = beleg_status(beleg["befunde"], cfg)
    ziel = beleg_pfad(repo, beleg["sha"], beleg["moment"], beleg["fingerprint"])
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ignore = ziel.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    ziel.write_text(json.dumps(beleg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    behalten = sum(1 for b in beleg["befunde"] if b["status"] == "behalten")
    print(ziel.as_posix())
    print(f"Status: {beleg['status']} ({behalten} behaltene Befunde, Stufe {beleg['stufe']})")
    return 0


def pruefen(args: argparse.Namespace) -> int:
    route = berechne_route(args.repo, args.moment, args.base, args.staged, args.files)
    if route["stufe"] == "keine":
        return 0
    grund = ""
    if route["beleg_gueltig"] and route["beleg"]:
        pfad = Path(route["beleg"])  # auch per Inhalt gefunden (nach Commit)
    else:
        pfad = beleg_pfad(Path(route["repo"]), route["sha"], route["moment"], route["fingerprint"])
    if not pfad.is_file():
        grund = "Beleg fehlt"
    else:
        beleg = _lade_json(pfad)
        if not route["beleg_gueltig"]:
            grund = "Beleg veraltet (fingerprint alt)"
        elif beleg_status(beleg.get("befunde", []), lade_config()) == "rot":
            grund = "Beleg rot"
    if not grund:
        return 0
    satz = (
        f"{grund}. Review fällig ({route['moment']}, Stufe {route['stufe']}): "
        f"Skill review-dirigent ausführen mit moment={route['moment']}."
    )
    if route["modus"] == "scharf":
        sys.stderr.write(satz + "\n")
        return 1
    sys.stderr.write(f"Hinweis (modus hinweis, nicht blockiert): {satz}\n")
    return 0


def kurz(pfad: str) -> int:
    b = _lade_json(Path(pfad))
    zeilen = [
        f"Beleg {b.get('moment')} {b.get('sha')}: {str(b.get('status', '?')).upper()} "
        f"(Stufe {b.get('stufe')}, Modus {b.get('modus')}, Fix-Runde {b.get('fix_runde', 0)})",
        "Prüfer: "
        + (", ".join(f"{p.get('name')} ({p.get('befunde_roh', 0)} roh)" for p in b.get("pruefer", [])) or "–"),
    ]
    richter = b.get("richter")
    if isinstance(richter, dict):
        zeilen.append(f"Richter {richter.get('modell')}: {richter.get('gestrichen', 0)} gestrichen")
    rang = {s: i for i, s in enumerate(SCHWEREN)}
    behalten = sorted(
        (x for x in b.get("befunde", []) if x.get("status") == "behalten"),
        key=lambda x: (rang.get(x.get("schwere"), 9), -x.get("konfidenz", 0)),
    )
    platz = 15 - len(zeilen)
    zeigen = behalten if len(behalten) <= platz else behalten[: platz - 1]
    for x in zeigen:
        zeilen.append(
            f"- {x.get('schwere')} {x.get('konfidenz')} {x.get('datei')}:{x.get('zeile', '?')} "
            f"{x.get('text', '')} ({x.get('pruefer', '?')})"
        )
    if len(zeigen) < len(behalten):
        zeilen.append(f"… +{len(behalten) - len(zeigen)} weitere Befunde")
    print("\n".join(zeilen))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging_einrichten()
    parser = argparse.ArgumentParser(description="Review-Beleg schreiben/prüfen.")
    unter = parser.add_subparsers(dest="befehl", required=True)
    p_schreiben = unter.add_parser("schreiben")
    p_schreiben.add_argument("--datei", required=True)
    p_schreiben.add_argument("--repo")
    p_pruefen = unter.add_parser("pruefen")
    p_pruefen.add_argument("--moment", required=True, choices=MOMENTE)
    diff_argumente(p_pruefen)
    p_kurz = unter.add_parser("kurz")
    p_kurz.add_argument("--datei", required=True)
    args = parser.parse_args(argv)
    try:
        if args.befehl == "schreiben":
            return schreiben(args.datei, args.repo)
        if args.befehl == "pruefen":
            return pruefen(args)
        return kurz(args.datei)
    except EingabeFehler as fehler:
        LOG.error("%s", fehler)
        return 2


if __name__ == "__main__":
    sys.exit(main())
