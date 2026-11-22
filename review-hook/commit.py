"""PreToolUse Bash (Moment commit): Review-Hinweis bzw. Gate vor `git commit` (VERTRAG.md §8)."""

from __future__ import annotations

import os
import re
import shlex
import time
from pathlib import Path
from typing import Any

from _hookbasis import (
    BELEG,
    LOG,
    ausfuehren,
    befehl as git_befehl,
    dateien_anhaengen,
    lade_sitzung,
    python_skript,
    route,
    speichere_sitzung,
)
from merker import nicht_merken, start_sha_merken

# git [-C pfad | -c k=v]* commit — nicht commit-tree o. ä.
GIT_COMMIT = re.compile(r"""\bgit((?:\s+-[Cc]\s*(?:"[^"]*"|'[^']*'|[^\s;&|]+))*)\s+commit(?![-\w])""")
C_OPTION = re.compile(r"""-C\s*("[^"]*"|'[^']*'|[^\s;&|]+)""")

# Shell-Trenner, die die commit-Argumentliste beenden (nächster Befehl beginnt).
_TRENNER = {";", "&&", "||", "|", "&"}
# git commit: Kurz-/Langflags, die einen eigenständigen Wert verlangen (nicht -a).
_WERT_KURZ = {"m", "c", "C", "F", "t"}
# Kurzflags mit optionalem Wert, der nur angehängt vorkommt (nie als eigenes Token) —
# z. B. -S<keyid>, -u<modus>. Kein `ueberspringen`, sonst frisst es ein folgendes -a.
_WERT_KURZ_OPTIONAL = {"S", "u"}
_WERT_LANG = {
    "--message",
    "--reuse-message",
    "--reedit-message",
    "--file",
    "--author",
    "--date",
    "--template",
}


# `cd <pfad>` am Anfang eines Befehlsglieds (vor dem git commit derselben Kette).
CD_BEFEHL = re.compile(r"""(?:^|[;&|(]\s*)cd\s+("[^"]*"|'[^']*'|[^\s;&|)]+)""")
# Git-Bash-Pfad /c/… → C:/…
_GIT_BASH_LAUFWERK = re.compile(r"^/([a-zA-Z])(/|$)")


def _cd_ziel(roh: str, basis: Path) -> Path:
    text = roh.strip("\"'")
    if os.name == "nt":
        text = _GIT_BASH_LAUFWERK.sub(lambda m: f"{m.group(1).upper()}:/", text)
    return basis / Path(text).expanduser()


def repo_aus_befehl(befehl: str, cwd: Path) -> Path | None:
    treffer = GIT_COMMIT.search(befehl)
    if treffer is None:
        return None
    repo = cwd
    # Runde 6: `cd ~/.claude && git commit …` aus einer Session mit anderem cwd.
    for cd in CD_BEFEHL.finditer(befehl, 0, treffer.start()):
        repo = _cd_ziel(cd.group(1), repo)
    for c in C_OPTION.findall(treffer.group(1)):
        repo = repo / c.strip("\"'")
    return repo


def _commit_argumente(befehl: str, ab: int) -> list[str]:
    """Tokens nach `commit` bis zum nächsten Shell-Trenner, Anführungszeichen beachtet."""
    try:
        lexer = shlex.shlex(befehl[ab:], posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens: list[str] = []
        for tok in lexer:
            if tok in _TRENNER:
                break
            tokens.append(tok)
        return tokens
    except ValueError:
        return []


def hat_a_flag(befehl: str) -> bool:
    """`-a`/`--all` erkennen — kombinierte Kurzflags, `--amend` und `-m "-a"` ausgenommen."""
    treffer = GIT_COMMIT.search(befehl)
    if treffer is None:
        return False
    ueberspringen = False
    for tok in _commit_argumente(befehl, treffer.end()):
        if ueberspringen:
            ueberspringen = False
            continue
        if tok == "--":
            break
        if tok in ("-a", "--all"):
            return True
        if tok.startswith("--"):
            if "=" not in tok and tok in _WERT_LANG:
                ueberspringen = True
            continue
        if tok.startswith("-") and len(tok) > 1:
            rest = tok[1:]
            for i, zeichen in enumerate(rest):
                if zeichen == "a":
                    return True
                if zeichen in _WERT_KURZ:
                    if i == len(rest) - 1:
                        ueberspringen = True
                    break
                if zeichen in _WERT_KURZ_OPTIONAL:
                    break
    return False


def pathspec_aus_befehl(befehl: str) -> list[str]:
    """Pfade nach `--` im git-commit-Aufruf (bis zum nächsten Shell-Trenner)."""
    treffer = GIT_COMMIT.search(befehl)
    if treffer is None:
        return []
    tokens = _commit_argumente(befehl, treffer.end())
    return tokens[tokens.index("--") + 1 :] if "--" in tokens else []


def _git_namen(repo: Path, *args: str) -> list[str]:
    ergebnis = git_befehl(["git", "-C", str(repo), *args])
    if ergebnis.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {ergebnis.stderr.strip()}")
    return [x for x in ergebnis.stdout.split("\0") if x]


def commit_dateien(befehl: str, repo: Path) -> list[Path]:
    """Dateien, die dieser commit mitnimmt: Pathspec nach `--`, sonst Index (+ getrackte bei -a).
    Pathspec-Dateien direkt (auch per `git add -f` ignorierte), Ordner über git status."""
    wurzel = Path(_git_namen(repo, "rev-parse", "--show-toplevel")[0].strip())
    pathspec = pathspec_aus_befehl(befehl)
    if pathspec:
        dateien = [repo / x for x in pathspec if not (repo / x).is_dir()]
        ordner = [x for x in pathspec if (repo / x).is_dir()]
        if ordner:
            status = _git_namen(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", *ordner)
            dateien += [wurzel / e[3:] for e in status if len(e) > 3]
        return dateien
    namen = _git_namen(wurzel, "diff", "--cached", "--name-only", "-z", "--no-renames")
    if hat_a_flag(befehl):
        namen += _git_namen(wurzel, "diff", "HEAD", "--name-only", "-z", "--no-renames")
    return [wurzel / n for n in dict.fromkeys(namen)]


def commit_dateien_merken(eingabe: dict[str, Any], befehl: str, repo: Path) -> None:
    """Runde 6: Per Skript geänderte, selbst committete Dateien sieht der Merker (Edit/Write)
    nie; stop.py verwarf sie als „nur committet, Merker kennt sie nicht“. Der eigene Commit
    läuft durch diesen Hook, fremde Commits paralleler Sessions nie → hier merken ist sicher."""
    session_id = eingabe.get("session_id")
    if not session_id:
        return
    try:
        dateien = [d.resolve() for d in commit_dateien(befehl, repo)]
    except (RuntimeError, OSError, IndexError) as fehler:
        LOG.warning("commit-Dateien nicht gemerkt: %s", fehler)
        return
    dateien = [d for d in dateien if not nicht_merken(d)]
    if not dateien:
        return
    sid = str(session_id)
    sitzung = lade_sitzung(sid)
    sitzung.setdefault("start", time.time())
    for datei in dateien:
        text = datei.as_posix()
        dateien_anhaengen(sid, text)
        if text not in sitzung["dateien"]:
            sitzung["dateien"].append(text)
    start_sha_merken(sitzung, dateien[0])  # HEAD vor dem Commit = Basis für stop.py
    speichere_sitzung(sid, sitzung)


def _kontext(text: str, **extra: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
            **extra,
        }
    }


def haupt(eingabe: dict[str, Any]) -> dict[str, Any] | None:
    befehl = str((eingabe.get("tool_input") or {}).get("command") or "")
    repo = repo_aus_befehl(befehl, Path(eingabe.get("cwd") or Path.cwd()))
    if repo is None:
        return None
    commit_dateien_merken(eingabe, befehl, repo)
    # `-a`/`--all`/`-am` sieht ungestagte Änderungen mit → dann Diff über git diff HEAD
    # (getrackte Dateien) statt nur --staged.
    diff_quelle = [] if hat_a_flag(befehl) else ["--staged"]
    r = route("--moment", "commit", "--repo", str(repo), *diff_quelle, "--log-ausloeser")
    if r["stufe"] == "keine":
        return None
    anweisung = (
        f"Review fällig (commit, Stufe {r['stufe']}): Skill review-dirigent ausführen "
        f"mit moment=commit und --repo {r['repo']}" + (f" {diff_quelle[0]}" if diff_quelle else "") + "."
    )
    if r["modus"] != "scharf":
        return None if r["beleg_gueltig"] else _kontext(anweisung)
    pruefung = python_skript(BELEG, "pruefen", "--moment", "commit", "--repo", str(repo), *diff_quelle)
    if pruefung.returncode != 1:
        return None
    grund = pruefung.stderr.strip().splitlines()[-1] if pruefung.stderr.strip() else anweisung
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": grund,
        }
    }


if __name__ == "__main__":
    ausfuehren("commit", haupt)
