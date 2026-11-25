#!/usr/bin/env python3
"""PreToolUse-Hook: Skill-Dateien auf dem Bau-Server schreibgeschützt (Hauptprojekt-Issue #325).

Eine Bau-Session darf ``~/.claude/skills/`` nicht ändern — Skill-Änderungen entstehen
am PC, ``/to-spawn`` schiebt sie selbst. Liest das Hook-JSON von stdin
(``tool_name``, ``tool_input``, ``cwd``) und verweigert mit Exit 2 (Grund auf stderr):

* Edit/Write/MultiEdit/NotebookEdit, deren ``file_path``/``notebook_path`` unter
  ``<home>/.claude/skills/`` liegt (``~`` und relative Pfade gegen ``cwd`` aufgelöst).
* Bash-Befehle, deren SCHREIBZIEL unter den Skills liegt (nicht bloß das Vorkommen
  des Pfads — ``python3 ~/.claude/skills/to-spawn/to_spawn.py … > /tmp/log`` bleibt
  erlaubt). Erkannte Schreibmuster:
    - Umleitung ``>``/``>>``/``&>`` (Regex ``_UMLEITUNG``) — Ziel wird geprüft
    - ``tee``, ``touch``, ``rm``, ``rmdir``, ``unlink``, ``mkdir``, ``truncate``,
      ``chmod``, ``chown``, ``patch`` — jedes Nicht-Options-Argument
    - ``cp``, ``install``, ``ln``, ``rsync`` — letztes Argument (Ziel);
      ``mv`` — jedes Argument (die Quelle verschwindet auch)
    - ``sed -i``/``--in-place``, ``perl -i`` — jedes Nicht-Options-Argument
    - ``dd of=…``; ``tar -x``/``--extract`` mit Skill-Pfad (z. B. ``-C``)
    - ``git`` mit schreibendem Unterbefehl (``_GIT_SCHREIBEND``) im Skill-Ordner
      (``-C <pfad>``, ``--git-dir``/``--work-tree``, vorheriges ``cd`` oder ``cwd``)
    - ``git clone``/``init``/``worktree add``/``submodule add`` mit Ziel in den Skills
      (Ziel-Position je Unterbefehl in ``_GIT_ANLEGEN``; dazu ``--separate-git-dir``)
      oder mit ``--git-dir``/``--work-tree`` in den Skills
    - Env-Präfixe ``GIT_DIR=``/``GIT_WORK_TREE=`` zählen wie ``--git-dir``/``--work-tree``
    - ``python``/``python3`` mit Inline-Code (``-c`` oder ``- <<``), der schreibt
      (Regex ``_PY_SCHREIBT``) und den Skill-Pfad nennt
  ``cd <pfad>`` im Befehl verschiebt das Arbeitsverzeichnis für die Folgeteile.

Alles andere und kaputtes JSON → Exit 0 (fail-open, bei kaputtem JSON eine
stderr-Zeile). Unerwartete Ausnahme beim Prüfen → Exit 2 (verweigern, geloggt).
``SKILL_SCHUTZ_WURZEL`` ersetzt das Home (für Tests).

ponytail: Regex-/Token-Heuristik, keine Shell-Semantik (eval, Variablen, Heredocs
anderer Interpreter rutschen durch); Upgrade: Skill-Ordner zusätzlich per
Dateirechte (chattr/read-only-Mount) sperren.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.WARNING, format="skill_schutz: %(message)s", stream=sys.stderr)
log = logging.getLogger("skill_schutz")

GRUND = "Skill-Dateien sind auf dem Bau-Server schreibgeschützt — Änderung am PC machen, /to-spawn schiebt sie selbst."
DATEI_WERKZEUGE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_TRENNER = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_UMLEITUNG = re.compile(r"(?:^|[^<>&\d])(?:\d|&)?>>?\|?\s*([^\s;&|<>]+)")
_ALLE_ARGS = {
    "tee",
    "touch",
    "rm",
    "rmdir",
    "unlink",
    "mkdir",
    "truncate",
    "chmod",
    "chown",
    "patch",
    "mv",
}
_ZIEL_LETZTES = {"cp", "install", "ln", "rsync"}
_GIT_SCHREIBEND = {
    "checkout", "switch", "apply", "reset", "restore", "pull", "merge", "am", "rebase",
    "stash", "clean", "rm", "mv", "commit", "cherry-pick", "revert", "add",
}  # fmt: skip
# Globale git-Optionen, die das nächste Token als Wert schlucken.
_GIT_GLOBAL_MIT_WERT = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source",
}  # fmt: skip
# Env-Präfixe, die wie die gleichnamigen globalen git-Optionen wirken.
_GIT_ENV = {"GIT_DIR": "--git-dir", "GIT_WORK_TREE": "--work-tree"}
# Unterbefehle, die ein neues Repo/Arbeitsverzeichnis anlegen:
# (Index des Ziel-Positionsarguments, Optionen mit Wert in Leerzeichen-Form).
# Fehlt das Ziel, gilt der Arbeitsordner; fehlen schon die Argumente davor, nichts.
_GIT_ANLEGEN = {
    "clone": (1, {
        "-b", "--branch", "-o", "--origin", "-u", "--upload-pack", "--depth", "-c",
        "--config", "--reference", "--reference-if-able", "--separate-git-dir",
        "--template", "-j", "--jobs", "--shallow-since", "--shallow-exclude",
        "--filter", "--server-option", "--bundle-uri", "--ref-format", "--revision",
    }),
    "init": (0, {
        "-b", "--initial-branch", "--separate-git-dir", "--template",
        "--object-format", "--ref-format",
    }),
    "worktree add": (0, {"-b", "-B", "--reason"}),
    "submodule add": (1, {"-b", "--branch", "--name", "--reference", "--depth"}),
}  # fmt: skip
# Optionen, deren Wert selbst ein Schreibziel ist.
_GIT_ZIEL_OPTIONEN = {"--separate-git-dir"}
_PY_SCHREIBT = re.compile(
    r"open\([^)]*['\"](?:[wax][bt+]*|r[bt]*\+[bt]*)['\"]|write_text|write_bytes|shutil\."
    r"|os\.(?:remove|unlink|rename|replace|mkdir|makedirs|rmdir|truncate|chmod|chown)"
    r"|\.(?:unlink|rename|touch|mkdir|rmdir)\(|Path\([^)]*\)\.replace\("
)
_PY_OPT_MIT_WERT = {"-W", "-X", "--check-hash-based-pycs"}
_PY_HEREDOC = re.compile(r"(?:^|[\s;&|(`/])python\d*(?:\.\d+)?(?:\.exe)?(?:\s+(?:-[^\s]*|-))*\s*<<")
_PRAEFIXE = {"sudo", "env", "nohup", "command", "exec", "time", "busybox"}
_SCHLUESSELWOERTER = {"if", "then", "else", "elif", "while", "until", "do", "!", "{", "}"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_SHELL_MIT_WERT = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_MAX_TIEFE = 16


def _home() -> Path:
    return Path(os.environ.get("SKILL_SCHUTZ_WURZEL") or Path.home())


def _wurzel() -> Path:
    return (_home() / ".claude" / "skills").resolve()


def _aufloesen(pfad: str, cwd: Path) -> Path:
    """``~``/``$HOME`` gegen das Home, relative Pfade gegen ``cwd`` auflösen."""
    pfad = pfad.strip("'\"")
    for kuerzel in ("~", "$HOME", "${HOME}"):
        if pfad == kuerzel or pfad.startswith(kuerzel + "/"):
            pfad = str(_home()) + pfad[len(kuerzel) :]
            break
    p = Path(pfad)
    if not p.is_absolute():
        p = cwd / p
    return p.resolve()


def _in_skills(pfad: str, cwd: Path) -> bool:
    if not pfad:
        return False
    try:
        return _aufloesen(pfad, cwd).is_relative_to(_wurzel())
    except (OSError, ValueError):
        return ".claude/skills" in pfad


def _tokens_mit_status(teil: str) -> tuple[list[str], bool]:
    try:
        return shlex.split(teil), True
    except ValueError:
        return teil.split(), False


def _tokens(teil: str) -> list[str]:
    return _tokens_mit_status(teil)[0]


def _git_ziel_args(args: list[str], mit_wert: set[str]) -> tuple[list[str], list[str]]:
    """Trennt ``git clone``/``git init``-Argumente in (Positionsargumente, Schreibziel-Optionswerte).

    Optionen aus ``mit_wert`` schlucken das nächste Token (``--depth 1``); ``--opt=wert``
    bleibt ein Token. Schreibziel-Optionen (``_GIT_ZIEL_OPTIONEN``, z. B.
    ``--separate-git-dir``) liefern ihren Wert zur Prüfung mit.
    """
    positionale: list[str] = []
    ziel_werte: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            positionale.extend(args[i + 1 :])
            break
        if a.startswith("-"):
            name, gleich, wert = a.partition("=")
            if not gleich and name in mit_wert and i + 1 < len(args):
                wert = args[i + 1]
                i += 1
            if name in _GIT_ZIEL_OPTIONEN:
                ziel_werte.append(wert)
        else:
            positionale.append(a)
        i += 1
    return positionale, ziel_werte


def _git_schreibt(args: list[str], cwd: Path) -> bool:
    """``git …``: schreibender Unterbefehl im Skill-Ordner oder clone/init mit Skill-Ziel.

    Globale Optionen: ``-C <pfad>`` verschiebt den Arbeitsordner; ``--git-dir``/
    ``--work-tree`` (mit Leerzeichen oder ``=``) in den Skills zählen wie ein Skill-Ordner.
    """
    ordner = cwd
    repo_in_skills = False
    rest = list(args)
    while rest and rest[0].startswith("-"):
        name, gleich, wert = rest[0].partition("=")
        schritt = 1
        if not gleich and name in _GIT_GLOBAL_MIT_WERT and len(rest) > 1:
            wert, schritt = rest[1], 2
        if name == "-C" and schritt == 2:
            ordner = _aufloesen(wert, ordner)
        elif name in ("--git-dir", "--work-tree") and _in_skills(wert, ordner):
            repo_in_skills = True
        rest = rest[schritt:]
    if not rest:
        return False
    unterbefehl, rest = rest[0], rest[1:]
    if unterbefehl in ("worktree", "submodule") and rest[:1] == ["add"]:
        unterbefehl, rest = f"{unterbefehl} add", rest[1:]
    if unterbefehl in _GIT_ANLEGEN:
        ziel_index, mit_wert = _GIT_ANLEGEN[unterbefehl]
        positionale, ziel_werte = _git_ziel_args(rest, mit_wert)
        if len(positionale) < ziel_index:
            return False
        # Fehlt das Ziel: clone → Ordner aus der URL im Arbeitsordner, init → Arbeitsordner.
        ziel = positionale[ziel_index] if len(positionale) > ziel_index else "."
        return repo_in_skills or any(_in_skills(z, ordner) for z in (ziel, *ziel_werte))
    return unterbefehl in _GIT_SCHREIBEND and (repo_in_skills or _in_skills(str(ordner), cwd))


def _py_code_schreibt(code: str, cwd: Path) -> bool:
    """True, wenn Python-Inline-Code erkennbar in den Skill-Ordner schreibt."""
    if not _PY_SCHREIBT.search(code):
        return False
    return ".claude/skills" in code or ".claude\\skills" in code or _in_skills(".", cwd)


def _python_c_code(args: list[str]) -> str | None:
    """Liefert den Inline-Code hinter ``-c`` (auch ``-uc``/``-cCODE``) oder ``None``."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--", "-") or not a.startswith("-"):
            return None
        if a in _PY_OPT_MIT_WERT:
            i += 2
            continue
        if a.startswith("--") or a.startswith(("-W", "-X")):
            i += 1
            continue
        if "c" in a[1:]:
            rest = a[1:].partition("c")[2]
            if rest:
                return rest
            return args[i + 1] if i + 1 < len(args) else ""
        if "m" in a[1:]:
            return None
        i += 1
    return None


def _shell_c_code(args: list[str]) -> str | None:
    """Liefert den Befehl hinter ``-c`` einer Shell (z. B. ``bash -ec …``) oder ``None``."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--", "-") or not a.startswith(("-", "+")):
            return None
        if a in _SHELL_MIT_WERT:
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if a.startswith("-") and "c" in a[1:]:
            return args[i + 1] if i + 1 < len(args) else ""
        i += 1
    return None


def _teil_schreibt(tokens: list[str], cwd: Path, *, shlex_ok: bool = True, _tiefe: int = 0) -> bool:
    git_env: list[str] = []  # GIT_DIR=…/GIT_WORK_TREE=… als globale git-Optionen
    while tokens and (
        tokens[0] in _SCHLUESSELWOERTER
        or tokens[0] in _PRAEFIXE
        or ("=" in tokens[0] and not tokens[0].startswith("-"))
    ):
        name, _, wert = tokens[0].partition("=")
        if name in _GIT_ENV:
            git_env.append(f"{_GIT_ENV[name]}={wert}")
        tokens = tokens[1:]
    if not tokens:
        return False
    befehl = Path(tokens[0]).name.removesuffix(".exe")
    args = tokens[1:]
    if befehl == "git":
        args = git_env + args
    nicht_optionen = [a for a in args if not a.startswith("-")]
    if befehl in _ALLE_ARGS:
        return any(_in_skills(a, cwd) for a in nicht_optionen)
    if befehl in _ZIEL_LETZTES:
        return bool(nicht_optionen) and _in_skills(nicht_optionen[-1], cwd)
    if befehl in ("sed", "perl") and any(re.match(r"^-[a-zA-Z]*i", a) or a.startswith("--in-place") for a in args):
        return any(_in_skills(a, cwd) for a in nicht_optionen)
    if befehl == "dd":
        return any(a.startswith("of=") and _in_skills(a[3:], cwd) for a in args)
    if befehl == "tar" and any(a == "--extract" or re.match(r"^-?[a-zA-Z]*x", a) for a in args[:2]):
        return any(_in_skills(a, cwd) for a in nicht_optionen) or _in_skills(".", cwd)
    if befehl == "git":
        return _git_schreibt(args, cwd)
    if re.fullmatch(r"python\d*(?:\.\d+)?", befehl):
        code = _python_c_code(args)
        if code is not None:
            if not shlex_ok:
                code = " ".join(args)
            return _py_code_schreibt(code, cwd)
    if befehl in _SHELLS:
        code = _shell_c_code(args)
        if code is not None:
            if not shlex_ok:
                code = " ".join(args)
            return bash_schreibt_in_skills(code, cwd, _tiefe=_tiefe + 1)
    if befehl == "eval" and args:
        return bash_schreibt_in_skills(" ".join(args), cwd, _tiefe=_tiefe + 1)
    return False


_HEREDOC_KOPF = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\2")
_INTERPRETER = re.compile(
    r"(?:^|[\s;&|(`/])(?:bash|sh|zsh|dash|ksh|eval|source|xargs|ssh|python\d*(?:\.\d+)?|perl|ruby|node)(?=[\s;&|)`]|$)"
)


class _Unsicher(Exception):
    """Konstrukt nicht sicher verstanden → Befehl bleibt unverändert Befehl."""


def _nach_fortsetzung(s: str, i: int) -> int:
    """Überspringt Zeilenfortsetzungen ``\\`` + Zeilenumbruch ab ``i``."""
    while i < len(s) and s[i] == "\\":
        if s.startswith("\\\r\n", i):
            i += 3
        elif s.startswith("\\\n", i):
            i += 2
        else:
            break
    return i


def _ende_einfachquote(s: str, start: int, *, ansi: bool = False) -> tuple[int, bool]:
    """Index hinter schließendem ``'`` (mit Escapes bei ``$'…'``) sowie ``geschlossen``."""
    j, n = start + 1, len(s)
    while j < n:
        if ansi and s[j] == "\\":
            j += 2
            continue
        if s[j] == "'":
            return j + 1, True
        j += 1
    return n, False


def _ende_backtick(s: str, start: int) -> tuple[str, int, bool]:
    """Extrahiert den Befehl in Backticks `` `…` `` (inkl. ``\\` ``-Entschachtelung)."""
    inhalt: list[str] = []
    j, n = start + 1, len(s)
    while j < n:
        if s[j] == "\\":
            if s.startswith("\\\r\n", j):
                j += 3
                continue
            if s.startswith("\\\n", j):
                j += 2
                continue
            if j + 1 < n and s[j + 1] in "$`\\\"":
                inhalt.append(s[j + 1])
                j += 2
                continue
            inhalt.append(s[j])
            j += 1
        elif s[j] == "`":
            return "".join(inhalt), j + 1, True
        else:
            inhalt.append(s[j])
            j += 1
    return "".join(inhalt), n, False


def _ist_arithmetik(s: str, innen_start: int) -> bool:
    """True, wenn die zweite Klammer von ``$((`` direkt mit ``))`` schließt."""
    tiefe, j, n = 1, innen_start + 1, len(s)
    while j < n and tiefe > 0:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            j, _ = _ende_einfachquote(s, j, ansi=j > innen_start + 1 and s[j - 1] == "$")
            continue
        if c == '"':
            j, _, _ = _ende_doppelquote(s, j)
            continue
        if c == "(":
            tiefe += 1
        elif c == ")":
            tiefe -= 1
        j += 1
    return tiefe == 0 and j < n and s[j] == ")"


def _ende_klammer_subst(s: str, klammer_pos: int) -> tuple[str, int, bool]:
    """Extrahiert den Inhalt einer Klammer-Ersetzung ab ``(`` (``$(…)``, ``<(…)``, ``>(…)``)."""
    j, n = klammer_pos + 1, len(s)
    tiefe, case_tiefe, befehlsanfang = 1, 0, True
    offen: list[tuple[bool, str, bool]] = []
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "#" and (j == klammer_pos + 1 or s[j - 1] in " \t\n;&|("):
            ende = s.find("\n", j)
            j = n if ende < 0 else ende
            continue
        if c == "'":
            ansi = j > klammer_pos + 1 and s[j - 1] == "$"
            j, geschlossen = _ende_einfachquote(s, j, ansi=ansi)
            if not geschlossen:
                return s[klammer_pos + 1 :], n, False
            befehlsanfang = False
            continue
        if c == '"':
            j, _, geschlossen = _ende_doppelquote(s, j)
            if not geschlossen:
                return s[klammer_pos + 1 :], n, False
            befehlsanfang = False
            continue
        if c == "`":
            _, j, geschlossen = _ende_backtick(s, j)
            if not geschlossen:
                return s[klammer_pos + 1 :], n, False
            befehlsanfang = False
            continue
        if s.startswith("<<<", j):
            befehlsanfang = False
            j += 3
            continue
        if s.startswith("<<", j):
            kopf = _HEREDOC_KOPF.match(s, j)
            if kopf:
                offen.append((bool(kopf.group(1)), kopf.group(3), bool(kopf.group(2))))
                befehlsanfang = False
                j = kopf.end()
                continue
        if c == "\n" and offen:
            try:
                j, _ = _rumpf_ueberspringen(s, j + 1, offen, [])
            except _Unsicher:
                return s[klammer_pos + 1 :], n, False
            offen = []
            befehlsanfang = True
            continue
        if c == "(":
            tiefe += 1
            befehlsanfang = True
            j += 1
            continue
        if c == ")":
            if tiefe == 1:
                if case_tiefe > 0:
                    befehlsanfang = True
                    j += 1
                    continue
                return s[klammer_pos + 1 : j], j + 1, True
            tiefe -= 1
            j += 1
            continue
        if c.isalpha() or c == "_":
            k = j + 1
            while k < n and (s[k].isalnum() or s[k] == "_"):
                k += 1
            wort = s[j:k]
            if befehlsanfang and wort == "case":
                case_tiefe += 1
                befehlsanfang = False
            elif wort == "esac" and case_tiefe > 0:
                case_tiefe -= 1
                befehlsanfang = False
            elif wort in _SCHLUESSELWOERTER:
                befehlsanfang = True
            else:
                befehlsanfang = False
            j = k
            continue
        if c in ";&|\n":
            befehlsanfang = True
        elif c not in " \t\r":
            befehlsanfang = False
        j += 1
    return s[klammer_pos + 1 :], n, False


def _ende_doppelquote(s: str, start: int) -> tuple[int, list[str], bool]:
    """Scannt ``"…"`` ab ``start``, sammelt darin enthaltene ``$(…)``/Backticks."""
    funde: list[str] = []
    j, n = start + 1, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 3 if s.startswith("\\\r\n", j) else 2
            continue
        if c == '"':
            return j + 1, funde, True
        if c == "$":
            k = _nach_fortsetzung(s, j + 1)
            if k < n and s[k] == "(":
                k2 = _nach_fortsetzung(s, k + 1)
                if k2 < n and s[k2] == "(" and _ist_arithmetik(s, k2):
                    j = k2 + 1
                    continue
                innen, j, _ = _ende_klammer_subst(s, k)
                funde.append(innen)
                continue
        if c == "`":
            innen, j, _ = _ende_backtick(s, j)
            funde.append(innen)
            continue
        j += 1
    return n, funde, False


def _ersetzungen_im_text(s: str, *, nur_subst: bool = False) -> list[str]:
    """Findet ausführbare ``$(…)``/Backticks (und ``<(…)``/``>(…)`` außerhalb von Text).

    Mit ``nur_subst=True`` (für ungequotete Heredoc-Rümpfe) gelten ``'``, ``"`` und ``#``
    wie in der Shell als gewöhnliche Zeichen, nicht als Anführung oder Kommentar.
    """
    funde: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 3 if s.startswith("\\\r\n", i) else 2
            continue
        if not nur_subst:
            if c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
                ende = s.find("\n", i)
                i = n if ende < 0 else ende
                continue
            if c == "'":
                ende, geschlossen = _ende_einfachquote(s, i, ansi=i > 0 and s[i - 1] == "$")
                i = ende if geschlossen else i + 1
                continue
            if c == '"':
                i, innen_funde, _ = _ende_doppelquote(s, i)
                funde.extend(innen_funde)
                continue
            if c in "<>" and i + 1 < n and s[i + 1] == "(":
                innen, i, _ = _ende_klammer_subst(s, i + 1)
                funde.append(innen)
                continue
        if c == "$":
            k = _nach_fortsetzung(s, i + 1)
            if k < n and s[k] == "(":
                k2 = _nach_fortsetzung(s, k + 1)
                if k2 < n and s[k2] == "(" and _ist_arithmetik(s, k2):
                    i = k2 + 1
                    continue
                innen, i, _ = _ende_klammer_subst(s, k)
                funde.append(innen)
                continue
        if c == "`":
            innen, i, _ = _ende_backtick(s, i)
            funde.append(innen)
            continue
        i += 1
    return funde


def _ohne_text(befehl: str) -> tuple[str, list[str]]:
    """SSOT „Befehl oder Text“: Heredoc-Rümpfe und mehrzeilige Zeichenketten entfernen.

    Ein Durchlauf Zeichen für Zeichen (außen / '…' / "…" / Kommentar / Escape). Ein Heredoc-Kopf
    zählt nur im Zustand „außen“ (nie ``<<<``, ``\\<<``, in ``((…))``, Anführung oder Kommentar);
    die Kopfzeile samt Umleitungs-Ziel bleibt. Mehrzeilige Zeichenketten werden nur geleert, wenn
    sie sauber geöffnet und geschlossen wurden und keine ``$(…)``/Backticks enthalten. Ungequotete
    Heredoc-Rümpfe mit ``$(…)``/Backticks geben ihre Ersetzungen als ausführbare Befehle heraus
    (auch im Zweifel oder bei einem Interpreter im Aufruf).
    """
    zusatz: list[str] = []
    try:
        bereinigt = _text_entfernen(befehl, zusatz)
    except _Unsicher:
        return befehl, zusatz
    if _INTERPRETER.search(befehl):
        return befehl, zusatz
    return bereinigt, zusatz


def _text_entfernen(s: str, zusatz: list[str]) -> str:
    aus: list[str] = []
    offen: list[tuple[bool, str, bool]] = []  # (<<-, Begrenzer, angeführt) bis zum Zeilenende
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            aus.append(s[i : i + 2])
            i += 2
        elif c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
            ende = s.find("\n", i)
            ende = n if ende < 0 else ende
            aus.append(s[i:ende])
            i = ende
        elif c == "'":
            ansi = i > 0 and s[i - 1] == "$"
            ende, geschlossen = _ende_einfachquote(s, i, ansi=ansi)
            if not geschlossen:
                raise _Unsicher
            inhalt = s[i + 1 : ende - 1]
            aus.append("''" if "\n" in inhalt and not ansi else s[i:ende])
            i = ende
        elif c == '"':
            ende, innen_funde, geschlossen = _ende_doppelquote(s, i)
            if not geschlossen or innen_funde:
                zusatz.extend(innen_funde)
                raise _Unsicher
            inhalt = s[i + 1 : ende - 1]
            aus.append('""' if "\n" in inhalt else s[i:ende])
            i = ende
        elif s.startswith("((", i):
            tiefe, j = 0, i
            while j < n:
                tiefe += (s[j] == "(") - (s[j] == ")")
                j += 1
                if tiefe == 0:
                    break
            if tiefe:
                raise _Unsicher
            aus.append(s[i:j])
            i = j
        elif s.startswith("<<<", i):
            aus.append("<<<")
            i += 3
        elif s.startswith("<<", i):
            kopf = _HEREDOC_KOPF.match(s, i)
            if not kopf:
                raise _Unsicher
            offen.append((bool(kopf.group(1)), kopf.group(3), bool(kopf.group(2))))
            aus.append(kopf.group(0))
            i = kopf.end()
        elif c == "\n" and offen:
            aus.append("\n")
            i, rumpf_befehle = _rumpf_ueberspringen(s, i + 1, offen, zusatz)
            for eintrag in rumpf_befehle:
                aus.append(f"{eintrag}\n")
            offen = []
        else:
            if c == "\n":
                offen = []
            aus.append(c)
            i += 1
    return "".join(aus)


def _rumpf_ueberspringen(
    s: str,
    start: int,
    offen: list[tuple[bool, str, bool]],
    zusatz: list[str],
) -> tuple[int, list[str]]:
    """Index hinter den Rümpfen aller offenen Heredocs plus ausführbare Ersetzungen daraus."""
    i = start
    rumpf_befehle: list[str] = []
    for strich, begrenzer, angefuehrt in offen:
        rumpf_start = i
        geschlossen = False
        rumpf_ende = len(s)
        while i < len(s):
            ende = s.find("\n", i)
            naechst = len(s) if ende < 0 else ende + 1
            zeile = s[i : len(s) if ende < 0 else ende]
            if (zeile.lstrip("\t") if strich else zeile) == begrenzer:
                rumpf_ende = i
                i = naechst
                geschlossen = True
                break
            i = naechst
        rumpf = s[rumpf_start:rumpf_ende]
        if not angefuehrt:
            funde = _ersetzungen_im_text(rumpf, nur_subst=True)
            if funde:
                rumpf_befehle.extend(funde)
                zusatz.extend(funde)
        if not geschlossen:
            raise _Unsicher
    return i, rumpf_befehle


def _teile_trennen(s: str) -> list[str]:
    """Teilt ``s`` an Shell-Trennern außerhalb von Anführungszeichen und Ersetzungen.

    Bei ungeschlossenen Anführungszeichen oder Ersetzungen fällt die Zerlegung
    konservativ auf ``_TRENNER`` zurück (fail-closed).
    """
    teile: list[str] = []
    start, i, n = 0, 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 3 if s.startswith("\\\r\n", i) else 2
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
            teile.append(s[start:i].strip())
            ende = s.find("\n", i)
            if ende < 0:
                return [t for t in teile if t]
            i = ende + 1
            start = i
            continue
        if c == "'":
            i, geschlossen = _ende_einfachquote(s, i, ansi=i > 0 and s[i - 1] == "$")
            if not geschlossen:
                return [t for t in _TRENNER.split(s) if t]
            continue
        if c == '"':
            i, _, geschlossen = _ende_doppelquote(s, i)
            if not geschlossen:
                return [t for t in _TRENNER.split(s) if t]
            continue
        if c == "`":
            _, i, geschlossen = _ende_backtick(s, i)
            if not geschlossen:
                return [t for t in _TRENNER.split(s) if t]
            continue
        if c == "$":
            k = _nach_fortsetzung(s, i + 1)
            if k < n and s[k] == "(":
                _, i, geschlossen = _ende_klammer_subst(s, k)
                if not geschlossen:
                    return [t for t in _TRENNER.split(s) if t]
                continue
        if c in "<>" and i + 1 < n and s[i + 1] == "(":
            _, i, geschlossen = _ende_klammer_subst(s, i + 1)
            if not geschlossen:
                return [t for t in _TRENNER.split(s) if t]
            continue
        if s.startswith("((", i):
            _, i, geschlossen = _ende_klammer_subst(s, i)
            if not geschlossen:
                return [t for t in _TRENNER.split(s) if t]
            continue
        if s.startswith(("&&", "||"), i):
            teile.append(s[start:i].strip())
            i += 2
            start = i
            continue
        if c == "&" and (i == 0 or s[i - 1] != ">") and s[i + 1 : i + 2] != ">":
            teile.append(s[start:i].strip())
            i += 1
            start = i
            continue
        if c in ";|\n)" or (c == "(" and (i == 0 or s[i - 1] not in "$<>@+?*!=")):
            teile.append(s[start:i].strip())
            i += 1
            start = i
            continue
        i += 1
    teile.append(s[start:].strip())
    return [t for t in teile if t]


def bash_schreibt_in_skills(befehl: str, cwd: Path, *, _tiefe: int = 0) -> bool:
    """True, wenn der Bash-Befehl erkennbar in den Skill-Ordner schreibt."""
    if _tiefe > _MAX_TIEFE:
        return True
    # Python-Heredoc (``python3 - <<EOF … EOF`` oder ``python3 <<EOF … EOF``).
    heredoc = _PY_HEREDOC.search(befehl)
    if heredoc and _py_code_schreibt(befehl[heredoc.end() :], cwd):
        return True
    bereinigt, zusatz = _ohne_text(befehl)
    aktuell = cwd
    for teil in _teile_trennen(bereinigt):
        if not teil:
            continue
        for innen in _ersetzungen_im_text(teil):
            if innen and bash_schreibt_in_skills(innen, aktuell, _tiefe=_tiefe + 1):
                return True
        for ziel in _UMLEITUNG.findall(teil):
            if _in_skills(ziel, aktuell):
                return True
        roh_tokens, shlex_ok = _tokens_mit_status(teil)
        tokens = [t for t in roh_tokens if not re.match(r"^(?:\d|&)?>", t)]
        if tokens[:1] == ["cd"] and len(tokens) > 1:
            aktuell = _aufloesen(tokens[1], aktuell)
            continue
        if _PY_HEREDOC.search(teil) and _py_code_schreibt(befehl, aktuell):
            return True
        if _teil_schreibt(tokens, aktuell, shlex_ok=shlex_ok, _tiefe=_tiefe):
            return True
    for innen in zusatz:
        if innen and bash_schreibt_in_skills(innen, cwd, _tiefe=_tiefe + 1):
            return True
    return False


def pruefe(daten: dict[str, Any]) -> bool:
    """True = verweigern."""
    werkzeug = daten.get("tool_name")
    eingabe = daten.get("tool_input") or {}
    cwd = Path(daten.get("cwd") or os.getcwd())
    if werkzeug in DATEI_WERKZEUGE:
        pfad = eingabe.get("file_path") or eingabe.get("notebook_path") or ""
        return _in_skills(str(pfad), cwd)
    if werkzeug == "Bash":
        return bash_schreibt_in_skills(str(eingabe.get("command") or ""), cwd)
    return False


def main() -> int:
    try:
        daten = json.loads(sys.stdin.read())
        if not isinstance(daten, dict):
            raise ValueError("kein JSON-Objekt")
    except (ValueError, OSError) as fehler:
        log.warning("Hook-JSON unlesbar (%s) — durchgelassen.", fehler)
        return 0
    try:
        verweigern = pruefe(daten)
    except Exception as fehler:  # noqa: BLE001 — Schutz-Hook: im Zweifel verweigern
        log.error("Prüfung abgebrochen (%s: %s) — verweigert.", type(fehler).__name__, fehler)
        return 2
    if verweigern:
        sys.stderr.write(f"{GRUND}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
