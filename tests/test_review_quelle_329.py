"""#329 P1: ``review/`` ist die versionierte Quelle des Review-Werkzeugs.

Geprüft: (a) jede Datei, die Hook (``review-hook/_hookbasis.py``) und Skill
``review-dirigent`` aufrufen, liegt unter ``review/``; (b) keine Laufzeit-Daten
(``sitzungen/``, ``*.jsonl``, ``*.log``, ``__pycache__``) im Repo; (c) das Repo ist
öffentlich — kein Projektname, kein privater Pfad, kein Personenname, kein Geheimnis.
Die Muster für (c) stammen aus den bestehenden Namens-Prüfungen (#323, L1).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from test_feste_namen_l1 import MUSTER
from test_rolle_statt_name_323 import NAME

SKILL = Path(__file__).resolve().parents[1]
REVIEW = SKILL / "review"

SKRIPTE = (
    "_gemeinsam.py",
    "review_route.py",
    "review_beleg.py",
    "review_bericht.py",
    "rot_probe.py",
)
DOKU = ("VERTRAG.md", "config.standard.json", ".gitignore")
AUFTRAEGE = (
    "basis.md",
    "datenbank.md",
    "fehlerpfad.md",
    "infra.md",
    "kosten.md",
    "n8n.md",
    "oberflaeche.md",
    "python.md",
    "react.md",
    "richter.md",
    "shell.md",
    "sicherheit.md",
    "spec.md",
    "tests.md",
    "typescript.md",
)
TEST_HILFEN = ("tests/conftest.py", "tests/review_hilfen.py")

LAUFZEIT = re.compile(r"(^|/)(sitzungen/|__pycache__/)|\.(jsonl|log|pyc)$")
#: Geheimnis-Formen (echte Längen; Attrappen wie ``sk-abc123`` bleiben erlaubt).
GEHEIMNIS = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
)
#: Begriffe aus App-Projekten (Plattform-Abläufe, Modul-Namen) — gehören in deren ``.review/regeln.md``.
#: ``reels?``/``follows?`` fielen weg (#329 P2): normales Englisch („follows the rule“).
PROJEKT_BEGRIFF = re.compile(
    r"\b(rpa|instagram|postproxy|phone_power|account_db|duoplus)\b",
    re.IGNORECASE,
)
#: Mail-Adressen; reservierte Beispiel-Domains (RFC 2606) und ``\n@`` in Strings bleiben erlaubt.
MAIL = re.compile(
    r"(?<![\\\w.+-])[\w.+-]+@(?!example\.(?:invalid|com|org|net)\b)[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b",
    re.IGNORECASE,
)
#: IPv4 außer Loopback, 0.0.0.0 und Doku-Netzen (RFC 5737).
IP = re.compile(
    r"(?<![\d.])(?!(?:127|0)\.)(?!192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
#: Hostnamen mit gängiger Endung; Beispiel-Domains und öffentliche Dienste bleiben erlaubt.
HOST = re.compile(
    r"\b(?!(?:[\w-]+\.)*(?:example\.(?:com|org|net)|github\.com|githubusercontent\.com"
    r"|anthropic\.com|claude\.(?:ai|com)|python\.org|pypi\.org)\b)"
    r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:de|com|net|org|io|app|dev|cloud|eu|ai)\b",
    re.IGNORECASE,
)
#: Private Namensliste (Firmen-, Host-, Personennamen) — liegt nie im Repo, das Repo ist öffentlich.
#: Format: UTF-8, ein Name je Zeile, ``#`` leitet Kommentare ein; Vergleich ganzer Wörter ohne Groß/klein.
NAMEN_DATEI = Path.home() / ".claude" / "review" / "namen_privat.txt"


def lade_namen(pfad: Path) -> frozenset[str]:
    """Namen aus ``pfad``; fehlt die Datei, ist die Liste leer (Mechanik prüft dann ein Kunstwort)."""
    try:
        text = pfad.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    return frozenset(z.split("#", 1)[0].strip().lower() for z in text.splitlines()) - {""}


class _Namenmuster:
    """Wie ``re.Pattern.search``: trifft, wenn ein Wort der Zeile in der Namensliste steht."""

    def __init__(self, namen: frozenset[str]) -> None:
        self.namen = namen

    def search(self, zeile: str) -> bool:
        return not self.namen.isdisjoint(re.findall(r"[^\W_]+", zeile.lower()))


FREMDNAME = _Namenmuster(lade_namen(NAMEN_DATEI))
OEFFENTLICH = {
    **MUSTER,
    "personenname": NAME,
    "geheimnis": GEHEIMNIS,
    "projekt-begriff": PROJEKT_BEGRIFF,
    "mail": MAIL,
    "ip-adresse": IP,
    "hostname": HOST,
    "firmen-oder-personenname": FREMDNAME,
}
#: Ausnahmen je Zeile (#329 P2): (Datei, Begriff) → Textstück, das in derselben Zeile stehen muss.
#: Projekt-Wörter des Routers leben nur in ``config.standard.json`` → ``projekt_woerter``.
AUSNAHMEN = {
    ("review/config.standard.json", "account_db"): '"projekt_woerter":',
    ("review/config.standard.json", "postproxy"): '"projekt_woerter":',
    ("review/config.standard.json", "phone_power"): '"projekt_woerter":',
    ("review/tests/test_router_runde2.py", "account_db"): '["account_db.set_status(x)"]',
}


def _verboten(datei: str, art: str, muster: re.Pattern[str] | _Namenmuster, zeile: str) -> bool:
    if not isinstance(muster, re.Pattern) or art != "projekt-begriff":
        return bool(muster.search(zeile))
    for m in muster.finditer(zeile):
        stueck = AUSNAHMEN.get((datei, m.group(0).lower()))
        if stueck is None or stueck not in zeile:
            return True
    return False


def _review_dateien() -> list[str]:
    """Dateien unter ``review/``, die ins Repo gehören (versioniert oder nicht ignoriert)."""
    raus = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "review"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [z for z in raus.stdout.splitlines() if (SKILL / z).is_file()]


def test_quelle_vollstaendig() -> None:
    soll = [
        *SKRIPTE,
        *DOKU,
        *(f"auftraege/{a}" for a in AUFTRAEGE),
        *TEST_HILFEN,
    ]
    fehlt = [d for d in soll if not (REVIEW / d).is_file()]
    assert fehlt == [], f"fehlt unter review/: {fehlt}"
    assert len(list((REVIEW / "tests").glob("test_*.py"))) >= 17


def test_keine_laufzeit_daten_im_repo() -> None:
    funde = [d for d in _review_dateien() if LAUFZEIT.search(d)]
    assert funde == [], f"Laufzeit-Daten gehören nicht ins Repo: {funde}"


def test_oeffentlich_tauglich() -> None:
    funde = [
        f"{datei}:{nr} ({art})"
        for datei in _review_dateien()
        for nr, zeile in enumerate((SKILL / datei).read_text(encoding="utf-8").splitlines(), 1)
        for art, muster in OEFFENTLICH.items()
        if _verboten(datei, art, muster, zeile)
    ]
    assert funde == [], f"nicht öffentlich-tauglich: {funde}"


def test_review_tests_ohne_fremdes_conftest() -> None:
    """Werkzeug-Tests holen Helfer aus ``review_hilfen``, nie per ``from conftest``."""
    funde = [
        f"{p.name}:{nr}"
        for p in sorted((REVIEW / "tests").glob("*.py"))
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\s*(from conftest import|import conftest)", zeile)
    ]
    assert funde == [], f"from conftest im Werkzeug-Test: {funde}"


@pytest.mark.parametrize(
    ("art", "zeile", "treffer"),
    [
        ("projekt-begriff", "Likes nur im Instagram-Feed", True),
        ("projekt-begriff", "Abläufe per RPA", True),
        ("projekt-begriff", "This follows the rule; reels of tape", False),
        ("ip-adresse", "ssh root@203.0.113.7 ok, aber 46.38.1.2 nicht", True),
        ("ip-adresse", "Loopback 127.0.0.1, Version 3.11.4", False),
        ("hostname", "curl https://api.meine-firma.de/x", True),
        ("hostname", "siehe https://github.com/x und mail@example.com", False),
        ("hostname", "config.json, _gemeinsam.py, os.environ", False),
        ("mail", "an jemand@firma.io", True),
        ("mail", "an jemand@example.org", False),
    ],
)
def test_oeffentlich_muster_beispiele(art: str, zeile: str, treffer: bool) -> None:
    """Treffer- und Nicht-Treffer-Beispiele je Muster (Befund Review 334)."""
    assert _verboten("probe.md", art, OEFFENTLICH[art], zeile) is treffer


def test_fremdname_mechanik_mit_kunstwort() -> None:
    """Ohne private Liste prüft der Test die Mechanik an erfundenen Wörtern."""
    muster = _Namenmuster(frozenset({"beispielname"}))
    assert muster.search("Frage an Beispielname, bitte")
    assert not muster.search("Frage an die Rolle, beispielnamen bleiben frei")


def test_namensliste_format(tmp_path: Path) -> None:
    datei = tmp_path / "namen.txt"
    datei.write_text("# Kommentar\nBeispielname\n  Musterfirma  # Firma\n\n", encoding="utf-8")
    assert lade_namen(datei) == {"beispielname", "musterfirma"}
    assert lade_namen(tmp_path / "fehlt.txt") == frozenset()


def test_ausnahme_gilt_nur_fuer_ihre_zeile() -> None:
    """Eine Ausnahme gibt den Begriff nicht für die ganze Datei frei (Befund Review 334)."""
    datei = "review/config.standard.json"
    erlaubt = '  "projekt_woerter": {"datenbank": ["account_db"]},'
    assert not _verboten(datei, "projekt-begriff", PROJEKT_BEGRIFF, erlaubt)
    assert _verboten(datei, "projekt-begriff", PROJEKT_BEGRIFF, '  "x": "account_db",')


def _hook_verweise() -> set[str]:
    """Dateien unter ``review/``, die der Hook anspricht: ``REVIEW / "<datei>"`` und Importe daraus."""
    hook = SKILL / "review-hook"
    eigene = {p.stem for p in hook.glob("*.py")}
    namen: set[str] = set()
    for p in hook.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        namen |= set(re.findall(r'REVIEW\s*/\s*"([\w.]+)"', text))
        namen |= {f"{m}.py" for m in re.findall(r"^\s*from (\w+) import", text, re.MULTILINE) if m not in eigene}
    return {n for n in namen if (REVIEW / n).exists() or n.startswith(("_", "review_", "rot_"))} - {"__future__.py"}


def _vertrag_verweise() -> set[str]:
    """Einträge aus ``VERTRAG.md`` §1 (Ordner-Baum unter ``~/.claude/review/``) ohne Laufzeit-Daten."""
    text = (REVIEW / "VERTRAG.md").read_text(encoding="utf-8")
    block = text.split("~/.claude/review/\n", 1)[1].split("~/.claude/skills/", 1)[0]
    eintraege = {z.split()[0].rstrip("/") for z in block.splitlines() if z.strip()}
    return {e for e in eintraege if not LAUFZEIT.search(e)}


def _import_huelle(start: set[str]) -> set[str]:
    """Ergänzt lokale Importe der Skripte (``from _gemeinsam import …``), bis nichts Neues dazukommt."""
    huelle, offen = set(start), [n for n in start if n.endswith(".py")]
    while offen:
        pfad = REVIEW / offen.pop()
        if not pfad.is_file():
            continue
        for m in re.findall(r"^\s*from (\w+) import", pfad.read_text(encoding="utf-8"), re.MULTILINE):
            if (REVIEW / f"{m}.py").is_file() and f"{m}.py" not in huelle:
                huelle.add(f"{m}.py")
                offen.append(f"{m}.py")
    return huelle


def test_quelle_deckt_hook_und_vertrag() -> None:
    """Skriptliste aus Hook + Vertrag abgeleitet statt fest (Befund Review 334)."""
    hook = _hook_verweise()
    assert {"review_route.py", "review_beleg.py", "review_bericht.py", "_gemeinsam.py"} <= hook
    soll = _import_huelle(hook | _vertrag_verweise())
    fehlt = sorted(d for d in soll if not (REVIEW / d).exists())
    assert fehlt == [], f"Hook/Vertrag nennen, fehlt unter review/: {fehlt}"
    ungenannt = sorted({p.name for p in REVIEW.glob("*.py")} - {"__init__.py"} - soll)
    assert ungenannt == [], f"Skript weder vom Hook genutzt noch in VERTRAG.md §1: {ungenannt}"
