"""Was eine gültige Abnahme-Rundschau ist — die eine Definition (#529).

Erzeuger (:func:`erzeuge`) und Prüfer (:func:`pruefe`) leben hier und teilen sich die
Konstanten; Vorlagen, Tickets und ``SKILL.md`` verweisen nur auf dieses Modul.

Eine Rundschau ist ein HTML-Deck (``<meta name="rundschau-deck">``, Folien
``<section class="folie" data-teil="1|2|3">``) in genau dieser Reihenfolge:

1. Teil 1 — Präsentation für Nutzer: je Folie Überschrift, ein Kernsatz, ein eingebettetes
   Bild, höchstens :data:`MAX_WOERTER_TEIL1` Wörter.
2. Teil 2 — der gewohnte Rest (Früher/Jetzt, Belege); jede Inhaltsfolie hat ein Bild;
   „Offenes“ ist eine eigene Folie (``data-art="offenes"``, darf ohne Bild sein).
3. Schluss „Entscheidungen“: Zeitstrahl als Inline-SVG; je Entscheidung Ticket, Zeitpunkt
   und, wenn vorhanden, „überholt durch #N“. Ohne Entscheidungen eine Leer-Folie.

Entscheidungs-Daten: Einträge von ``briefkasten.liste`` (Schlüssel ``ticket``, ``ts``,
``was``, optional ``ueberholt_durch``). Kein externer Dienst, keine CDN-Abhängigkeit.
"""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

#: Teil 1 ist eine Präsentation, keine Textwüste: so viele Wörter je Folie höchstens.
MAX_WOERTER_TEIL1 = 40
#: Kleinste Größe eines echten Bildes in Bytes (leere/Platzhalter-Bilder fallen durch).
MIN_BILD_BYTES = 60
SKILL_FEHLT = (
    "Skill `rundschau` fehlt auf dieser Maschine (erwartet: ~/.claude/skills/rundschau/SKILL.md). "
    "Kein Ersatz-Deck: Skill installieren, dann erneut erzeugen."
)
_MAGIE = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"<svg", b"<?xml")
_DATA_IMG = re.compile(r"^data:image/[a-z+.-]+;base64,([A-Za-z0-9+/=\s]*)$")


def skill_da() -> bool:
    """Ist der Skill ``rundschau`` da? (``TO_SPAWN_RUNDSCHAU_SKILL`` überschreibt den Pfad, für Tests.)"""
    pfad = os.environ.get("TO_SPAWN_RUNDSCHAU_SKILL") or str(
        Path.home() / ".claude/skills/rundschau/SKILL.md"
    )
    return Path(pfad).is_file()


def zeit_text(ts: str) -> str:
    """``24.11. 12:46`` aus ISO-Zeit; unlesbar → Rohtext (leer bleibt leer)."""
    try:
        return f"{datetime.fromisoformat(ts):%d.%m. %H:%M}"
    except ValueError:
        return ts


def ueberholt_map(eintraege: list[dict[str, str]]) -> dict[int, str]:
    """Index → Ticket, das die Entscheidung überholt: Feld ``ueberholt_durch`` oder spätere gleiche Frage."""
    reihe = sorted(range(len(eintraege)), key=lambda i: eintraege[i].get("ts", ""))
    frage = lambda i: eintraege[i].get("was", "").split(" → ", 1)[0]
    aus: dict[int, str] = {}
    for pos, i in enumerate(reihe):
        explizit = str(eintraege[i].get("ueberholt_durch") or "").strip().lstrip("#")
        if explizit:
            aus[i] = explizit
            continue
        for j in reihe[pos + 1 :]:
            if frage(j) and frage(j) == frage(i):
                aus[i] = eintraege[j].get("ticket", "")
                break
    return aus


# --- Erzeuger ---------------------------------------------------------------


def _bild_uri(quelle: str) -> str:
    daten = Path(quelle).read_bytes()
    if len(daten) < MIN_BILD_BYTES or not daten.startswith(_MAGIE):
        raise ValueError(f"Bild {quelle} ist leer oder keine Bilddatei")
    art = {
        b"\x89PNG": "png",
        b"\xff\xd8\xff": "jpeg",
        b"GIF8": "gif",
        b"RIFF": "webp",
    }.get(daten[:4], "svg+xml")
    return f"data:image/{art};base64,{base64.b64encode(daten).decode()}"


def _folie(teil: int, f: dict[str, Any], *, bild_pflicht: bool) -> str:
    art = f' data-art="{html.escape(f["art"])}"' if f.get("art") else ""
    if bild_pflicht and not f.get("bild"):
        raise ValueError(
            f"Folie „{f.get('titel', '?')}“ braucht ein Bild (Teil {teil})"
        )
    bild = (
        f'<figure><img alt="{html.escape(f["titel"])}" src="{_bild_uri(f["bild"])}"></figure>'
        if f.get("bild")
        else ""
    )
    return (
        f'<section class="folie" data-teil="{teil}"{art}><h2>{html.escape(f["titel"])}</h2>'
        f'<p class="kern">{html.escape(f["kern"])}</p>{bild}</section>'
    )


def _grafik(eintraege: list[dict[str, str]]) -> str:
    if not eintraege:
        return '<p class="leer">Keine Entscheidungen in diesem Bau-Verlauf.</p>'
    reihe = sorted(range(len(eintraege)), key=lambda i: eintraege[i].get("ts", ""))
    ue = ueberholt_map(eintraege)
    schritt, breite = 170, max(640, 170 * len(reihe) + 60)
    x = {i: 60 + n * schritt for n, i in enumerate(reihe)}
    teile = [
        f'<svg class="zeitstrahl" role="img" aria-label="Entscheidungen im Bau-Verlauf" width="{breite}" height="300" viewBox="0 0 {breite} 300">'
    ]
    teile.append(f'<line x1="20" y1="150" x2="{breite - 20}" y2="150" class="achse"/>')
    for n, i in enumerate(reihe):
        e, oben = eintraege[i], n % 2 == 0
        y_text = 60 if oben else 215
        nr = html.escape(e.get("ticket", ""))
        ts = html.escape(e.get("ts", ""))
        durch = " ueberholt" if i in ue else ""
        zeilen = [f"#{nr} · {zeit_text(e.get('ts', ''))}", e.get("was", "")[:44]]
        if i in ue:
            zeilen.append(f"überholt durch #{ue[i]}")
        tspans = "".join(
            f'<tspan x="{x[i]}" dy="{16 if k else 0}">{html.escape(z)}</tspan>'
            for k, z in enumerate(zeilen)
        )
        teile.append(
            f'<g class="entscheidung{durch}" data-ticket="{nr}" data-ts="{ts}"><title>{html.escape(e.get("was", ""))} — {html.escape(e.get("warum", ""))}</title>'
            f'<line x1="{x[i]}" y1="150" x2="{x[i]}" y2="{y_text + 36 if oben else y_text - 14}" class="stiel"/>'
            f'<circle cx="{x[i]}" cy="150" r="7" class="punkt"/><text x="{x[i]}" y="{y_text}" text-anchor="middle">{tspans}</text></g>'
        )
    for i, ziel in ue.items():
        j = next(
            (
                k
                for k in reihe
                if eintraege[k].get("ticket") == ziel and k != i and x[k] > x[i]
            ),
            None,
        )
        if j is not None:
            teile.append(
                f'<path d="M{x[i]} 150 Q{(x[i] + x[j]) // 2} 100 {x[j]} 150" class="ueberholt-pfeil"/>'
            )
    return "".join(teile) + "</svg>"


_CSS = """
:root{color-scheme:light dark;--bg:#fafaf7;--fg:#1c1c1e;--ak:#2f6fed;--rot:#d64545}
@media(prefers-color-scheme:dark){:root{--bg:#141417;--fg:#f2f2f4;--ak:#7aa2ff;--rot:#ff7a7a}}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.5 system-ui,sans-serif}
main{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;height:100vh}
.folie{flex:0 0 100%;box-sizing:border-box;scroll-snap-align:start;padding:6vh 8vw;overflow-y:auto}
.folie h2{font-size:clamp(1.8rem,5vw,3rem);margin:0 0 .4em;letter-spacing:-.02em}
.kern{font-size:clamp(1.1rem,2.4vw,1.5rem);max-width:34ch}
figure{margin:1.2em 0}img{max-width:100%;max-height:50vh;border-radius:14px}
.zeitstrahl{font-size:13px;fill:var(--fg)}.achse{stroke:var(--ak);stroke-width:3}.stiel{stroke:var(--fg);opacity:.3}
.punkt{fill:var(--ak)}.ueberholt .punkt{fill:none;stroke:var(--rot);stroke-width:2;stroke-dasharray:3}
.ueberholt text{fill:var(--rot)}.ueberholt-pfeil{fill:none;stroke:var(--rot);stroke-dasharray:4}
.leer{opacity:.7}
"""

_JS = "addEventListener('keydown',e=>{const m=document.querySelector('main');if(e.key=='ArrowRight')m.scrollBy(m.clientWidth,0);if(e.key=='ArrowLeft')m.scrollBy(-m.clientWidth,0)})"


def erzeuge(texte: dict[str, Any], eintraege: list[dict[str, str]]) -> str:
    """Fertiges Deck aus Texten (``titel``, ``teil1``, ``teil2``: Listen von ``titel``/``kern``/``bild``/``art``) + Entscheidungen."""
    teil1 = [_folie(1, f, bild_pflicht=True) for f in texte["teil1"]]
    teil2 = [
        _folie(2, f, bild_pflicht=f.get("art") != "offenes") for f in texte["teil2"]
    ]
    schluss = f'<section class="folie" data-teil="3" data-art="entscheidungen"><h2>Entscheidungen</h2>{_grafik(eintraege)}</section>'
    titel = html.escape(texte.get("titel", "Rundschau"))
    return (
        f'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="rundschau-deck" content="1"><title>{titel}</title><style>{_CSS}</style></head>'
        f"<body><main>{''.join(teil1 + teil2)}{schluss}</main><script>{_JS}</script></body></html>"
    )


# --- Prüfer -----------------------------------------------------------------


class _Leser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.deck = False
        self.folien: list[dict[str, Any]] = []
        self._aus = 0  # in script/style
        self._grp: dict[str, Any] | None = None
        self._svg = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("name") == "rundschau-deck":
            self.deck = True
        if tag in ("script", "style"):
            self._aus += 1
        if tag == "section" and "folie" in a.get("class", "").split():
            self.folien.append(
                {
                    "teil": a.get("data-teil", ""),
                    "art": a.get("data-art", ""),
                    "text": [],
                    "bilder": [],
                    "svg_viz": 0,
                    "kern": 0,
                    "h2": 0,
                    "gruppen": [],
                }
            )
        if not self.folien:
            return
        f = self.folien[-1]
        if tag == "img":
            f["bilder"].append(a.get("src", ""))
        if tag == "h2":
            f["h2"] += 1
        if tag == "p" and "kern" in a.get("class", "").split():
            f["kern"] += 1
        if tag == "svg":
            self._svg += 1
            if "viz" in a.get("class", "").split():
                f["svg_viz"] += 1
        if tag == "g" and "entscheidung" in a.get("class", "").split():
            self._grp = {
                "ticket": a.get("data-ticket", ""),
                "ts": a.get("data-ts", ""),
                "text": [],
            }
            f["gruppen"].append(self._grp)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._aus -= 1
        if tag == "svg" and self._svg:
            self._svg -= 1
        if tag == "g":
            self._grp = None

    def handle_data(self, data: str) -> None:
        if self._aus or not self.folien:
            return
        if self._grp is not None:
            self._grp["text"].append(data)
        if not self._svg:
            self.folien[-1]["text"].append(data)


def _bild_echt(src: str) -> bool:
    m = _DATA_IMG.match(src.strip())
    if not m:
        return False
    try:
        daten = base64.b64decode(re.sub(r"\s", "", m.group(1)), validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(daten) >= MIN_BILD_BYTES and daten.startswith(_MAGIE)


def pruefe(text: str, eintraege: list[dict[str, str]]) -> list[str]:
    """Mängel der Rundschau (leer = gültig). Jede Meldung sagt, was fehlt."""
    leser = _Leser()
    leser.feed(text)
    if not leser.deck or not leser.folien:
        return [
            "Kein Deck: Marker `rundschau-deck` und Folien `section.folie` fehlen (Markdown/einfache Seite genügt nicht)."
        ]
    maengel: list[str] = []
    teile = [f["teil"] for f in leser.folien]
    for n in "123":
        if n not in teile:
            maengel.append(f'Teil {n} fehlt (keine Folie mit data-teil="{n}").')
    if not maengel and teile != sorted(teile):
        maengel.append(
            "Reihenfolge falsch: erwartet Teil 1, dann Teil 2, am Schluss Teil 3 (Entscheidungen)."
        )
    for f in leser.folien:
        if f["teil"] not in ("1", "2"):
            continue
        name = " ".join("".join(f["text"]).split())[:30]
        if not f["h2"]:
            maengel.append(f"Folie „{name}“ (Teil {f['teil']}) ohne Überschrift.")
        if f["art"] != "offenes" and not (
            any(_bild_echt(s) for s in f["bilder"]) or f["svg_viz"]
        ):
            maengel.append(
                f"Folie „{name}“ (Teil {f['teil']}): Bild fehlt oder ist leer/keine echte Bilddatei."
            )
        if f["teil"] == "1":
            woerter = len(" ".join(f["text"]).split())
            if woerter > MAX_WOERTER_TEIL1 or f["kern"] != 1:
                maengel.append(
                    f"Folie „{name}“ (Teil 1): {woerter} Wörter (max. {MAX_WOERTER_TEIL1}) und {f['kern']} Kernsätze (genau 1)."
                )
    schluss = [f for f in leser.folien if f["teil"] == "3"]
    if schluss:
        maengel += _pruefe_grafik(schluss[-1], eintraege)
    return maengel


def _pruefe_grafik(folie: dict[str, Any], eintraege: list[dict[str, str]]) -> list[str]:
    if not eintraege:
        return (
            []
            if "Keine Entscheidungen" in "".join(folie["text"])
            else ["Teil 3: Leer-Folie „Keine Entscheidungen“ fehlt."]
        )
    maengel: list[str] = []
    sollen = ueberholt_map(eintraege)
    gruppen = folie["gruppen"]
    if not gruppen:
        return [
            "Teil 3: keine Entscheidungs-Grafik (SVG mit `g.entscheidung`) gefunden."
        ]
    gefunden = Counter((g["ticket"], g["ts"]) for g in gruppen)
    for i, e in enumerate(eintraege):
        t, ts = e.get("ticket", ""), e.get("ts", "")
        if not ts:
            maengel.append(f"Entscheidung #{t}: Daten haben keinen Zeitpunkt.")
            continue
        if gefunden[(t, ts)] <= 0:
            maengel.append(
                f"Entscheidung #{t} ({ts}) fehlt in der Grafik oder hat dort kein Ticket/keinen Zeitpunkt."
            )
            continue
        gefunden[(t, ts)] -= 1
        g = next(g for g in gruppen if (g["ticket"], g["ts"]) == (t, ts))
        gtext = "".join(g["text"])
        if f"#{t}" not in gtext or zeit_text(ts) not in gtext:
            maengel.append(
                f"Entscheidung #{t}: Beschriftung ohne Ticket oder Zeitpunkt."
            )
        if i in sollen and f"überholt durch #{sollen[i]}" not in "".join(
            "".join(x["text"]) for x in gruppen
        ):
            maengel.append(f"Entscheidung #{t}: „überholt durch #{sollen[i]}“ fehlt.")
    return maengel
