"""Erlaubt-Liste für die Verlauf-Prüfung (#389, Variante E).

Dreht die Beweislast gegenüber Verbots-Mustern um: jede nicht ausdrücklich als harmlos bekannte
Werkzeug-Handlung eines ``assistant``-Eintrags gilt als unbekannt (Klasse 2). Reiner
Assistent-Text und ``thinking`` sind keine Handlung (Klasse 3); die Frage selbst prüft weiter
:func:`to_spawn.befugnis.ist_klasse_2`.

Einzige öffentliche Funktion: :func:`unbekannt`. Alle Erlaubt-Listen stehen oben als
Konstanten mit kurzer Begründung. Das Geheimnis-Pfad-Muster :data:`_PFAD` kommt direkt aus
:mod:`to_spawn.befugnis` (keine Kopie).
"""

from __future__ import annotations

import ast
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from to_spawn import befugnis

log = logging.getLogger("to_spawn.erlaubt")

#: Einzige Quelle für Geheimnis-Pfade (``.env``, ``secret``, ``credential``) aus ``befugnis``.
_PFAD = befugnis._PFAD

# --- Erlaubt-Listen (Konstanten mit einem Satz „warum harmlos“) ------------------------------

#: Lokale Datei- und Such-Werkzeuge sind harmlos, solange kein Geheimnis-Pfad berührt wird.
WERKZEUGE_MIT_PFAD: frozenset[str] = frozenset(
    {"Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit"}
)

#: Planungs-, Lese- und Kommunikations-Werkzeuge führen keine Shell-Befehle aus (Unter-Agenten
#: werden über ihre eigenen Transkript-Dateien geprüft).
WERKZEUGE_HARMLOS: frozenset[str] = frozenset(
    {
        "Task",
        "Agent",
        "TodoWrite",
        "ToolSearch",
        "WebSearch",
        "WebFetch",
        "TaskStop",
        "TaskOutput",
        "AskUserQuestion",
        "ScheduleWakeup",
        "SendMessage",
    }
)

#: Shell-Werkzeuge, deren ``command``-Feld Glied für Glied durch den Zerleger geprüft wird.
WERKZEUGE_SHELL: frozenset[str] = frozenset({"Bash", "Monitor"})

#: Bekannte Prüf-, Planungs- und Schleifen-Skills ohne Deploy- oder Live-Wirkung.
SKILLS_HARMLOS: frozenset[str] = frozenset(
    {
        "tdd",
        "grilling",
        "grill-me",
        "code-review",
        "verify-hard",
        "loop",
        "plan",
        "compact",
        "review",
        "brainstorming",
        "systematic-debugging",
        "writing-plans",
        "executing-plans",
        "verification-before-completion",
        "requesting-code-review",
        "receiving-code-review",
        "using-git-worktrees",
        "finishing-a-development-branch",
    }
)

#: Rein lesende MCP-Präfixe (Wissensgraph, Suche, Doku) verändern weder Server noch Konten.
MCP_LESEND_PRAEFIXE: tuple[str, ...] = (
    "mcp__graphify__",
    "mcp__perplexity__",
    "mcp__docs__",
    "mcp__context7__",
)

#: Rein lesende einzelne MCP-Werkzeuge (Kontext-Suche, Browser-Ansicht ohne Klicks/Eingaben).
MCP_LESEND_NAMEN: frozenset[str] = frozenset(
    {
        "mcp__plugin_context-mode_context-mode__ctx_search",
        "mcp__plugin_context-mode_context-mode__ctx_stats",
        "mcp__plugin_context-mode_context-mode__ctx_index",
        "mcp__plugin_context-mode_context-mode__ctx_fetch_and_index",
        "mcp__chrome-devtools__take_screenshot",
        "mcp__chrome-devtools__take_snapshot",
        "mcp__chrome-devtools__list_pages",
        "mcp__chrome-devtools__list_console_messages",
        "mcp__chrome-devtools__list_network_requests",
        "mcp__chrome-devtools__get_console_message",
        "mcp__chrome-devtools__get_network_request",
    }
)

#: Kontext-Modus-Sammelaufruf ist harmlos, wenn jeder enthaltene Shell-Befehl selbst harmlos ist.
MCP_BATCH_WERKZEUG: str = "mcp__plugin_context-mode_context-mode__ctx_batch_execute"

#: Rein lesende und rechnende Shell-Befehle ohne Dateischreiben oder Code-Ausführung.
BEFEHLE_LESEND: frozenset[str] = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "diff",
        "cmp",
        "comm",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "jq",
        "yq",
        "echo",
        "printf",
        "pwd",
        "cd",
        "true",
        "false",
        "test",
        "[",
        "[[",
        "date",
        "uname",
        "whoami",
        "id",
        "hostname",
        "uptime",
        "df",
        "du",
        "free",
        "ps",
        "pgrep",
        "stat",
        "file",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "which",
        "type",
        "sha256sum",
        "md5sum",
        "shasum",
        "cksum",
        "base64",
        "xxd",
        "od",
        "hexdump",
        "strings",
        "nl",
        "fold",
        "fmt",
        "column",
        "paste",
        "join",
        "rev",
        "tac",
        "seq",
        "expr",
        "bc",
        "tree",
        "less",
        "more",
        "bat",
        "sleep",
    }
)

#: Lokale Datei-Befehle sind harmlos, solange kein Systempfad, ``..``, Variable oder Geheimnis-Pfad
#: betroffen ist.
BEFEHLE_DATEI_LOKAL: frozenset[str] = frozenset({"mkdir", "cp", "mv", "tee", "touch"})

#: Lokale Git-Unterbefehle ohne Merge, hartes Zurücksetzen oder Fern-Schreiben.
GIT_HARMLOS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "rev-list",
        "merge-base",
        "merge-tree",
        "ls-files",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "describe",
        "shortlog",
        "blame",
        "grep",
        "for-each-ref",
        "show-ref",
        "name-rev",
        "check-ignore",
        "check-attr",
        "symbolic-ref",
        "count-objects",
        "reflog",
        "whatchanged",
        "var",
        "version",
        "help",
        "add",
        "commit",
        "fetch",
        "switch",
        "checkout",
        "restore",
        "cherry-pick",
        "stash",
        "worktree",
        "mv",
        "rm",
    }
)

#: Erlaubte Zweig-Präfixe für ``git push`` ohne Force, ``+``, ``:`` oder Sammel-Schalter.
GIT_PUSH_PRAEFIXE: tuple[str, ...] = ("ticket/", "fix/", "wip/")

#: Erlaubte ``gh``-Unterbefehle: Lesen sowie Issue-/PR-Kommentare, Anlegen und Bearbeiten.
GH_HARMLOS: dict[str, frozenset[str]] = {
    "issue": frozenset({"view", "list", "status", "comment", "create", "edit"}),
    "pr": frozenset({"view", "list", "status", "diff", "checks", "comment", "create", "edit"}),
    "run": frozenset({"view", "list", "watch"}),
    "workflow": frozenset({"list", "view"}),
    "repo": frozenset({"view", "list"}),
    "release": frozenset({"list", "view"}),
    "search": frozenset({"issues", "prs", "repos", "code", "commits"}),
    "label": frozenset({"list"}),
}

#: Rein lesende ``docker``-Unterbefehle ändern weder Container noch Images oder Volumes.
DOCKER_HARMLOS: frozenset[str] = frozenset(
    {"ps", "logs", "inspect", "images", "version", "info", "stats", "top", "port", "diff", "history"}
)

#: Rein lesende ``tmux``-Unterbefehle (nie ``send-keys``, ``kill-*`` oder ``run-shell``).
TMUX_HARMLOS: frozenset[str] = frozenset(
    {
        "list-sessions",
        "ls",
        "list-windows",
        "lsw",
        "list-panes",
        "lsp",
        "list-clients",
        "lsc",
        "display-message",
        "capture-pane",
        "show-options",
        "show-window-options",
        "show-environment",
        "has-session",
    }
)

#: Bekannte Prüf- und Hilfs-Module für ``python -m``.
PYTHON_MODULE_HARMLOS: frozenset[str] = frozenset(
    {"pytest", "ruff", "mypy", "unittest", "json.tool", "py_compile", "compileall"}
)

#: Reine Standard-Rechen- und Parser-Module für Inline-Python (``-c`` / ``- <<EOF``) ohne OS-/Netz-Zugriff.
PYTHON_AST_MODULE_HARMLOS: frozenset[str] = frozenset(
    {
        "ast",
        "base64",
        "bisect",
        "collections",
        "configparser",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "fnmatch",
        "fractions",
        "functools",
        "glob",
        "hashlib",
        "heapq",
        "itertools",
        "json",
        "math",
        "operator",
        "pathlib",
        "pprint",
        "re",
        "shlex",
        "statistics",
        "string",
        "textwrap",
        "time",
        "tokenize",
        "tomllib",
        "typing",
        "unicodedata",
        "uuid",
    }
)

#: Eigene Bau-System-Skripte mit festen harmlosen Unterbefehlen (leere Menge = alle Argumente).
EIGENE_SKRIPTE: dict[str, frozenset[str]] = {
    "ticket_status.py": frozenset({"modus", "status", "warte", "zeige"}),
    "review_route.py": frozenset(),
    "review_beleg.py": frozenset({"schreiben", "pruefen", "zeige"}),
    "rot_probe.py": frozenset(),
    "to_spawn.py": frozenset({"eintrag", "aufseher-stand", "befund", "ticketstand", "leitstand"}),
}

#: Lokale Prüf- und Bau-Ziele für ``npm``/``pnpm``/``yarn`` (nie ``deploy``, ``publish`` oder ``release``).
NPM_HARMLOS: frozenset[str] = frozenset({"test", "t", "lint", "build", "check", "typecheck"})

#: Lokale Wissensgraph-Unterbefehle für ``graphify``.
GRAPHIFY_HARMLOS: frozenset[str] = frozenset({"query", "affected", "update", "status", "stats", "show"})

#: Erlaubte absolute/Home-Wurzeln für ``rm`` (zusätzlich zu relativen Pfaden, immer ohne ``..``).
RM_ERLAUBTE_PRAEFIXE: tuple[str, ...] = ("/tmp/", "/var/tmp/", "~/wt/", "~/tmp")


# --- Interne Schutz-Grenzen (keine Erlaubt-Listen) -------------------------------------------

_MAX_TIEFE = 12
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ZUWEISUNG = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_PYTHON_BIN = re.compile(r"^python3?(?:\.\d+)?$")
_PUSH_ZIEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RM_HOME_WT = re.compile(r"^/home/[^/\s]+/(?:wt/|tmp)")

_SHELL_SCHLUESSELWOERTER = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"})
_SHELL_ENDWOERTER = frozenset({"fi", "done", "esac", "{", "}"})

_VERBOTENE_SYSTEM_PFADE = (
    "/etc",
    "/opt",
    "/usr",
    "/var",
    "/boot",
    "/sbin",
    "/bin",
    "/lib",
    "/lib64",
    "/sys",
    "/proc",
    "/root",
    "/dev",
)
_VERBOTENE_PFAD_TEILE = ("systemd", "cron", ".ssh", ".bashrc", ".profile", ".zshrc")

_VERBOTENE_ENV_NAMEN = frozenset(
    {
        "ENV",
        "BASH_ENV",
        "PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "PAGER",
        "EDITOR",
        "VISUAL",
        "BROWSER",
        "SHELL",
        "IFS",
        "PROMPT_COMMAND",
        "PS4",
        "CDPATH",
        "LESSOPEN",
        "LESSCLOSE",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
)
_VERBOTENE_ENV_PRAEFIXE = ("LD_", "DYLD_", "GIT_", "BASH_", "SSH_", "AWS_", "CURL_", "PERL", "RUBY")

_GIT_LESEND = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "rev-list",
        "merge-base",
        "merge-tree",
        "ls-files",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "describe",
        "shortlog",
        "blame",
        "grep",
        "for-each-ref",
        "show-ref",
        "name-rev",
        "check-ignore",
        "check-attr",
        "symbolic-ref",
        "count-objects",
        "reflog",
        "whatchanged",
        "var",
        "version",
        "help",
    }
)
_GIT_GLOBAL_OHNE_WERT = frozenset({"-P", "--no-pager", "--literal-pathspecs", "--no-optional-locks", "--bare"})
_GIT_GLOBAL_MIT_WERT = frozenset({"-C", "--git-dir", "--work-tree"})
_GIT_PUSH_FLAGS = frozenset({"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain", "-n", "--dry-run"})

_GH_API_VERBOTENE_FLAGS = frozenset({"-X", "--method", "-f", "--raw-field", "-F", "--field", "--input"})
_GH_API_VERBOTENE_PRAEFIXE = ("-X", "-f", "-F", "--method=", "--raw-field=", "--field=", "--input=")

_SSH_OPT_OHNE_WERT = frozenset({"-n", "-q", "-4", "-6", "-T", "-v"})
_SSH_OPT_MIT_WERT = frozenset({"-p", "-l", "-o"})
_SSH_VERBOTENE_O = ("proxycommand", "localcommand", "remotecommand", "permitlocalcommand", "proxyjump")

_DOCKER_EXEC_OHNE_WERT = frozenset({"-i", "-t", "-it", "-ti", "--interactive", "--tty"})
_DOCKER_EXEC_MIT_WERT = frozenset({"-u", "--user", "-w", "--workdir", "-e", "--env"})

_TMUX_GLOBAL_MIT_WERT = frozenset({"-L", "-S", "-f"})

_FIND_VERBOTEN = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls", "-fprint", "-fprint0", "-fprintf"}
)
_XARGS_MIT_WERT = frozenset({"-I", "-L", "-l", "-n", "-P", "-d", "-E", "-e", "-s", "-a", "--max-args", "--max-procs"})

_AST_VERBOTENE_NAMEN = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "type",
        "object",
        "super",
        "dir",
        "memoryview",
    }
)
_AST_VERBOTENE_ATTRS = frozenset(
    {
        "system",
        "popen",
        "unlink",
        "rmdir",
        "replace",
        "rename",
        "chmod",
        "chown",
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
        "symlink_to",
        "hardlink_to",
        "open",
    }
)
_LESE_MODI = frozenset({"r", "rb", "rt"})


class _Unlesbar(Exception):
    """Shell-Befehl konnte nicht sicher zerlegt werden (z. B. offenes Zitat)."""


@dataclass
class _Token:
    wert: str
    dynamisch: bool = False


@dataclass
class _Umleitung:
    op: str
    ziel: str
    dynamisch: bool = False


@dataclass
class _Glied:
    tokens: list[_Token] = field(default_factory=list)
    umleitungen: list[_Umleitung] = field(default_factory=list)
    heredocs: list[tuple[str, bool]] = field(default_factory=list)
    hat_stdin: bool = False


# --- Öffentliche Schnittstelle ---------------------------------------------------------------


def unbekannt(eintrag: dict[str, Any]) -> str | None:
    """Erste nicht als harmlos bekannte Handlung eines ``assistant``-Eintrags oder ``None``."""
    if not isinstance(eintrag, dict):
        return "Eintrag ungültig"
    if eintrag.get("type") != "assistant" or eintrag.get("isApiErrorMessage"):
        return None
    inhalt = (eintrag.get("message") or {}).get("content")
    if inhalt is None or isinstance(inhalt, str):
        return None
    if not isinstance(inhalt, list):
        return "Eintrag-Inhalt ungültig"
    for block in inhalt:
        if not isinstance(block, dict):
            continue
        typ = block.get("type")
        if typ in ("text", "thinking", "redacted_thinking"):
            continue
        if typ != "tool_use":
            return f"Unbekannter Block-Typ: {typ}"
        if grund := _pruefe_werkzeug(block):
            return " ".join(grund.split())
    return None


# --- Werkzeug-Prüfung ------------------------------------------------------------------------


def _pruefe_werkzeug(block: dict[str, Any]) -> str | None:
    name = block.get("name")
    if not isinstance(name, str) or not name.strip():
        return "Werkzeug ohne Name"
    eingabe = block.get("input")
    if eingabe is None:
        eingabe = {}
    if not isinstance(eingabe, dict):
        return f"{name}: Eingabe ungültig"

    if name in WERKZEUGE_MIT_PFAD:
        for feld in ("file_path", "notebook_path", "path"):
            wert = eingabe.get(feld)
            if isinstance(wert, str) and wert.strip() and _PFAD.search(wert.lower()):
                return f"{name}: {wert}"
        return None

    if name in WERKZEUGE_HARMLOS:
        return None

    if name == "Skill":
        skill = str(eingabe.get("skill") or "").strip().lstrip("/").lower()
        if skill and skill in SKILLS_HARMLOS:
            return None
        return f"Skill {skill or '(leer)'}"

    if name in WERKZEUGE_SHELL:
        befehl = eingabe.get("command")
        if not isinstance(befehl, str) or not befehl.strip():
            return f"{name}: Befehl fehlt"
        return _befehl_unbekannt(befehl)

    if name == MCP_BATCH_WERKZEUG:
        return _pruefe_mcp_batch(eingabe)

    if name.startswith("mcp__"):
        if name in MCP_LESEND_NAMEN or any(name.startswith(p) for p in MCP_LESEND_PRAEFIXE):
            return None
        return f"{name}: {_kurz_eingabe(eingabe)}"

    return f"{name}: {_kurz_eingabe(eingabe)}"


def _pruefe_mcp_batch(eingabe: dict[str, Any]) -> str | None:
    befehle = eingabe.get("commands")
    if befehle is None:
        return None if isinstance(eingabe.get("queries"), list) else f"{MCP_BATCH_WERKZEUG}: commands fehlt"
    if not isinstance(befehle, list):
        return f"{MCP_BATCH_WERKZEUG}: commands ungültig"
    for eintrag in befehle:
        if isinstance(eintrag, str):
            cmd = eintrag
        elif isinstance(eintrag, dict) and isinstance(eintrag.get("command"), str):
            cmd = eintrag["command"]
        else:
            return f"{MCP_BATCH_WERKZEUG}: Eintrag ungültig"
        if grund := _befehl_unbekannt(cmd):
            return grund
    return None


def _kurz_eingabe(eingabe: dict[str, Any]) -> str:
    for feld in ("command", "cmd", "script", "code", "input", "argv", "commands", "url", "uid"):
        wert = eingabe.get(feld)
        if wert:
            return str(wert)[:160]
    return "unbekanntes Werkzeug"


# --- Eigener Shell-Zerleger (kein shlex; unlesbar = Klasse 2) --------------------------------


def _befehl_unbekannt(befehl: str, *, nur_lesend: bool = False, tiefe: int = 0) -> str | None:
    if tiefe > _MAX_TIEFE:
        return f"Verschachtelung zu tief: {befehl[:120]}"
    norm = unicodedata.normalize("NFKC", befehl)
    try:
        glieder, eingebettet = _zerlege_befehl(norm)
    except _Unlesbar as fehler:
        log.debug("Befehl nicht zerlegbar (%s): %s", fehler, befehl[:120])
        return f"Unlesbarer Befehl ({fehler}): {befehl[:160]}"

    for innen in eingebettet:
        if not innen.strip():
            continue
        if grund := _befehl_unbekannt(innen, nur_lesend=nur_lesend, tiefe=tiefe + 1):
            return grund

    for glied in glieder:
        if grund := _pruefe_glied(glied, nur_lesend=nur_lesend, tiefe=tiefe):
            return grund
    return None


def _zerlege_befehl(text: str) -> tuple[list[_Glied], list[str]]:
    """Zerlegt einen Shell-Befehl in Glieder (an ``; && || | & ( ) \\n``) und eingebettete
    Ersetzungen (``$(…)``, ``<(…)``, ``>(…)``, Backticks, ungequotete Heredocs)."""
    glieder: list[_Glied] = []
    eingebettet: list[str] = []
    aktuell = _Glied()
    offene_heredocs: list[tuple[_Glied, str, bool, bool]] = []
    buf: list[str] = []
    hat_token = False
    tok_dyn = False
    zitiert_im_token = False
    n = len(text)
    i = 0

    def _tok_abschliessen() -> None:
        nonlocal hat_token, tok_dyn, zitiert_im_token
        if hat_token:
            aktuell.tokens.append(_Token("".join(buf), tok_dyn))
            buf.clear()
            hat_token = False
            tok_dyn = False
            zitiert_im_token = False

    def _glied_abschliessen() -> None:
        nonlocal aktuell
        _tok_abschliessen()
        if aktuell.tokens or aktuell.umleitungen or aktuell.heredocs:
            glieder.append(aktuell)
            aktuell = _Glied()

    def _heredocs_lesen(pos: int) -> int:
        while offene_heredocs:
            ziel_glied, marker, strip_tabs, zitiert = offene_heredocs.pop(0)
            zeilen: list[str] = []
            gefunden = False
            while pos < n:
                ende = text.find("\n", pos)
                if ende == -1:
                    zeile = text[pos:]
                    pos = n
                else:
                    zeile = text[pos:ende]
                    pos = ende + 1
                vergleich = zeile.lstrip("\t") if strip_tabs else zeile
                if vergleich == marker:
                    gefunden = True
                    break
                zeilen.append(zeile)
            if not gefunden:
                raise _Unlesbar(f"Heredoc {marker!r} nicht geschlossen")
            koerper = "\n".join(zeilen)
            ziel_glied.heredocs.append((koerper, zitiert))
            ziel_glied.hat_stdin = True
            if not zitiert:
                _sammle_expansionen(koerper, eingebettet)
        return pos

    def _wort_lesen(pos: int) -> tuple[str, bool, int]:
        while pos < n and text[pos] in " \t":
            pos += 1
        if pos >= n or text[pos] in ";|&()\n<>":
            raise _Unlesbar("Ziel fehlt")
        wort: list[str] = []
        dyn = False
        while pos < n and text[pos] not in " \t\r\n;|&()<>":
            ch = text[pos]
            if ch == "'":
                stueck, pos = _lese_einzelzitat(text, pos)
                wort.append(stueck)
            elif ch == '"':
                stueck, d, pos = _lese_doppelzitat(text, pos, eingebettet)
                wort.append(stueck)
                dyn = dyn or d
            elif ch == "\\":
                if pos + 1 >= n:
                    raise _Unlesbar("offener Backslash")
                wort.append(text[pos + 1])
                pos += 2
            elif ch == "$" and pos + 1 < n and text[pos + 1] == "(":
                stueck, pos = _lese_dollar_klammer(text, pos, eingebettet)
                wort.append(stueck)
                dyn = True
            elif ch == "`":
                innen, pos = _lese_backtick(text, pos)
                eingebettet.append(innen)
                wort.append("$(…)")
                dyn = True
            else:
                if ch == "$":
                    dyn = True
                wort.append(ch)
                pos += 1
        return "".join(wort), dyn, pos

    while i < n:
        ch = text[i]

        if ch in " \t\r":
            _tok_abschliessen()
            i += 1
            continue

        if ch == "\n":
            _glied_abschliessen()
            i += 1
            if offene_heredocs:
                i = _heredocs_lesen(i)
            continue

        if ch == "#" and not hat_token:
            ende = text.find("\n", i)
            i = n if ende == -1 else ende
            continue

        if ch == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2
            continue

        # Kontroll-Operatoren
        if text[i : i + 2] in ("&&", "||", "|&"):
            _glied_abschliessen()
            i += 2
            continue
        if ch in ";|&()":
            if ch == "&" and i + 1 < n and text[i + 1] == ">":
                pass  # Umleitung &> / &>> unten
            else:
                _glied_abschliessen()
                i += 1
                continue

        # Prozess-Substitution <(…) / >(…)
        if ch in "<>" and i + 1 < n and text[i + 1] == "(":
            innen, i = _lese_klammer(text, i + 2)
            eingebettet.append(innen)
            buf.append(f"{ch}(…)")
            hat_token = True
            tok_dyn = True
            continue

        # Umleitungen und Heredocs (mit optionalem vorangestelltem Dateideskriptor wie 2>&1)
        if ch in "<>" or (ch == "&" and i + 1 < n and text[i + 1] == ">"):
            ist_fd = hat_token and not zitiert_im_token and "".join(buf).isdigit()
            if ist_fd:
                buf.clear()
                hat_token = False
                tok_dyn = False
            else:
                _tok_abschliessen()

            if text[i : i + 3] == "<<<":
                ziel, dyn, i = _wort_lesen(i + 3)
                aktuell.umleitungen.append(_Umleitung("<<<", ziel, dyn))
                aktuell.hat_stdin = True
                continue
            if text[i : i + 2] == "<<":
                strip_tabs = i + 2 < n and text[i + 2] == "-"
                pos = i + (3 if strip_tabs else 2)
                marker, zitiert, i = _lese_heredoc_marker(text, pos)
                offene_heredocs.append((aktuell, marker, strip_tabs, zitiert))
                continue

            if text[i : i + 3] == "&>>":
                op, pos = "&>>", i + 3
            elif text[i : i + 2] in ("&>", ">>", ">|", "<>"):
                op, pos = text[i : i + 2], i + 2
            else:
                op, pos = ch, i + 1

            # Dateideskriptor-Duplikation >&1 / >&2 / >&- / <&0
            if op in (">", "<") and pos < n and text[pos] == "&":
                pos += 1
                start_fd = pos
                while pos < n and (text[pos].isdigit() or text[pos] == "-"):
                    pos += 1
                if pos > start_fd:
                    i = pos
                    continue
                raise _Unlesbar("Umleitung & ohne Deskriptor")

            ziel, dyn, i = _wort_lesen(pos)
            aktuell.umleitungen.append(_Umleitung(op, ziel, dyn))
            if op in ("<", "<>"):
                aktuell.hat_stdin = True
            continue

        # Zitate, Escapes und Ersetzungen
        if ch == "'":
            stueck, i = _lese_einzelzitat(text, i)
            buf.append(stueck)
            hat_token = True
            zitiert_im_token = True
            continue

        if ch == "$" and i + 1 < n and text[i + 1] == "'":
            stueck, i = _lese_ansi_zitat(text, i)
            buf.append(stueck)
            hat_token = True
            zitiert_im_token = True
            continue

        if ch == '"':
            stueck, dyn, i = _lese_doppelzitat(text, i, eingebettet)
            buf.append(stueck)
            hat_token = True
            zitiert_im_token = True
            tok_dyn = tok_dyn or dyn
            continue

        if ch == "\\" :
            if i + 1 >= n:
                raise _Unlesbar("offener Backslash")
            buf.append(text[i + 1])
            hat_token = True
            zitiert_im_token = True
            i += 2
            continue

        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            stueck, i = _lese_dollar_klammer(text, i, eingebettet)
            buf.append(stueck)
            hat_token = True
            tok_dyn = True
            continue

        if ch == "`":
            innen, i = _lese_backtick(text, i)
            eingebettet.append(innen)
            buf.append("$(…)")
            hat_token = True
            tok_dyn = True
            continue

        if ch == "$":
            tok_dyn = True
        buf.append(ch)
        hat_token = True
        i += 1

    _glied_abschliessen()
    if offene_heredocs:
        raise _Unlesbar("Heredoc ohne Inhalt/Ende")
    return glieder, eingebettet


def _lese_einzelzitat(text: str, pos: int) -> tuple[str, int]:
    ende = text.find("'", pos + 1)
    if ende == -1:
        raise _Unlesbar("offenes Einzelzitat")
    return text[pos + 1 : ende], ende + 1


def _lese_ansi_zitat(text: str, pos: int) -> tuple[str, int]:
    i = pos + 2
    n = len(text)
    out: list[str] = []
    while i < n:
        if text[i] == "\\":
            if i + 1 >= n:
                raise _Unlesbar("offenes $'…'-Zitat")
            out.append(text[i + 1])
            i += 2
            continue
        if text[i] == "'":
            return "".join(out), i + 1
        out.append(text[i])
        i += 1
    raise _Unlesbar("offenes $'…'-Zitat")


def _lese_doppelzitat(text: str, pos: int, eingebettet: list[str]) -> tuple[str, bool, int]:
    i = pos + 1
    n = len(text)
    out: list[str] = []
    dyn = False
    while i < n:
        ch = text[i]
        if ch == '"':
            return "".join(out), dyn, i + 1
        if ch == "\\":
            if i + 1 >= n:
                raise _Unlesbar("offenes Doppelzitat")
            nxt = text[i + 1]
            if nxt == "\n":
                i += 2
                continue
            if nxt in ('"', "\\", "$", "`"):
                out.append(nxt)
            else:
                out.append("\\" + nxt)
            i += 2
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            stueck, i = _lese_dollar_klammer(text, i, eingebettet)
            out.append(stueck)
            dyn = True
            continue
        if ch == "`":
            innen, i = _lese_backtick(text, i, in_doppelzitat=True)
            eingebettet.append(innen)
            out.append("$(…)")
            dyn = True
            continue
        if ch == "$":
            dyn = True
        out.append(ch)
        i += 1
    raise _Unlesbar("offenes Doppelzitat")


def _lese_dollar_klammer(text: str, pos: int, eingebettet: list[str]) -> tuple[str, int]:
    """Liest ``$((…))`` (Arithmetik) oder ``$(…)`` (Befehlssubstitution)."""
    if text[pos : pos + 3] == "$((":
        i = pos + 3
        n = len(text)
        tiefe = 0
        while i < n:
            if text[i : i + 2] == "))" and tiefe == 0:
                innen = text[pos + 3 : i]
                if "$(" in innen or "`" in innen or "[" in innen:
                    raise _Unlesbar("Ausführung in Arithmetik")
                return "$((…))", i + 2
            if text[i] == "(":
                tiefe += 1
            elif text[i] == ")":
                tiefe -= 1
            i += 1
        raise _Unlesbar("offene Arithmetik $((…))")
    innen, neu = _lese_klammer(text, pos + 2)
    eingebettet.append(innen)
    return "$(…)", neu


def _lese_klammer(text: str, pos: int) -> tuple[str, int]:
    """Liest Inhalt ab direkt hinter ``(`` bis zur passenden ``)``, samt Zitaten und Heredocs."""
    i = pos
    n = len(text)
    tiefe = 1
    offene_heredocs: list[tuple[str, bool]] = []
    while i < n:
        ch = text[i]
        if ch == "\n":
            i += 1
            while offene_heredocs:
                marker, strip_tabs = offene_heredocs.pop(0)
                gefunden = False
                while i < n:
                    ende = text.find("\n", i)
                    if ende == -1:
                        zeile = text[i:]
                        i = n
                    else:
                        zeile = text[i:ende]
                        i = ende + 1
                    if (zeile.lstrip("\t") if strip_tabs else zeile) == marker:
                        gefunden = True
                        break
                if not gefunden:
                    raise _Unlesbar(f"Heredoc {marker!r} in $(…) nicht geschlossen")
            continue
        if ch == "'":
            _, i = _lese_einzelzitat(text, i)
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "'":
            _, i = _lese_ansi_zitat(text, i)
            continue
        if ch == '"':
            _, _, i = _lese_doppelzitat(text, i, [])
            continue
        if ch == "\\":
            if i + 1 >= n:
                raise _Unlesbar("offener Backslash in $(…)")
            i += 2
            continue
        if ch == "`":
            _, i = _lese_backtick(text, i)
            continue
        if ch == "#" and (i == pos or text[i - 1] in " \t\n;|&("):
            ende = text.find("\n", i)
            i = n if ende == -1 else ende
            continue
        if text[i : i + 2] == "<<" and text[i : i + 3] != "<<<":
            strip_tabs = i + 2 < n and text[i + 2] == "-"
            marker, _, i = _lese_heredoc_marker(text, i + (3 if strip_tabs else 2))
            offene_heredocs.append((marker, strip_tabs))
            continue
        if ch == "(":
            tiefe += 1
            i += 1
            continue
        if ch == ")":
            tiefe -= 1
            if tiefe == 0:
                if offene_heredocs:
                    raise _Unlesbar("Heredoc in $(…) nicht geschlossen")
                return text[pos:i], i + 1
            i += 1
            continue
        i += 1
    raise _Unlesbar("offene Klammer $(…)")


def _lese_backtick(text: str, pos: int, *, in_doppelzitat: bool = False) -> tuple[str, int]:
    i = pos + 1
    n = len(text)
    out: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                raise _Unlesbar("offener Backtick")
            nxt = text[i + 1]
            if nxt in ("$", "`", "\\") or (in_doppelzitat and nxt == '"'):
                out.append(nxt)
            else:
                out.append("\\" + nxt)
            i += 2
            continue
        if ch == "`":
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise _Unlesbar("offener Backtick")


def _lese_heredoc_marker(text: str, pos: int) -> tuple[str, bool, int]:
    n = len(text)
    while pos < n and text[pos] in " \t":
        pos += 1
    if pos >= n or text[pos] in "\n;|&()":
        raise _Unlesbar("Heredoc-Marker fehlt")
    zitiert = False
    out: list[str] = []
    while pos < n and text[pos] not in " \t\r\n;|&()<>":
        ch = text[pos]
        if ch == "'":
            zitiert = True
            stueck, pos = _lese_einzelzitat(text, pos)
            out.append(stueck)
        elif ch == '"':
            zitiert = True
            ende = text.find('"', pos + 1)
            if ende == -1:
                raise _Unlesbar("offenes Zitat im Heredoc-Marker")
            out.append(text[pos + 1 : ende])
            pos = ende + 1
        elif ch == "\\":
            if pos + 1 >= n:
                raise _Unlesbar("offener Backslash im Heredoc-Marker")
            zitiert = True
            out.append(text[pos + 1])
            pos += 2
        else:
            out.append(ch)
            pos += 1
    marker = "".join(out)
    if not marker:
        raise _Unlesbar("leerer Heredoc-Marker")
    return marker, zitiert, pos


def _sammle_expansionen(koerper: str, eingebettet: list[str]) -> None:
    """Sammelt ``$(…)`` und Backticks aus einem ungequoteten Heredoc-Körper."""
    i = 0
    n = len(koerper)
    while i < n:
        ch = koerper[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "$" and i + 1 < n and koerper[i + 1] == "(":
            _, i = _lese_dollar_klammer(koerper, i, eingebettet)
            continue
        if ch == "`":
            innen, i = _lese_backtick(koerper, i)
            eingebettet.append(innen)
            continue
        i += 1


# --- Glied-Prüfung ---------------------------------------------------------------------------


def _pruefe_glied(glied: _Glied, *, nur_lesend: bool, tiefe: int) -> str | None:
    for u in glied.umleitungen:
        if u.op in (">", ">>", ">|", "&>", "&>>", "<>"):
            if u.ziel == "/dev/null":
                continue
            if nur_lesend:
                return f"Schreib-Umleitung im Lese-Modus: {u.op} {u.ziel}"
            if u.dynamisch or _ist_verbotener_schreibpfad(u.ziel):
                return f"Umleitung auf geschützten Pfad: {u.op} {u.ziel}"
        elif u.op in ("<", "<<<") and _PFAD.search(u.ziel.lower()):
            return f"Geheimnis-Pfad in Umleitung: {u.ziel}"

    tokens = list(glied.tokens)
    while tokens and not tokens[0].dynamisch and tokens[0].wert in _SHELL_SCHLUESSELWOERTER:
        tokens.pop(0)
    if not tokens:
        return None
    if len(tokens) == 1 and not tokens[0].dynamisch and tokens[0].wert in _SHELL_ENDWOERTER:
        return None

    # ``for <var> in <werte...>`` (Körper steht im nächsten Glied nach ``; do``)
    if (
        len(tokens) >= 3
        and not tokens[0].dynamisch
        and tokens[0].wert == "for"
        and not tokens[1].dynamisch
        and _IDENT.fullmatch(tokens[1].wert)
        and not tokens[2].dynamisch
        and tokens[2].wert == "in"
    ):
        for t in tokens[3:]:
            if _PFAD.search(t.wert.lower()):
                return f"Geheimnis-Pfad in for-Schleife: {t.wert}"
        return None

    # Führende ``VAR=wert``-Zuweisungen prüfen und abstreifen
    while tokens and (m := _ZUWEISUNG.fullmatch(tokens[0].wert)):
        var_name, var_wert = m.group(1), m.group(2)
        if _ist_verbotene_env(var_name):
            return f"Verbotene Umgebungsvariable: {var_name}"
        if _PFAD.search(var_wert.lower()):
            return f"Geheimnis-Pfad in Variable {var_name}"
        tokens.pop(0)
    if not tokens:
        return None

    cmd = tokens[0]
    args = tokens[1:]
    if cmd.dynamisch or any(z in cmd.wert for z in ("/", "\\", "*", "?")):
        return f"Unbekannter Befehl: {_befehl_text(tokens)}"

    name = cmd.wert
    if name in BEFEHLE_LESEND:
        if name == "rg" and any(a.wert == "--pre" or a.wert.startswith("--pre=") for a in args):
            return "rg mit --pre nicht erlaubt"
        if name == "sort" and any(
            a.wert == "--compress-program" or a.wert.startswith("--compress-program=") for a in args
        ):
            return "sort mit --compress-program nicht erlaubt"
        return _pruefe_keine_geheimnisse(name, args)
    if name in BEFEHLE_DATEI_LOKAL:
        if nur_lesend:
            return f"Schreib-Befehl im Lese-Modus: {_befehl_text(tokens)}"
        return _pruefe_datei_lokal(name, args)
    if name == "sed":
        return _pruefe_sed(args, nur_lesend=nur_lesend)
    if name in ("awk", "gawk", "mawk"):
        return _pruefe_awk(args)
    if name == "find":
        return _pruefe_find(args)
    if name == "xargs":
        return _pruefe_xargs(args)
    if name == "rm":
        if nur_lesend:
            return f"rm im Lese-Modus: {_befehl_text(tokens)}"
        return _pruefe_rm(args)
    if name in ("pytest", "ruff", "mypy"):
        if nur_lesend:
            return f"{name} im Lese-Modus"
        return _pruefe_keine_geheimnisse(name, args)
    if _PYTHON_BIN.fullmatch(name):
        if nur_lesend:
            return f"Python im Lese-Modus: {_befehl_text(tokens)}"
        return _pruefe_python(args, glied)
    if name in ("npm", "pnpm", "yarn"):
        if nur_lesend:
            return f"{name} im Lese-Modus"
        return _pruefe_npm(name, args)
    if name == "graphify":
        if nur_lesend:
            return "graphify im Lese-Modus"
        return _pruefe_graphify(args)
    if name == "docker":
        return _pruefe_docker(args, nur_lesend=nur_lesend, tiefe=tiefe)
    if name == "tmux":
        return _pruefe_tmux(args)
    if name == "gh":
        return _pruefe_gh(args, nur_lesend=nur_lesend)
    if name == "git":
        return _pruefe_git(args, nur_lesend=nur_lesend)
    if name == "ssh":
        if nur_lesend or glied.hat_stdin or glied.heredocs:
            return f"ssh nicht als reiner Lese-Befehl: {_befehl_text(tokens)}"
        return _pruefe_ssh(args, tiefe=tiefe)

    return f"Unbekannter Befehl: {_befehl_text(tokens)}"


def _befehl_text(tokens: list[_Token]) -> str:
    return " ".join(t.wert for t in tokens)[:160]


def _ist_verbotene_env(name: str) -> bool:
    gross = name.upper()
    return gross in _VERBOTENE_ENV_NAMEN or any(gross.startswith(p) for p in _VERBOTENE_ENV_PRAEFIXE)


def _ist_verbotener_schreibpfad(pfad: str) -> bool:
    p = pfad.replace("\\", "/")
    klein = p.lower()
    if ".." in p or _PFAD.search(klein):
        return True
    if any(teil in klein for teil in _VERBOTENE_PFAD_TEILE):
        return True
    for sys_pfad in _VERBOTENE_SYSTEM_PFADE:
        if klein == sys_pfad or klein.startswith(sys_pfad + "/"):
            return True
    return False


def _pruefe_keine_geheimnisse(name: str, args: list[_Token]) -> str | None:
    for a in args:
        if _PFAD.search(a.wert.lower()):
            return f"{name}: Geheimnis-Pfad {a.wert}"
    return None


def _pruefe_datei_lokal(name: str, args: list[_Token]) -> str | None:
    for a in args:
        if a.wert.startswith("-"):
            continue
        if a.dynamisch or _ist_verbotener_schreibpfad(a.wert):
            return f"{name}: geschütztes Ziel {a.wert}"
    return None


# --- Einzel-Prüfer für Spezial-Befehle -------------------------------------------------------


def _pruefe_sed(args: list[_Token], *, nur_lesend: bool) -> str | None:
    ausdruecke: list[str] = []
    dateien: list[_Token] = []
    i = 0
    while i < len(args):
        w = args[i].wert
        if w in ("-f", "--file") or w.startswith("--file="):
            return f"sed mit Skript-Datei: {w}"
        if w == "-i" or w.startswith(("-i", "--in-place")):
            if nur_lesend:
                return "sed -i im Lese-Modus"
            i += 1
            continue
        if w in ("-e", "--expression"):
            if i + 1 >= len(args):
                return "sed -e ohne Ausdruck"
            ausdruecke.append(args[i + 1].wert)
            i += 2
            continue
        if w.startswith("--expression="):
            ausdruecke.append(w.partition("=")[2])
            i += 1
            continue
        if w.startswith("-") and w != "-":
            i += 1
            continue
        if not ausdruecke:
            ausdruecke.append(w)
        else:
            dateien.append(args[i])
        i += 1
    if not ausdruecke:
        return "sed ohne Ausdruck"
    for expr in ausdruecke:
        if _sed_gefaehrlich(expr):
            return f"sed mit e/w/r-Befehl: {expr[:80]}"
    for d in dateien:
        if _PFAD.search(d.wert.lower()) or _ist_verbotener_schreibpfad(d.wert):
            return f"sed auf geschütztem Pfad: {d.wert}"
    return None


def _sed_gefaehrlich(expr: str) -> bool:
    """True, wenn der ``sed``-Ausdruck ``e``, ``w``/``W`` oder ``r``/``R`` ausführt."""
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch in " \t\n;{}!0123456789,$~+-":
            i += 1
            continue
        if ch == "/":
            i += 1
            while i < n and expr[i] != "/":
                i += 2 if expr[i] == "\\" else 1
            i += 1
            continue
        if ch == "s" and i + 1 < n and not expr[i + 1].isalnum():
            trenner = expr[i + 1]
            i += 2
            for _ in range(2):
                while i < n and expr[i] != trenner:
                    i += 2 if expr[i] == "\\" else 1
                if i >= n:
                    return True
                i += 1
            while i < n and expr[i] not in ";\n}":
                if expr[i] in ("e", "w", "W"):
                    return True
                i += 1
            continue
        if ch in ("e", "w", "W", "r", "R"):
            return True
        if ch in ("y",):
            if i + 1 < n:
                trenner = expr[i + 1]
                i += 2
                for _ in range(2):
                    while i < n and expr[i] != trenner:
                        i += 2 if expr[i] == "\\" else 1
                    i += 1
            continue
        i += 1
    return False


def _pruefe_awk(args: list[_Token]) -> str | None:
    programm: str | None = None
    dateien: list[_Token] = []
    i = 0
    while i < len(args):
        w = args[i].wert
        if w in ("-f", "--file") or w.startswith("--file="):
            return f"awk mit Skript-Datei: {w}"
        if w in ("-F", "-v"):
            i += 2
            continue
        if w.startswith("-") and w != "-":
            i += 1
            continue
        if programm is None:
            programm = w
        else:
            dateien.append(args[i])
        i += 1
    if programm is None:
        return "awk ohne Programm"
    if re.search(r"\b(?:system|getline|ENVIRON)\b|@load|@include|\||>>?", programm):
        return f"awk mit system/getline/Pipe: {programm[:80]}"
    for d in dateien:
        if _PFAD.search(d.wert.lower()):
            return f"awk auf Geheimnis-Pfad: {d.wert}"
    return None


def _pruefe_find(args: list[_Token]) -> str | None:
    for a in args:
        if a.wert in _FIND_VERBOTEN:
            return f"find mit {a.wert}"
        if _PFAD.search(a.wert.lower()):
            return f"find auf Geheimnis-Pfad: {a.wert}"
    return None


def _pruefe_xargs(args: list[_Token]) -> str | None:
    i = 0
    while i < len(args):
        w = args[i].wert
        if w == "--":
            i += 1
            break
        if not w.startswith("-"):
            break
        if w in _XARGS_MIT_WERT:
            if w == "-a" and i + 1 < len(args) and _PFAD.search(args[i + 1].wert.lower()):
                return f"xargs -a mit Geheimnis-Pfad: {args[i + 1].wert}"
            i += 2
            continue
        i += 1
    if i >= len(args):
        return None  # Vorgabe ist echo
    ziel = args[i]
    if ziel.dynamisch or ziel.wert not in BEFEHLE_LESEND:
        return f"xargs mit nicht-lesendem Befehl: {ziel.wert}"
    return _pruefe_keine_geheimnisse(f"xargs {ziel.wert}", args[i + 1 :])


def _pruefe_rm(args: list[_Token]) -> str | None:
    ziele = [a for a in args if not a.wert.startswith("-")]
    if not ziele:
        return "rm ohne Ziel"
    for z in ziele:
        p = z.wert.replace("\\", "/")
        if z.dynamisch or ".." in p or _PFAD.search(p.lower()):
            return f"rm auf unsicherem Pfad: {z.wert}"
        ist_relativ = not p.startswith(("/", "~", "$")) and not re.match(r"^[A-Za-z]:", p)
        erlaubt_abs = any(p.startswith(pr) for pr in RM_ERLAUBTE_PRAEFIXE) or bool(_RM_HOME_WT.match(p))
        if not (ist_relativ or erlaubt_abs):
            return f"rm außerhalb erlaubter Pfade: {z.wert}"
    return None


def _pruefe_python(args: list[_Token], glied: _Glied) -> str | None:
    if any(u.op == "<<<" for u in glied.umleitungen):
        return "python mit Here-String"
    if not args:
        return "python interaktiv"

    i = 0
    while i < len(args) and args[i].wert.startswith("-") and args[i].wert not in ("-", "-c", "-m"):
        w = args[i].wert
        if w in ("-V", "--version"):
            return None
        if w in ("-u", "-B", "-S", "-I", "-q", "-E", "-s", "-O", "-OO"):
            i += 1
            continue
        if w in ("-W", "-X"):
            i += 2
            continue
        return f"python mit unbekannter Option: {w}"
    if i >= len(args):
        return "python ohne Ziel"

    erst = args[i]
    rest = args[i + 1 :]
    if erst.wert == "-m":
        if not rest or rest[0].dynamisch or rest[0].wert not in PYTHON_MODULE_HARMLOS:
            return f"python -m mit unbekanntem Modul: {rest[0].wert if rest else '(leer)'}"
        return _pruefe_keine_geheimnisse(f"python -m {rest[0].wert}", rest[1:])

    if erst.wert == "-c":
        if not rest or rest[0].dynamisch:
            return "python -c ohne festen Code"
        return _pruefe_python_ast(rest[0].wert)

    if erst.wert == "-":
        if len(glied.heredocs) != 1:
            return "python - ohne genau ein Heredoc"
        return _pruefe_python_ast(glied.heredocs[0][0])

    # Eigenes Bau-System-Skript?
    if erst.dynamisch or ".." in erst.wert:
        return f"python mit dynamischem/unsicherem Skript: {erst.wert}"
    basis = Path(erst.wert.replace("\\", "/")).name
    if basis not in EIGENE_SKRIPTE:
        return f"python mit fremdem Skript: {erst.wert}"
    erlaubte_unter = EIGENE_SKRIPTE[basis]
    if erlaubte_unter:
        unter = next((t.wert for t in rest if not t.wert.startswith("-")), None)
        if unter not in erlaubte_unter:
            return f"{basis}: unbekannter Unterbefehl {unter or '(leer)'}"
    return _pruefe_keine_geheimnisse(basis, rest)


def _pruefe_python_ast(code: str) -> str | None:
    """Inline-Python ist nur harmlos, wenn der AST rein rechnend/lesend ohne OS-/Netz-Zugriff ist."""
    try:
        baum = ast.parse(code)
    except (SyntaxError, ValueError):
        return f"python mit unlesbarem Code: {code[:80]}"

    erlaubte_open_namen: set[int] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "open":
            if not _ist_lesendes_open(knoten):
                return "python open() nur rein lesend erlaubt"
            erlaubte_open_namen.add(id(knoten.func))

    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                top = alias.name.split(".")[0]
                if top not in PYTHON_AST_MODULE_HARMLOS:
                    return f"python import {alias.name} nicht erlaubt"
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.level != 0 or not knoten.module:
                return "python relativer Import nicht erlaubt"
            top = knoten.module.split(".")[0]
            if top not in PYTHON_AST_MODULE_HARMLOS:
                return f"python from {knoten.module} import nicht erlaubt"
            for alias in knoten.names:
                if alias.name.startswith("_") or alias.name in _AST_VERBOTENE_NAMEN or alias.name in _AST_VERBOTENE_ATTRS:
                    return f"python Import-Name {alias.name} nicht erlaubt"
        elif isinstance(knoten, ast.Attribute):
            if knoten.attr.startswith("__") or knoten.attr in _AST_VERBOTENE_ATTRS:
                return f"python Attribut .{knoten.attr} nicht erlaubt"
        elif isinstance(knoten, ast.Name):
            if knoten.id in _AST_VERBOTENE_NAMEN:
                return f"python Name {knoten.id} nicht erlaubt"
            if knoten.id == "open" and id(knoten) not in erlaubte_open_namen:
                return "python open-Referenz nicht erlaubt"
        elif (
            isinstance(knoten, ast.Constant)
            and isinstance(knoten.value, str)
            and _PFAD.search(knoten.value.lower())
        ):
            return f"python Geheimnis-Pfad im Code: {knoten.value[:60]}"
    return None


def _ist_lesendes_open(aufruf: ast.Call) -> bool:
    """``open(pfad)`` oder ``open(pfad, 'r'/'rb'/'rt')`` ohne Schreib-Modus."""
    if len(aufruf.args) > 2:
        return False
    if len(aufruf.args) == 2:
        modus = aufruf.args[1]
        if not (isinstance(modus, ast.Constant) and modus.value in _LESE_MODI):
            return False
    for kw in aufruf.keywords:
        if kw.arg == "mode":
            if not (isinstance(kw.value, ast.Constant) and kw.value.value in _LESE_MODI):
                return False
        elif kw.arg is None:
            return False
    return True


def _pruefe_npm(name: str, args: list[_Token]) -> str | None:
    if not args:
        return f"{name} ohne Unterbefehl"
    if args[0].wert == "run":
        if len(args) < 2 or args[1].wert not in NPM_HARMLOS:
            return f"{name} run {args[1].wert if len(args) > 1 else '(leer)'}"
        return None
    if args[0].wert in NPM_HARMLOS:
        return None
    return f"{name} {args[0].wert}"


def _pruefe_graphify(args: list[_Token]) -> str | None:
    if not args or args[0].wert not in GRAPHIFY_HARMLOS:
        return f"graphify {args[0].wert if args else '(leer)'}"
    return _pruefe_keine_geheimnisse("graphify", args[1:])


def _pruefe_docker(args: list[_Token], *, nur_lesend: bool, tiefe: int) -> str | None:
    if not args:
        return "docker ohne Unterbefehl"
    unter = args[0].wert
    if unter in DOCKER_HARMLOS:
        return _pruefe_keine_geheimnisse(f"docker {unter}", args[1:])
    if unter == "exec":
        return _pruefe_docker_exec(args[1:], tiefe=tiefe)
    return f"docker {unter}"


def _pruefe_docker_exec(args: list[_Token], *, tiefe: int) -> str | None:
    i = 0
    while i < len(args) and args[i].wert.startswith("-"):
        w = args[i].wert
        if w in _DOCKER_EXEC_OHNE_WERT:
            i += 1
            continue
        if w in _DOCKER_EXEC_MIT_WERT:
            if w in ("-e", "--env") and i + 1 < len(args):
                var = args[i + 1].wert.partition("=")[0]
                if _ist_verbotene_env(var):
                    return f"docker exec mit verbotener Variable: {var}"
            i += 2
            continue
        return f"docker exec mit Option {w}"
    if i >= len(args) or args[i].dynamisch:
        return "docker exec ohne Container"
    innen_tokens = args[i + 1 :]
    if not innen_tokens:
        return "docker exec ohne Befehl"
    return _befehl_unbekannt(_befehl_text(innen_tokens), nur_lesend=True, tiefe=tiefe + 1)


def _pruefe_tmux(args: list[_Token]) -> str | None:
    i = 0
    while i < len(args) and args[i].wert.startswith("-"):
        w = args[i].wert
        if w in _TMUX_GLOBAL_MIT_WERT:
            if i + 1 < len(args) and _PFAD.search(args[i + 1].wert.lower()):
                return f"tmux {w} mit Geheimnis-Pfad"
            i += 2
            continue
        i += 1
    if i >= len(args) or args[i].wert not in TMUX_HARMLOS:
        return f"tmux {args[i].wert if i < len(args) else '(leer)'}"
    return _pruefe_keine_geheimnisse(f"tmux {args[i].wert}", args[i + 1 :])


def _pruefe_gh(args: list[_Token], *, nur_lesend: bool) -> str | None:
    if not args:
        return "gh ohne Unterbefehl"
    gruppe = args[0].wert
    if gruppe == "status":
        return None
    if gruppe == "api":
        return _pruefe_gh_api(args[1:])
    if gruppe in GH_HARMLOS:
        if len(args) < 2:
            return f"gh {gruppe} ohne Aktion"
        aktion = args[1].wert
        if aktion not in GH_HARMLOS[gruppe]:
            return f"gh {gruppe} {aktion}"
        if nur_lesend and aktion in ("comment", "create", "edit"):
            return f"gh {gruppe} {aktion} im Lese-Modus"
        return _pruefe_keine_geheimnisse(f"gh {gruppe} {aktion}", args[2:])
    return f"gh {gruppe}"


def _pruefe_gh_api(args: list[_Token]) -> str | None:
    if not args:
        return "gh api ohne Ziel"
    for a in args:
        w = a.wert
        if w in _GH_API_VERBOTENE_FLAGS or any(w.startswith(p) for p in _GH_API_VERBOTENE_PRAEFIXE):
            return f"gh api mit Schreib-Option: {w}"
        if "/merge" in w.lower() or "secrets" in w.lower() or _PFAD.search(w.lower()):
            return f"gh api auf geschütztem Ziel: {w}"
    return None


def _pruefe_git(args: list[_Token], *, nur_lesend: bool) -> str | None:
    i = 0
    while i < len(args) and args[i].wert.startswith("-"):
        w = args[i].wert
        if w in _GIT_GLOBAL_OHNE_WERT:
            i += 1
            continue
        if w in _GIT_GLOBAL_MIT_WERT:
            if i + 1 >= len(args) or _PFAD.search(args[i + 1].wert.lower()):
                return f"git {w} ungültig"
            i += 2
            continue
        if w.startswith(("--git-dir=", "--work-tree=")):
            if _PFAD.search(w.partition("=")[2].lower()):
                return f"git {w} mit Geheimnis-Pfad"
            i += 1
            continue
        return f"git mit verbotener Global-Option: {w}"
    if i >= len(args):
        return "git ohne Unterbefehl"

    unter = args[i].wert
    rest = args[i + 1 :]
    if any(
        a.wert in ("--upload-pack", "--receive-pack", "--exec-path")
        or a.wert.startswith(("--upload-pack=", "--receive-pack=", "--exec-path="))
        for a in rest
    ):
        return f"git {unter} mit Ausführungs-Option"
    if unter == "push":
        if nur_lesend:
            return "git push im Lese-Modus"
        return _pruefe_git_push(rest)
    if unter == "branch":
        return _pruefe_git_branch(rest, nur_lesend=nur_lesend) or _pruefe_keine_geheimnisse("git branch", rest)
    if unter == "remote":
        return _pruefe_git_remote(rest) or _pruefe_keine_geheimnisse("git remote", rest)
    if unter == "config":
        return _pruefe_git_config(rest) or _pruefe_keine_geheimnisse("git config", rest)
    if unter in GIT_HARMLOS:
        if nur_lesend and unter not in _GIT_LESEND:
            return f"git {unter} im Lese-Modus"
        return _pruefe_keine_geheimnisse(f"git {unter}", rest)
    return f"git {unter}"


def _pruefe_git_push(args: list[_Token]) -> str | None:
    positionale: list[_Token] = []
    for a in args:
        if a.wert.startswith("-"):
            if a.wert not in _GIT_PUSH_FLAGS:
                return f"git push mit verbotener Option: {a.wert}"
        else:
            positionale.append(a)
    if len(positionale) < 2:
        return "git push ohne ausdrückliches Ziel"
    remote = positionale[0]
    if remote.dynamisch or ":" in remote.wert or "/" in remote.wert:
        return f"git push mit ungewöhnlichem Remote: {remote.wert}"
    for ziel in positionale[1:]:
        w = ziel.wert
        if ziel.dynamisch or "+" in w or ":" in w or ".." in w:
            return f"git push mit unsicherem Refspec: {w}"
        if not any(
            w.startswith(p) and _PUSH_ZIEL.fullmatch(w[len(p) :]) for p in GIT_PUSH_PRAEFIXE
        ):
            return f"git push auf nicht erlaubtes Ziel: {w}"
    return None


def _pruefe_git_branch(args: list[_Token], *, nur_lesend: bool) -> str | None:
    for a in args:
        w = a.wert
        if w in ("-D", "-d", "--delete", "-f", "--force", "-m", "-M", "--move", "-c", "-C", "--copy"):
            return f"git branch mit {w}"
        if re.fullmatch(r"-[a-zA-Z]*[dDfFmM][a-zA-Z]*", w):
            return f"git branch mit {w}"
    if nur_lesend and any(not a.wert.startswith("-") for a in args):
        return "git branch schreibend im Lese-Modus"
    return None


def _pruefe_git_remote(args: list[_Token]) -> str | None:
    if not args:
        return None
    if args[0].wert in ("-v", "--verbose", "show", "get-url"):
        return None
    return f"git remote {args[0].wert}"


def _pruefe_git_config(args: list[_Token]) -> str | None:
    lese_flags = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin"}
    if any(a.wert in lese_flags for a in args):
        return None
    return "git config ohne Lese-Schalter"


def _pruefe_ssh(args: list[_Token], *, tiefe: int) -> str | None:
    i = 0
    while i < len(args) and args[i].wert.startswith("-"):
        w = args[i].wert
        if w in _SSH_OPT_OHNE_WERT:
            i += 1
            continue
        if w in _SSH_OPT_MIT_WERT:
            if i + 1 >= len(args):
                return f"ssh {w} ohne Wert"
            if w == "-o" and any(v in args[i + 1].wert.lower() for v in _SSH_VERBOTENE_O):
                return f"ssh mit verbotener Option: -o {args[i + 1].wert}"
            i += 2
            continue
        return f"ssh mit Option {w}"
    if i >= len(args) or args[i].dynamisch:
        return "ssh ohne festen Host"
    fern = args[i + 1 :]
    if not fern:
        return "ssh ohne Befehl"
    fern_cmd = fern[0].wert if len(fern) == 1 else " ".join(t.wert for t in fern)
    return _befehl_unbekannt(fern_cmd, nur_lesend=True, tiefe=tiefe + 1)
