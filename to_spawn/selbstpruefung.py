"""KI-Selbstprüfung klickt die Rundschau gegen Staging (#368).

Dieselbe Form wie ``skripte/rueckblick.py``, aufgerufen über die Unterbefehle
``skripte/abschluss_paket.py selbstpruefung planen|sammeln``:

* ``planen`` liest die Staging-Adresse aus ``.to-spawn/config.json`` (``staging.url``) und
  die Klick-Schrittfolgen aus der Rundschau (``rundschau_<S>.html``) bzw. dem
  Rundschau-Auftrag (``rundschau_auftrag_<S>.md``) und schreibt
  ``.to-spawn/selbstpruefung/<S>/auftrag.md`` + ``lauf.json`` (Sperre).
  Exit 0 = Auftrag geschrieben · Exit 2 = Staging fehlt („Selbstprüfung nicht möglich, weil …“) ·
  Exit 4 = schon grün erledigt oder Sperre aktiv · Exit 1 = Fehler.
* Ein frischer Helfer klickt jede Klick-Schrittfolge im kopflosen Browser gegen Staging
  (nur lesen und klicken; markierte Dummy-Daten am Ende wieder löschen) und schreibt
  ``.to-spawn/selbstpruefung/<S>/ergebnis.json``.
* ``sammeln`` prüft ``ergebnis.json``, schreibt atomar den Marker
  ``<pfade.agenten>/selbstpruefung_<S>.md`` und gibt die Sperre frei.
  Exit 0 = Marker geschrieben · Exit 3 = ``ergebnis.json`` fehlt ·
  Exit 4 = schon erledigt oder ``planen`` läuft noch · Exit 1 = Ergebnis ungültig.
* ``pruefe_fuer_abnahme`` speist ``abschluss_paket.py ablegen --stand abnahme``:
  ohne ``ergebnis.json`` bei gesetzter ``staging.url`` wird die Ablage verweigert;
  ohne ``staging.url`` wird „Selbstprüfung nicht möglich, weil …“ im Paket vermerkt;
  „passt nicht“-Schritte erscheinen sichtbar im Abschluss-Paket.

Browser-Entscheidung (Lastenheft L9 A53): kopfloser Browser (Playwright Chromium headless
bzw. ``chrome-devtools --headless``), weil er auf dem Linux-Bau-Server ohne Display und
auf Desktop-Rechnern gleichermaßen läuft.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from to_spawn import config, manifest, rundschau_deck

log = logging.getLogger("to_spawn.selbstpruefung")

SPERRE_MIN = 120
BROWSER_MODUS = "kopflos"
BROWSER_HINWEIS = (
    "Kopfloser Browser (Playwright Chromium headless oder chrome-devtools --headless; "
    "läuft ohne Bildschirm auf Bau-Server und PC)."
)
_ERGEBNIS_WERTE = {
    "passt": "passt",
    "passt nicht": "passt nicht",
    "passt_nicht": "passt nicht",
}
_TICKET_KOPF = re.compile(r"^###\s+Ticket\s+#(\d+)\s*[—–-]\s*(.+?)\s*$")
_DIREKT_ZEILE = re.compile(r"^-\s+\*\*Direkt-Link in die Staging-App\*\*:\s*(.+?)\s*$")


class Fehler(Exception):
    """Abbruch mit klarer Meldung (Exit 1)."""


def agenten_ordner(repo: Path) -> Path:
    """Ordner ``pfade.agenten`` aus ``.to-spawn/config.json`` unter ``repo``."""
    return config.pfad(config.Konfig(config.lade(repo), repo / config.KONFIG_PFAD), "agenten")


def arbeits_ordner(repo: Path, spec: int) -> Path:
    """Arbeitsordner der Selbstprüfung (nicht versioniert unter ``.to-spawn``)."""
    return repo / ".to-spawn" / "selbstpruefung" / str(spec)


def auftrag_pfad(repo: Path, spec: int) -> Path:
    return arbeits_ordner(repo, spec) / "auftrag.md"


def ergebnis_pfad(repo: Path, spec: int) -> Path:
    return arbeits_ordner(repo, spec) / "ergebnis.json"


def sperre_pfad(repo: Path, spec: int) -> Path:
    return arbeits_ordner(repo, spec) / "lauf.json"


def marker_pfad(repo: Path, spec: int) -> Path:
    return agenten_ordner(repo) / f"selbstpruefung_{spec}.md"


def _rel(repo: Path, pfad: Path) -> str:
    try:
        return pfad.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return pfad.as_posix()


def _url_ohne_zugang(url: str) -> str:
    """HTTP(S)-Adresse ohne ``nutzer:pass@``; ungültige Adresse → leer."""
    teile = urllib.parse.urlsplit(url.strip())
    if teile.scheme not in ("http", "https") or not teile.hostname:
        return ""
    netloc = teile.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit(teile._replace(netloc=netloc))


def staging_status(repo: Path, stage_override: str = "") -> tuple[str, str]:
    """Liefert ``(saubere_url, grund_wenn_nicht_moeglich)`` für die Selbstprüfung."""
    if stage_override.strip():
        sauber = _url_ohne_zugang(stage_override)
        if not sauber:
            raise Fehler(f"--stage „{stage_override}“ ist keine gültige http(s)-Adresse.")
        return sauber, ""
    konfig = config.lade(repo)
    staging = konfig.get("staging") or {}
    if not isinstance(staging, dict):
        staging = {}
    if staging.get("ohne"):
        return "", "Selbstprüfung nicht möglich, weil staging.ohne in .to-spawn/config.json gesetzt ist."
    roh = str(staging.get("url") or "").strip()
    if not roh:
        return "", "Selbstprüfung nicht möglich, weil staging.url in .to-spawn/config.json fehlt."
    sauber = _url_ohne_zugang(roh)
    if not sauber:
        return (
            "",
            "Selbstprüfung nicht möglich, weil staging.url in .to-spawn/config.json keine gültige http(s)-Adresse ist.",
        )
    return sauber, ""


def _sperre_aktiv(sperre: Path, min_alter: int = SPERRE_MIN) -> bool:
    if not sperre.exists():
        return False
    try:
        start = datetime.fromisoformat(json.loads(sperre.read_text(encoding="utf-8"))["start"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    jetzt = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
    return (jetzt - start) < timedelta(minutes=min_alter)


def _sperre_anlegen(sperre: Path, inhalt: dict[str, Any]) -> bool:
    """Sperre atomar anlegen; abgelaufene Sperre wird ersetzt. ``False`` = aktiv belegt."""
    for _ in range(2):
        try:
            datei = sperre.open("x", encoding="utf-8")
        except FileExistsError:
            if _sperre_aktiv(sperre):
                return False
            sperre.unlink(missing_ok=True)
            continue
        fertig = False
        try:
            with datei:
                datei.write(json.dumps(inhalt, ensure_ascii=False))
            fertig = True
        finally:
            if not fertig:
                sperre.unlink(missing_ok=True)
        return True
    return False


def _direktlinks_aus_auftrag(pfad: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Liest Ticket-Direkt-Links und Fallback-Folgen aus ``rundschau_auftrag_<S>.md``."""
    if not pfad.is_file():
        return {}, []
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except OSError as fehler:
        log.warning("Rundschau-Auftrag %s nicht lesbar: %s", pfad, fehler)
        return {}, []
    links: dict[str, str] = {}
    fallback: list[dict[str, Any]] = []
    aktuell_nr = ""
    aktuell_titel = ""
    aktuell_abnahme = False
    for zeile in zeilen:
        kopf = _TICKET_KOPF.match(zeile)
        if kopf:
            aktuell_nr = kopf.group(1)
            titel_roh = kopf.group(2)
            aktuell_abnahme = "(Abnahme-Ticket)" in titel_roh or titel_roh.lower().startswith("abnahme")
            aktuell_titel = titel_roh.replace("(Abnahme-Ticket)", "").strip()
            if not aktuell_abnahme:
                fallback.append(
                    {
                        "ticket": aktuell_nr,
                        "titel": f"Ticket #{aktuell_nr} — {aktuell_titel}",
                        "schritte": [
                            f"Klick-Schrittfolge für Ticket #{aktuell_nr} ({aktuell_titel}) auf Staging prüfen"
                        ],
                    }
                )
            continue
        if aktuell_nr:
            treffer = _DIREKT_ZEILE.match(zeile)
            if treffer:
                links[aktuell_nr] = treffer.group(1).strip()
    return links, fallback


def _manifest_fallback(repo: Path, spec: int) -> list[dict[str, Any]]:
    pfad = manifest.manifest_pfad(repo, spec)
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        tickets = daten.get("tickets") or {}
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(tickets, dict):
        return []
    folgen: list[dict[str, Any]] = []
    for nr, info in tickets.items():
        titel = str((info or {}).get("title") or "").strip() if isinstance(info, dict) else ""
        if titel.lower().startswith("abnahme"):
            continue
        folgen.append(
            {
                "ticket": str(nr),
                "titel": f"Ticket #{nr} — {titel or 'Funktion'}",
                "schritte": [f"Funktion aus Ticket #{nr} ({titel or 'Funktion'}) auf Staging durchklicken"],
            }
        )
    return folgen


def _sammle_folgen(repo: Path, spec: int, rundschau_datei: Path | None) -> tuple[list[dict[str, Any]], str, str]:
    """Klick-Schrittfolgen aus Rundschau-Deck (bevorzugt), Rundschau-Auftrag oder Manifest."""
    agenten = agenten_ordner(repo)
    auftrag_md = agenten / f"rundschau_auftrag_{spec}.md"
    links, auftrag_folgen = _direktlinks_aus_auftrag(auftrag_md)

    html_pfad = rundschau_datei if rundschau_datei is not None else (agenten / f"rundschau_{spec}.html")
    if rundschau_datei is not None and not html_pfad.is_absolute() and not html_pfad.is_file():
        html_pfad = repo / rundschau_datei
    if rundschau_datei is not None and not html_pfad.is_file():
        raise Fehler(f"Rundschau-Datei nicht gefunden: {rundschau_datei}")

    folgen: list[dict[str, Any]] = []
    html_rel = ""
    if html_pfad.is_file():
        try:
            html_text = html_pfad.read_text(encoding="utf-8")
        except OSError as fehler:
            raise Fehler(f"Rundschau-Datei {html_pfad} nicht lesbar: {fehler}") from fehler
        html_rel = _rel(repo, html_pfad)
        folgen = rundschau_deck.klickschritte(html_text)

    if not folgen:
        folgen = auftrag_folgen or _manifest_fallback(repo, spec)
    if not folgen:
        raise Fehler(
            f"Keine Klick-Schrittfolgen für Spec #{spec} gefunden: weder Rundschau-Deck noch Rundschau-Auftrag oder Manifest vorhanden."
        )
    for f in folgen:
        nr = str(f.get("ticket") or "")
        f["direktlink"] = links.get(nr, "")
    auftrag_rel = _rel(repo, auftrag_md) if auftrag_md.is_file() else ""
    return folgen, html_rel, auftrag_rel


def auftrag_text(
    repo: Path,
    spec: int,
    stage: str,
    folgen: list[dict[str, Any]],
    *,
    rundschau_rel: str = "",
    rundschau_auftrag_rel: str = "",
    runde: int = 1,
) -> str:
    """Markdown-Auftrag für den frischen Selbstprüfungs-Helfer (#368, Runden #370)."""
    ergebnis_rel = _rel(repo, ergebnis_pfad(repo, spec))
    konfig = config.lade(repo)
    zugang_datei = str((konfig.get("staging") or {}).get("zugang_datei") or "").strip()
    runde_zusatz = f" (Runde {runde})" if runde > 1 else ""

    zeilen = [
        f"# KI-Selbstprüfung Spec #{spec}{runde_zusatz} (Rundschau gegen Staging)",
        "",
        f"- **Stage-App**: {stage}",
        f"- **Browser**: {BROWSER_HINWEIS}",
    ]
    if zugang_datei:
        zeilen.append(
            f"- **Staging-Zugangsdatei (falls Basic-Auth nötig)**: `{zugang_datei}` (Passwörter nie in Belege oder JSON schreiben)"
        )
    if rundschau_rel:
        zeilen.append(f"- **Rundschau-Deck**: `{rundschau_rel}`")
    if rundschau_auftrag_rel:
        zeilen.append(f"- **Rundschau-Auftrag**: `{rundschau_auftrag_rel}`")
    zeilen.append(f"- **Ergebnis-Datei**: `{ergebnis_rel}`")
    zeilen.extend(
        [
            "",
            "## Auftrag",
            "",
            "Du bist ein frischer Prüf-Helfer. Klicke jede Klick-Schrittfolge unten im kopflosen Browser",
            "gegen die Staging-Adresse durch und prüfe, ob jeder Schritt wirklich funktioniert.",
            "Dokumentiere je Schritt `passt` oder `passt nicht` plus einen konkreten Beleg (Screenshot-Pfad",
            "oder sichtbare Ausgabe / HTTP-Status / DOM-Text).",
            "",
            "## Testdaten-Regel (Pflicht)",
            "",
            "- **Nur lesen und klicken**: keine Daten dauerhaft auf Staging anlegen oder verändern.",
            "- Falls ein Schritt zwingend Eingaben/Anlegen erfordert: ausschließlich klar markierte",
            "  **Dummy-Daten** verwenden (Präfix `SELBSTPRUEFUNG-DUMMY-`) und diese am Ende des Schritts",
            "  vollständig wieder **löschen**.",
            '- Prüfe am Schluss, dass keine Testdaten auf Staging zurückbleiben (`"testdaten_bereinigt": true`).',
            "- Passwörter oder Zugangsdaten gehören niemals in `ergebnis.json` oder in Beleg-Texte.",
            "",
            "## Klick-Schrittfolgen",
            "",
        ]
    )
    for idx, f in enumerate(folgen, 1):
        ticket = f"Ticket #{f['ticket']} — " if f.get("ticket") and f"#{f['ticket']}" not in f["titel"] else ""
        zeilen.append(f"### Folge {idx}: {ticket}{f['titel']}")
        if f.get("direktlink"):
            zeilen.append(f"- **Direkt-Link**: {f['direktlink']}")
        for nr, schritt in enumerate(f["schritte"], 1):
            zeilen.append(f"{nr}. {schritt}")
        zeilen.append("")

    beispiel_ticket = str(folgen[0].get("ticket") or "10")
    beispiel_titel = str(folgen[0].get("titel") or "Ticket #10")
    beispiel_schritt = str(folgen[0]["schritte"][0]) if folgen[0].get("schritte") else "Stage öffnen"
    schema_daten: dict[str, Any] = {
        "spec": spec,
        **({"runde": runde} if runde > 1 else {}),
        "stage": stage,
        "browser": BROWSER_MODUS,
        "testdaten_bereinigt": True,
        "folgen": [
            {
                "ticket": beispiel_ticket,
                "titel": beispiel_titel,
                "schritte": [
                    {
                        "schritt": beispiel_schritt,
                        "ergebnis": "passt",
                        "beleg": f"docs/verify-hard/selbstpruefung_{spec}_{beispiel_ticket}_1.png",
                        "hinweis": "",
                    }
                ],
            }
        ],
    }
    schema = json.dumps(schema_daten, ensure_ascii=False, indent=2)
    zeilen.extend(
        [
            "## Ergebnisformat",
            "",
            f"Schreibe das Ergebnis als gültiges JSON nach `{ergebnis_rel}`.",
            'Erlaubte Werte für `ergebnis` sind ausschließlich `"passt"` und `"passt nicht"`.',
            "Jeder Schritt braucht einen nicht leeren `beleg` (Bildpfad oder konkrete Ausgabe).",
            "",
            "```json",
            schema,
            "```",
            "",
        ]
    )
    return "\n".join(zeilen)


def _text(obj: dict[str, Any], feld: str, ort: str, *, pflicht: bool = True) -> str:
    wert = obj.get(feld)
    if wert is None and not pflicht:
        return ""
    if not isinstance(wert, str):
        raise Fehler(f"{ort}: '{feld}' fehlt oder ist kein Text, ist {wert!r}")
    sauber = " ".join(wert.split())
    if pflicht and not sauber:
        raise Fehler(f"{ort}: '{feld}' fehlt oder ist leer")
    return sauber


def lies_ergebnis(pfad: Path, spec: int) -> dict[str, Any]:
    """``ergebnis.json`` der Selbstprüfung prüfen und normiert zurückgeben."""
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except ValueError as fehler:
        raise Fehler(f"{pfad} ist kein gültiges JSON: {fehler}") from fehler
    except OSError as fehler:
        raise Fehler(f"{pfad} nicht lesbar: {fehler}") from fehler
    if not isinstance(daten, dict):
        raise Fehler(f"{pfad}: kein JSON-Objekt")
    if type(daten.get("spec")) is not int or daten["spec"] != spec:
        raise Fehler(f"{pfad}: 'spec' fehlt oder ist {daten.get('spec')!r}, erwartet {spec}")
    runde_roh = daten.get("runde", 1)
    if type(runde_roh) is not int or runde_roh < 1:
        raise Fehler(f"{pfad}: 'runde' muss eine Ganzzahl >= 1 sein, ist {runde_roh!r}")

    bereinigt = daten.get("testdaten_bereinigt", True)
    if not isinstance(bereinigt, bool):
        raise Fehler(f"{pfad}: 'testdaten_bereinigt' muss true/false sein, ist {bereinigt!r}")

    browser = _text(daten, "browser", str(pfad), pflicht=False) or BROWSER_MODUS
    stage_roh = _text(daten, "stage", str(pfad), pflicht=False)
    stage = _url_ohne_zugang(stage_roh) if stage_roh.startswith(("http://", "https://")) else stage_roh

    folgen_roh = daten.get("folgen")
    if not isinstance(folgen_roh, list) or not folgen_roh:
        raise Fehler(f"{pfad}: 'folgen' fehlt oder ist leer")

    folgen: list[dict[str, Any]] = []
    passt_nicht: list[dict[str, str]] = []
    gesamt = 0
    passt = 0
    for f_nr, f_obj in enumerate(folgen_roh, 1):
        if not isinstance(f_obj, dict):
            raise Fehler(f"Folge {f_nr}: kein Objekt, sondern {type(f_obj).__name__}")
        titel = _text(f_obj, "titel", f"Folge {f_nr}")
        t_roh = f_obj.get("ticket", "")
        if isinstance(t_roh, bool) or not isinstance(t_roh, (int, str)):
            raise Fehler(f"Folge {f_nr}: 'ticket' ist weder Zahl noch Text ({t_roh!r})")
        ticket = str(t_roh).strip().lstrip("#")
        schritte_roh = f_obj.get("schritte")
        if not isinstance(schritte_roh, list) or not schritte_roh:
            raise Fehler(f"Folge {f_nr}: 'schritte' fehlt oder ist leer")
        schritte: list[dict[str, str]] = []
        for s_nr, s_obj in enumerate(schritte_roh, 1):
            ort = f"Folge {f_nr}, Schritt {s_nr}"
            if not isinstance(s_obj, dict):
                raise Fehler(f"{ort}: kein Objekt, sondern {type(s_obj).__name__}")
            schritt_text = _text(s_obj, "schritt", ort)
            erg_roh = _text(s_obj, "ergebnis", ort).lower()
            erg = _ERGEBNIS_WERTE.get(erg_roh)
            if erg is None:
                raise Fehler(f"{ort}: 'ergebnis' muss 'passt' oder 'passt nicht' sein, ist {s_obj.get('ergebnis')!r}")
            beleg = _text(s_obj, "beleg", ort)
            hinweis = _text(s_obj, "hinweis", ort, pflicht=False)
            eintrag = {
                "schritt": schritt_text,
                "ergebnis": erg,
                "beleg": beleg,
                "hinweis": hinweis,
            }
            schritte.append(eintrag)
            gesamt += 1
            if erg == "passt":
                passt += 1
            else:
                passt_nicht.append(
                    {
                        "ticket": ticket,
                        "titel": titel,
                        "schritt": schritt_text,
                        "beleg": beleg,
                        "hinweis": hinweis,
                    }
                )
        folgen.append({"ticket": ticket, "titel": titel, "schritte": schritte})

    return {
        "spec": spec,
        "runde": runde_roh,
        "stage": stage,
        "browser": browser,
        "testdaten_bereinigt": bereinigt,
        "folgen": folgen,
        "gesamt": gesamt,
        "passt": passt,
        "passt_nicht": passt_nicht,
    }


def marker_text(ergebnis: dict[str, Any]) -> str:
    """Markdown-Bericht für ``<pfade.agenten>/selbstpruefung_<S>.md``."""
    spec = ergebnis["spec"]
    gesamt = ergebnis["gesamt"]
    passt = ergebnis["passt"]
    fehler = ergebnis["passt_nicht"]
    zeilen = [
        f"# KI-Selbstprüfung Spec #{spec}",
        "",
        f"- **Stage-App**: {ergebnis['stage'] or '(nicht genannt)'}",
        f"- **Browser**: {ergebnis['browser']} ({BROWSER_HINWEIS})",
        f"- **Ergebnis**: {passt} von {gesamt} Schritten passen — {len(fehler)} passt nicht",
        (
            "- **Testdaten**: bereinigt (keine Testdaten auf Staging zurückgelassen)"
            if ergebnis["testdaten_bereinigt"]
            else "- **Testdaten**: ⚠️ WARNUNG — laut Ergebnis nicht vollständig bereinigt"
        ),
        "",
    ]
    if fehler:
        zeilen.extend(["## Auffälligkeiten („passt nicht“)", ""])
        for punkt in fehler:
            ort = f"#{punkt['ticket']} · {punkt['titel']}" if punkt["ticket"] else punkt["titel"]
            extra = f" — {punkt['hinweis']}" if punkt["hinweis"] else ""
            zeilen.append(f"- **{ort} · Schritt „{punkt['schritt']}“**: passt nicht · Beleg: {punkt['beleg']}{extra}")
        zeilen.append("")
    zeilen.extend(["## Geprüfte Klick-Schrittfolgen", ""])
    for f in ergebnis["folgen"]:
        zeilen.append(f"### {f['titel']}")
        for nr, s in enumerate(f["schritte"], 1):
            zeichen = "✅" if s["ergebnis"] == "passt" else "❌"
            extra = f" — {s['hinweis']}" if s["hinweis"] else ""
            zeilen.append(f"{nr}. {zeichen} {s['schritt']} — **{s['ergebnis']}** · Beleg: {s['beleg']}{extra}")
        zeilen.append("")
    return "\n".join(zeilen)


def _vorher_gruen(repo: Path, spec: int) -> bool:
    """True, wenn ein vollständiger Marker vorliegt und das Ergebnis 0 „passt nicht“ hatte."""
    marker = marker_pfad(repo, spec)
    if not marker.is_file():
        return False
    erg = ergebnis_pfad(repo, spec)
    if erg.is_file():
        try:
            daten = lies_ergebnis(erg, spec)
            return len(daten["passt_nicht"]) == 0
        except Fehler:
            return False
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return False
    return "0 passt nicht" in text


def planen(
    repo: Path,
    spec: int,
    *,
    stage: str = "",
    rundschau_datei: Path | None = None,
    neu: bool = False,
    runde: int = 1,
) -> int:
    """Auftrag für die KI-Selbstprüfung schreiben (Exit 0/2/4/1)."""
    url, grund = staging_status(repo, stage)
    if grund:
        sys.stdout.write(f"{grund}\n")
        return 2

    ordner = arbeits_ordner(repo, spec)
    sperre = sperre_pfad(repo, spec)
    marker = marker_pfad(repo, spec)
    laeuft = f"Selbstprüfung Spec #{spec} läuft schon (Sperre {sperre}, jünger als {SPERRE_MIN} min)."
    if _sperre_aktiv(sperre):
        sys.stdout.write(f"{laeuft}\n")
        return 4
    if not neu and runde <= 1 and _vorher_gruen(repo, spec):
        sys.stdout.write(f"Schon erledigt: {marker}\n")
        return 4

    folgen, html_rel, auftrag_rel = _sammle_folgen(repo, spec, rundschau_datei)
    ordner.mkdir(parents=True, exist_ok=True)
    start = datetime.now().astimezone().isoformat(timespec="seconds")
    if not _sperre_anlegen(sperre, {"start": start, "spec": spec, "runde": runde}):
        sys.stdout.write(f"{laeuft}\n")
        return 4
    try:
        ergebnis_pfad(repo, spec).unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        ziel = auftrag_pfad(repo, spec)
        ziel.write_text(
            auftrag_text(
                repo,
                spec,
                url,
                folgen,
                rundschau_rel=html_rel,
                rundschau_auftrag_rel=auftrag_rel,
                runde=runde,
            ),
            encoding="utf-8",
        )
        lauf = {
            "start": start,
            "spec": spec,
            "runde": runde,
            "stage": url,
            "browser": BROWSER_MODUS,
            "rundschau_datei": html_rel,
            "folgen": len(folgen),
            "schritte": sum(len(f["schritte"]) for f in folgen),
            "auftrag": str(ziel),
            "ergebnis": str(ergebnis_pfad(repo, spec)),
        }
        sperre.write_text(json.dumps(lauf, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Selbstprüfung geplant: %s (%s Folgen)", ziel, len(folgen))
        sys.stdout.write(
            f"Selbstprüfung Spec #{spec}: {len(folgen)} Klick-Schrittfolge(n) gegen {url} ({BROWSER_MODUS})\n"
            f"Auftrag: {ziel}\n"
        )
        return 0
    except BaseException:
        sperre.unlink(missing_ok=True)
        raise


def sammeln(repo: Path, spec: int) -> int:
    """``ergebnis.json`` prüfen und Marker ``selbstpruefung_<S>.md`` schreiben (Exit 0/3/4/1)."""
    marker = marker_pfad(repo, spec)
    sperre = sperre_pfad(repo, spec)
    ergebnis = ergebnis_pfad(repo, spec)
    if marker.exists() and not sperre.exists():
        sys.stdout.write(f"Schon erledigt: {marker}\n")
        return 4
    freigeben = True
    try:
        if sperre.exists():
            try:
                lauf = json.loads(sperre.read_text(encoding="utf-8"))
            except (OSError, ValueError) as fehler:
                raise Fehler(f"{sperre} kaputt ({fehler}) — 'selbstpruefung planen {spec}' neu starten") from fehler
            if isinstance(lauf, dict) and "auftrag" not in lauf and _sperre_aktiv(sperre):
                freigeben = False
                sys.stdout.write(
                    f"Selbstprüfung Spec #{spec}: planen läuft noch (Sperre {sperre}) — später erneut sammeln.\n"
                )
                return 4
        if not ergebnis.is_file():
            sys.stdout.write(
                f"Ergebnis fehlt: {ergebnis} — Selbstprüfungs-Helfer hat nichts geschrieben; "
                f"'selbstpruefung planen {spec}' neu starten.\n"
            )
            return 3
        daten = lies_ergebnis(ergebnis, spec)
        text = marker_text(daten)
        marker.parent.mkdir(parents=True, exist_ok=True)
        zwischen = marker.with_name(marker.name + ".tmp")
        try:
            zwischen.write_text(text, encoding="utf-8")
            zwischen.replace(marker)
        finally:
            zwischen.unlink(missing_ok=True)
        log.info("Selbstprüfung gesammelt: %s", marker)
        sys.stdout.write(
            f"Ergebnis: {daten['passt']} von {daten['gesamt']} Schritten passen — "
            f"{len(daten['passt_nicht'])} passt nicht\n"
            f"Marker: {marker}\n"
        )
        return 0
    finally:
        if freigeben:
            sperre.unlink(missing_ok=True)


def pruefe_fuer_abnahme(
    repo: Path,
    spec: int,
    *,
    min_runde: int = 1,
    pflicht_tickets: Iterable[int | str] = (),
) -> tuple[list[str], dict[str, Any]]:
    """Prüfung für ``abschluss_paket.py ablegen --stand abnahme`` (#368, Runden #370).

    Rückgabe ``(maengel, paket_daten)``:
    - ``ergebnis.json`` da, gültig und passend zur Runde/zu den Folge-Tickets → ``([], daten)``
    - ``ergebnis.json`` veraltet (aus früherer Runde / neue Folge-Tickets fehlen) → ``([mangel], {})`` (Exit 4)
    - ``ergebnis.json`` da, aber ungültig → ``([mangel], {})`` (Exit 4)
    - ``ergebnis.json`` fehlt und ``staging.url`` fehlt/``staging.ohne`` → ``([], {"status": "nicht_moeglich", "grund": …})``
    - ``ergebnis.json`` fehlt und ``staging.url`` gesetzt → ``([mangel], {})`` (Exit 4)
    """
    erg = ergebnis_pfad(repo, spec)
    marker = marker_pfad(repo, spec)
    if erg.is_file():
        try:
            daten = lies_ergebnis(erg, spec)
        except Fehler as fehler:
            return [
                f"Selbstprüfung ungültig ({fehler}) — ergebnis.json prüfen und "
                f"`abschluss_paket.py selbstpruefung sammeln {spec}` ausführen."
            ], {}
        geprueft = {str(f.get("ticket") or "").strip().lstrip("#") for f in daten["folgen"]}
        pflicht = [str(t).strip().lstrip("#") for t in pflicht_tickets if str(t).strip()]
        fehlende = [t for t in pflicht if t not in geprueft]
        ist_runde = int(daten.get("runde") or 1)
        if fehlende or (min_runde > 1 and not pflicht and ist_runde < min_runde):
            detail = (
                f"Folge-Ticket(s) {', '.join(f'#{t}' for t in fehlende)} fehlen"
                if fehlende
                else f"Stand ist noch Runde {ist_runde}, erwartet Runde {min_runde}"
            )
            return [
                f"Selbstprüfung veraltet ({detail}) — für Runde {max(min_runde, 2)} erst "
                f"`abschluss_paket.py selbstpruefung planen {spec} --neu`, Helfer gegen Staging "
                f"klicken lassen und `abschluss_paket.py selbstpruefung sammeln {spec}` ausführen (#370)."
            ], {}
        status = "passt_nicht" if daten["passt_nicht"] else "ok"
        return [], {
            "status": status,
            "runde": ist_runde,
            "gesamt": daten["gesamt"],
            "passt": daten["passt"],
            "passt_nicht": daten["passt_nicht"],
            "testdaten_bereinigt": daten["testdaten_bereinigt"],
            "browser": daten["browser"],
            "datei": _rel(repo, marker) if marker.is_file() else "",
            "grund": "",
        }
    _url, grund = staging_status(repo)
    if grund:
        return [], {"status": "nicht_moeglich", "grund": grund}
    rel_erg = _rel(repo, erg)
    return [
        f"Selbstprüfung fehlt: Ergebnis-Datei `{rel_erg}` nicht gefunden — "
        f"erst `abschluss_paket.py selbstpruefung planen {spec}`, Helfer gegen Staging klicken lassen "
        f"und `abschluss_paket.py selbstpruefung sammeln {spec}` ausführen (#368)."
    ], {}


def paket_abschnitt(daten: dict[str, Any]) -> list[str]:
    """Markdown-Abschnitt für das Abschluss-Paket (leer, wenn keine Daten erhoben)."""
    if not daten or not daten.get("status"):
        return []
    status = str(daten["status"])
    zeilen = ["## KI-Selbstprüfung (Staging)", ""]
    if status == "nicht_moeglich":
        grund = str(daten.get("grund") or "Selbstprüfung nicht möglich.")
        zeilen.extend([f"- {grund}", ""])
        return zeilen

    gesamt = int(daten.get("gesamt") or 0)
    passt = int(daten.get("passt") or 0)
    fehler = list(daten.get("passt_nicht") or [])
    browser = str(daten.get("browser") or BROWSER_MODUS)
    datei = str(daten.get("datei") or "")
    meta = [f"Browser: {browser}"]
    if datei:
        meta.append(f"Datei: {datei}")
    meta_text = " · ".join(meta)

    if fehler:
        zeilen.append(
            f"- ⚠️ {passt} von {gesamt} Klick-Schritten auf Staging bestanden — "
            f"**{len(fehler)} passt nicht** ({meta_text}):"
        )
        for punkt in fehler:
            ticket = str(punkt.get("ticket") or "")
            titel = str(punkt.get("titel") or "")
            ort = f"#{ticket} · {titel}" if ticket and f"#{ticket}" not in titel else (titel or f"#{ticket}")
            schritt = str(punkt.get("schritt") or "")
            beleg = str(punkt.get("beleg") or "")
            hinweis = str(punkt.get("hinweis") or "")
            extra = f" — {hinweis}" if hinweis else ""
            zeilen.append(f"  - **{ort} · Schritt „{schritt}“**: passt nicht · Beleg: `{beleg}`{extra}")
    else:
        zeilen.append(f"- ✅ {passt} von {gesamt} Klick-Schritten auf Staging bestanden (0 passt nicht · {meta_text}).")
    if daten.get("testdaten_bereinigt") is False:
        zeilen.append("- ⚠️ Testdaten laut Ergebnis nicht vollständig bereinigt — Staging prüfen!")
    zeilen.append("")
    return zeilen
