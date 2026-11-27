"""Git-Plumbing-Probe für den verwaisten Ereignis-Zweig ``protokoll`` (#901 / App #1387).

Legt den verwaisten Zweig ohne Checkout und ohne Eingriff in den Arbeitsordner an,
hängt Zeilen an eine Protokolldatei an und setzt Push-Konflikte mehrerer gleichzeitiger
Schreiber verlustfrei neu auf.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from to_spawn.prozessbaum import fensterlos

LOG = logging.getLogger(__name__)

DEFAULT_ZWEIG = "protokoll"
DEFAULT_DATEI = "ereignisse.jsonl"
DEFAULT_AUTOR_NAME = "protokoll-probe"
DEFAULT_AUTOR_EMAIL = "protokoll-probe@local"


@dataclass(frozen=True)
class ProbeErgebnis:
    """Ergebnis eines Plumbing-Schreibvorgangs auf den Ereignis-Zweig."""

    erfolg: bool
    commit_sha: str
    versuche: int
    fehler: str = ""


def _git_umgebung() -> dict[str, str]:
    """Erzeugt eine vollständige Git-Umgebung mit Fallback-Identität für Plumbing-Commits."""
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", DEFAULT_AUTOR_NAME)
    env.setdefault("GIT_AUTHOR_EMAIL", DEFAULT_AUTOR_EMAIL)
    env.setdefault("GIT_COMMITTER_NAME", DEFAULT_AUTOR_NAME)
    env.setdefault("GIT_COMMITTER_EMAIL", DEFAULT_AUTOR_EMAIL)
    return env


def _git(
    repo_pfad: Path,
    *args: str,
    eingabe: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Führt einen Git-Unterprozess fensterlos im angegebenen Repository aus.

    Übergibt ``stdin`` als UTF-8-Bytes (ohne Text-Modus), damit Windows kein
    ``\\n`` in ``\\r\\n`` umschreibt: ``git mktree`` würde das ``\\r`` sonst in
    den Dateinamen übernehmen und ``git hash-object`` CRLF-Blobs erzeugen.
    """
    roh_eingabe = eingabe.encode() if eingabe is not None else None
    res = subprocess.run(
        ["git", *args],
        cwd=repo_pfad,
        input=roh_eingabe,
        capture_output=True,
        check=False,
        env=_git_umgebung(),
        **fensterlos(),
    )
    stdout = res.stdout.decode("utf-8", errors="replace")
    stderr = res.stderr.decode("utf-8", errors="replace")
    if check and res.returncode != 0:
        raise subprocess.CalledProcessError(res.returncode, res.args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(res.args, res.returncode, stdout=stdout, stderr=stderr)


def _remote_kopf_holen(repo_pfad: Path, remote: str, zweig: str) -> str | None:
    """Holt den aktuellen Stand von ``refs/heads/<zweig>`` vom Remote und liefert dessen SHA."""
    ls_res = _git(repo_pfad, "ls-remote", "--heads", remote, zweig, check=False)
    if ls_res.returncode != 0:
        raise RuntimeError(f"git ls-remote fehlgeschlagen: {ls_res.stderr.strip()}")
    if not ls_res.stdout.strip():
        return None

    fetch_res = _git(repo_pfad, "fetch", "--no-tags", remote, f"refs/heads/{zweig}", check=False)
    if fetch_res.returncode != 0:
        raise RuntimeError(f"git fetch fehlgeschlagen: {fetch_res.stderr.strip()}")

    rev_res = _git(repo_pfad, "rev-parse", "FETCH_HEAD")
    return rev_res.stdout.strip()


def lese_zweig_inhalt(repo_pfad: Path, zweig: str, dateiname: str = DEFAULT_DATEI) -> str:
    """Liest ``dateiname`` direkt aus ``refs/heads/<zweig>`` ohne Checkout."""
    res = _git(repo_pfad, "cat-file", "-p", f"refs/heads/{zweig}:{dateiname}", check=False)
    if res.returncode != 0:
        return ""
    return res.stdout


def _lese_commit_datei(repo_pfad: Path, commit_sha: str, dateiname: str) -> str:
    """Liest eine Datei aus einem bestimmten Commit per Git-Plumbing."""
    res = _git(repo_pfad, "cat-file", "-p", f"{commit_sha}:{dateiname}", check=False)
    if res.returncode != 0:
        return ""
    return res.stdout


def _baue_tree_mit_datei(
    repo_pfad: Path,
    parent_sha: str | None,
    dateiname: str,
    blob_sha: str,
) -> str:
    """Erstellt ein neues Tree-Objekt mit aktualisierter Datei und behält übrige Einträge bei."""
    eintraege: list[str] = []
    if parent_sha is not None:
        ls_tree = _git(repo_pfad, "ls-tree", parent_sha, check=False)
        if ls_tree.returncode == 0:
            for zeile in ls_tree.stdout.splitlines():
                if not zeile.strip():
                    continue
                _, _, rest = zeile.partition("\t")
                if rest == dateiname:
                    continue
                eintraege.append(zeile)

    eintraege.append(f"100644 blob {blob_sha}\t{dateiname}")
    tree_eingabe = "\n".join(eintraege) + "\n"
    return _git(repo_pfad, "mktree", eingabe=tree_eingabe).stdout.strip()


def anhaengen_plumbing(
    repo_pfad: Path,
    remote: str,
    zeile: str,
    zweig: str = DEFAULT_ZWEIG,
    dateiname: str = DEFAULT_DATEI,
    nachricht: str = "protokoll: Ereignis anhängen",
    max_versuche: int = 10,
) -> ProbeErgebnis:
    """Hängt eine Zeile per Git-Plumbing an ``dateiname`` auf ``zweig`` an und pusht verlustfrei.

    Existiert ``zweig`` noch nicht auf ``remote``, wird ein verwaister Erst-Commit ohne Eltern
    erzeugt. Bei Push-Konflikten durch parallele Schreiber wird der neue Remote-Stand geholt,
    die eigene Zeile obenauf gesetzt und erneut ohne ``--force`` gepusht.
    """
    bereinigte_zeile = zeile.rstrip("\r\n")
    letzter_fehler = ""

    for versuch in range(1, max_versuche + 1):
        try:
            parent_sha = _remote_kopf_holen(repo_pfad, remote, zweig)
        except RuntimeError as exc:
            letzter_fehler = str(exc)
            LOG.warning("Versuch %d/%d: Remote-Stand nicht lesbar: %s", versuch, max_versuche, exc)
            time.sleep(random.uniform(0.02, 0.12))
            continue

        bisher = _lese_commit_datei(repo_pfad, parent_sha, dateiname) if parent_sha else ""
        if bisher and not bisher.endswith("\n"):
            bisher += "\n"
        neuer_inhalt = f"{bisher}{bereinigte_zeile}\n"

        blob_sha = _git(repo_pfad, "hash-object", "-w", "--stdin", eingabe=neuer_inhalt).stdout.strip()
        tree_sha = _baue_tree_mit_datei(repo_pfad, parent_sha, dateiname, blob_sha)

        commit_args = ["commit-tree", tree_sha, "-m", nachricht]
        if parent_sha is not None:
            commit_args.extend(["-p", parent_sha])
        commit_sha = _git(repo_pfad, *commit_args).stdout.strip()

        push_res = _git(repo_pfad, "push", remote, f"{commit_sha}:refs/heads/{zweig}", check=False)
        if push_res.returncode == 0:
            LOG.info(
                "Plumbing-Push auf %s (%s) erfolgreich nach %d Versuch(en): %s", zweig, remote, versuch, commit_sha
            )
            return ProbeErgebnis(erfolg=True, commit_sha=commit_sha, versuche=versuch)

        letzter_fehler = push_res.stderr.strip() or push_res.stdout.strip()
        LOG.info(
            "Versuch %d/%d: Push-Konflikt auf %s, setze neu auf (%s)", versuch, max_versuche, zweig, letzter_fehler
        )
        time.sleep(random.uniform(0.02, 0.12))

    return ProbeErgebnis(erfolg=False, commit_sha="", versuche=max_versuche, fehler=letzter_fehler)


def _gh_run_list_ids(repo_pfad: Path) -> list[str]:
    """Liest die jüngsten Workflow-Lauf-IDs über ``gh run list`` für die GitHub-Probe."""
    res = subprocess.run(
        ["gh", "run", "list", "--limit", "10", "--json", "databaseId", "-q", ".[].databaseId"],
        cwd=repo_pfad,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
        **fensterlos(),
    )
    return [z.strip() for z in res.stdout.splitlines() if z.strip()]


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale oder einmalige GitHub-Plumbing-Proben."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Git-Plumbing-Probe für den verwaisten Zweig protokoll.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Pfad zum Git-Repository.")
    parser.add_argument("--remote", default="origin", help="Remote-Name oder URL.")
    parser.add_argument("--zweig", default=DEFAULT_ZWEIG, help="Zielzweig (Standard: protokoll).")
    parser.add_argument("--datei", default=DEFAULT_DATEI, help="Protokolldatei im Zweig.")
    parser.add_argument("--zeile", required=True, help="Anzuhängende Text- oder JSON-Zeile.")
    parser.add_argument("--max-versuche", type=int, default=10, help="Maximale Anzahl Push-Versuche bei Konflikten.")
    parser.add_argument(
        "--github-probe",
        action="store_true",
        help="Prüft zusätzlich gh run list vorher/nachher, um CI-/Cron-Auslösungen auszuschließen.",
    )
    args = parser.parse_args(argv)

    vorher_runs: list[str] = []
    if args.github_probe:
        vorher_runs = _gh_run_list_ids(args.repo)
        LOG.info("gh run list vorher: %s", vorher_runs)

    ergebnis = anhaengen_plumbing(
        repo_pfad=args.repo,
        remote=args.remote,
        zeile=args.zeile,
        zweig=args.zweig,
        dateiname=args.datei,
        max_versuche=args.max_versuche,
    )
    if not ergebnis.erfolg:
        LOG.error("Plumbing-Probe fehlgeschlagen nach %d Versuchen: %s", ergebnis.versuche, ergebnis.fehler)
        return 1

    if args.github_probe:
        nachher_runs = _gh_run_list_ids(args.repo)
        neue_runs = [r for r in nachher_runs if r not in vorher_runs]
        LOG.info("gh run list nachher: %s (neu: %s)", nachher_runs, neue_runs)
        if neue_runs:
            LOG.warning("GitHub-Probe: NEIN – neue Workflow-Läufe ausgelöst: %s", neue_runs)
            return 2
        LOG.info("GitHub-Probe: JA – Zweig %s per Plumbing gepusht ohne neue CI-/Cron-Läufe.", args.zweig)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
