"""Was eine gültige Abnahme-Rundschau ist — die eine Definition (#529).

Erzeuger (:func:`erzeuge`) und Prüfer (:func:`pruefe`) leben hier und teilen sich die
Konstanten; Vorlagen, Tickets und ``SKILL.md`` verweisen nur auf dieses Modul.

Eine Rundschau ist ein HTML-Deck (``<meta name="rundschau-deck">``, Folien
``<section class="folie" data-teil="1|2|3">``) in genau dieser Reihenfolge:

1. Teil 1 — Präsentation für Nutzer: Titel-Folie, dann je Folie Rubrik, Überschrift, ein
   Kernsatz, ein eingebettetes Bild (mindestens :data:`MIN_BILD_BREITE` ×
   :data:`MIN_BILD_HOEHE` px), höchstens :data:`MAX_WOERTER_TEIL1` Wörter (alles Sichtbare).
2. Teil 2 — der gewohnte Rest (Früher/Jetzt, Belege); jede Inhaltsfolie hat ein Bild;
   „Offenes“ ist Pflicht und eine eigene Folie (``data-art="offenes"``, darf ohne Bild sein).
3. Schluss „Entscheidungen“: senkrechter Zeitstrahl als Inline-SVG (``svg.zeitstrahl``),
   nach Ticket gruppiert; je Entscheidung Ticket, Zeitpunkt, Frage, Wahl, Grund und, wenn
   vorhanden, „überholt durch #N“. Ohne Entscheidungen eine Leer-Folie.

Entscheidungs-Daten: Einträge von ``briefkasten.liste`` (Schlüssel ``ticket``, ``ts``,
``was``, ``warum``, optional ``ueberholt_durch``). Kein externer Dienst, keine CDN-Abhängigkeit.
"""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
import struct
import textwrap
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

#: Teil 1 ist eine Präsentation, keine Textwüste: so viele Wörter je Folie höchstens.
MAX_WOERTER_TEIL1 = 40
#: Kleinstes Bildmaß in Pixeln (aus dem Bildkopf gelesen); kleiner = Platzhalter, fällt durch.
MIN_BILD_BREITE, MIN_BILD_HOEHE = 200, 120
#: Zeichen je Zeile im Zeitstrahl (Umbruch statt Kappen).
UMBRUCH = 32
TEIL_NAMEN = {
    "1": "Teil 1 · Neu für dich",
    "2": "Teil 2 · Details und Belege",
    "3": "Schluss · Entscheidungen",
}
SKILL_FEHLT = (
    "Skill `rundschau` fehlt auf dieser Maschine (erwartet: ~/.claude/skills/rundschau/SKILL.md). "
    "Kein Ersatz-Deck: Skill installieren, dann erneut erzeugen."
)
_DATA_IMG = re.compile(r"^data:image/[a-z+.-]+;base64,([A-Za-z0-9+/=\s]*)$")
_FORM = re.compile(rb"<(rect|circle|ellipse|path|polygon|polyline|line|text|image)\b")
_FORMEN = {
    "rect",
    "circle",
    "ellipse",
    "path",
    "polygon",
    "polyline",
    "line",
    "text",
    "image",
}
_VOID = {"meta", "img", "br", "hr", "input", "link"}


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


def _frage_wahl(was: str) -> tuple[str, str]:
    frage, _, wahl = was.partition(" → ")
    return frage.strip(), wahl.strip()


def ueberholt_map(eintraege: list[dict[str, str]]) -> dict[int, str]:
    """Index → Ticket, das die Entscheidung überholt: Feld ``ueberholt_durch`` oder spätere gleiche Frage."""
    reihe = sorted(range(len(eintraege)), key=lambda i: eintraege[i].get("ts", ""))
    frage = lambda i: _frage_wahl(eintraege[i].get("was", ""))[0]
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


# --- Bild-Prüfung (eine Stelle) ---------------------------------------------


def bild_masse(d: bytes) -> tuple[int, int] | None:
    """Breite × Höhe aus dem Bildkopf (PNG, JPEG, GIF, WebP, SVG); ``None`` = keine lesbare Bilddatei."""
    try:
        if d.startswith(b"\x89PNG\r\n\x1a\n") and len(d) >= 24:
            return struct.unpack(">II", d[16:24])
        if d[:4] == b"GIF8" and len(d) >= 10:
            return struct.unpack("<HH", d[6:10])
        if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
            art = d[12:16]
            if art == b"VP8 ":
                return struct.unpack("<HH", d[26:30])[0] & 0x3FFF, struct.unpack(
                    "<HH", d[26:30]
                )[1] & 0x3FFF
            if art == b"VP8L":
                b = struct.unpack("<I", d[21:25])[0]
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            if art == b"VP8X":
                return int.from_bytes(d[24:27], "little") + 1, int.from_bytes(
                    d[27:30], "little"
                ) + 1
        if d.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(d):
                if d[i] != 0xFF:
                    return None
                m, laenge = d[i + 1], struct.unpack(">H", d[i + 2 : i + 4])[0]
                if m in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                ):
                    h, b = struct.unpack(">HH", d[i + 5 : i + 9])
                    return b, h
                i += 2 + laenge
        kopf = d.lstrip()[:2000]
        if kopf.startswith((b"<svg", b"<?xml")) and _FORM.search(d):
            w = re.search(rb'\bwidth="(\d+(?:\.\d+)?)', kopf)
            h = re.search(rb'\bheight="(\d+(?:\.\d+)?)', kopf)
            if w and h:
                return int(float(w.group(1))), int(float(h.group(1)))
            vb = re.search(
                rb'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+(\d+(?:\.\d+)?)[ ,]+(\d+(?:\.\d+)?)"',
                kopf,
            )
            if vb:
                return int(float(vb.group(1))), int(float(vb.group(2)))
    except (struct.error, IndexError):
        return None
    return None


def bild_echt(d: bytes) -> bool:
    """Echte Bilddatei in Mindestgröße (:data:`MIN_BILD_BREITE` × :data:`MIN_BILD_HOEHE`)."""
    m = bild_masse(d)
    return m is not None and m[0] >= MIN_BILD_BREITE and m[1] >= MIN_BILD_HOEHE


def _uri_echt(src: str) -> bool:
    m = _DATA_IMG.match(src.strip())
    if not m:
        return False
    try:
        return bild_echt(base64.b64decode(re.sub(r"\s", "", m.group(1)), validate=True))
    except (binascii.Error, ValueError):
        return False


# --- Erzeuger ---------------------------------------------------------------


def _bild_uri(quelle: str) -> str:
    daten = Path(quelle).read_bytes()
    if not bild_echt(daten):
        raise ValueError(
            f"Bild {quelle} ist leer, keine Bilddatei oder kleiner als {MIN_BILD_BREITE}×{MIN_BILD_HOEHE} px"
        )
    art = {
        b"\x89PNG": "png",
        b"\xff\xd8\xff": "jpeg",
        b"GIF8": "gif",
        b"RIFF": "webp",
    }.get(daten[:4], "svg+xml")
    return f"data:image/{art};base64,{base64.b64encode(daten).decode()}"


def _folie(teil: int, f: dict[str, Any], *, bild_pflicht: bool) -> str:
    if bild_pflicht and not f.get("bild"):
        raise ValueError(
            f"Folie „{f.get('titel', '?')}“ braucht ein Bild (Teil {teil})"
        )
    art = f' data-art="{html.escape(f["art"])}"' if f.get("art") else ""
    rubrik = f.get("rubrik") or (
        "Neu" if teil == 1 else "Offen" if f.get("art") == "offenes" else "Details"
    )
    bild = (
        f'<figure><img alt="{html.escape(f["titel"])}" src="{_bild_uri(f["bild"])}"></figure>'
        if f.get("bild")
        else ""
    )
    punkte = "".join(f"<li>{html.escape(p)}</li>" for p in f.get("punkte", [])[:3])
    liste = f'<ul class="punkte">{punkte}</ul>' if punkte else ""
    return (
        f'<section class="folie" data-teil="{teil}"{art} data-label="{TEIL_NAMEN[str(teil)]}">'
        f'<span class="rubrik">{html.escape(rubrik)}</span><h2>{html.escape(f["titel"])}</h2>'
        f'<p class="kern">{html.escape(f["kern"])}</p>{bild}{liste}</section>'
    )


def _umbruch(text: str) -> list[str]:
    return textwrap.wrap(
        text, UMBRUCH, break_long_words=False, break_on_hyphens=False
    ) or [""]


def _grafik(eintraege: list[dict[str, str]]) -> str:
    """Senkrechter Zeitstrahl, je Ticket eine Spur; Text bricht um, nichts wird gekappt."""
    if not eintraege:
        return '<p class="leer">Keine Entscheidungen in diesem Bau-Verlauf.</p>'
    ue = ueberholt_map(eintraege)
    spuren: dict[str, list[int]] = {}
    for i in sorted(range(len(eintraege)), key=lambda i: eintraege[i].get("ts", "")):
        spuren.setdefault(eintraege[i].get("ticket", ""), []).append(i)
    teile: list[str] = []
    y = 8
    for ticket, idxs in spuren.items():
        teile.append(
            f'<rect x="2" y="{y}" width="132" height="30" rx="15" class="chip"/>'
            f'<text x="16" y="{y + 20}" class="spur">Ticket #{html.escape(ticket)}</text>'
        )
        y += 42
        start = y
        for i in idxs:
            e = eintraege[i]
            frage, wahl = _frage_wahl(e.get("was", ""))
            alt = " ueberholt" if i in ue else ""
            zeilen = [(f"#{ticket} · {zeit_text(e.get('ts', ''))}", "zeit")]
            zeilen += [(z, "frage") for z in _umbruch(frage)]
            zeilen += [(z, "wahl") for z in _umbruch("→ " + wahl)] if wahl else []
            zeilen += (
                [(z, "warum") for z in _umbruch("Warum: " + e["warum"])]
                if e.get("warum")
                else []
            )
            if i in ue:
                zeilen.append((f"überholt durch #{ue[i]}", "marke"))
            texte = "".join(
                f'<text x="46" y="{y + 17 + 22 * k}" class="{c}">{html.escape(z)}</text>'
                for k, (z, c) in enumerate(zeilen)
            )
            teile.append(
                f'<g class="entscheidung{alt}" data-ticket="{html.escape(ticket)}" data-ts="{html.escape(e.get("ts", ""))}">'
                f'<circle cx="22" cy="{y + 12}" r="7" class="punkt"/>{texte}</g>'
            )
            y += 22 * len(zeilen) + 18
        teile.insert(
            len(teile) - len(idxs),
            f'<line x1="22" y1="{start}" x2="22" y2="{y - 10}" class="achse"/>',
        )
        y += 10
    return (
        f'<svg class="zeitstrahl" role="img" aria-label="Entscheidungen im Bau-Verlauf" '
        f'viewBox="0 0 360 {y}" width="360" height="{y}">{"".join(teile)}</svg>'
    )


_CSS = """
:root{color-scheme:light dark;--bg:#f6f7fb;--karte:#fff;--fg:#14161c;--leise:#5b6272;--ak:#3b5bdb;--ak-hell:#e8edff;--rot:#c92a2a;--schatten:0 18px 50px rgba(20,30,70,.16)}
@media(prefers-color-scheme:dark){:root{--bg:#0f1117;--karte:#1a1d27;--fg:#eef0f6;--leise:#a3a9bb;--ak:#8ea2ff;--ak-hell:#232a45;--rot:#ff8787;--schatten:0 18px 50px rgba(0,0,0,.5)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.leiste{position:fixed;top:0;left:0;right:0;z-index:2;display:flex;justify-content:space-between;padding:14px 20px;font-size:.9rem;color:var(--leise)}
.fortschritt{position:fixed;top:0;left:0;right:0;height:4px;z-index:3;background:var(--ak-hell)}
.fortschritt i{display:block;height:100%;background:var(--ak);transition:width .3s}
main{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;height:100vh;height:100dvh;scrollbar-width:none}
.folie{flex:0 0 100%;scroll-snap-align:start;display:flex;flex-direction:column;align-items:center;text-align:center;padding:72px 8vw 104px;overflow-y:auto}
.folie>:first-child{margin-top:auto}.folie>:last-child{margin-bottom:auto}
.rubrik{display:inline-block;background:var(--ak-hell);color:var(--ak);font-weight:600;font-size:.85rem;letter-spacing:.08em;text-transform:uppercase;padding:.3em .9em;border-radius:99px}
.folie h2{font-size:clamp(1.9rem,5.5vw,3.4rem);line-height:1.12;margin:.5em 0 .3em;letter-spacing:-.025em}
.kern{font-size:clamp(1.1rem,2.4vw,1.5rem);max-width:30em;color:var(--leise);margin:0}
figure{margin:1.6em 0 0;padding:10px;background:var(--karte);border-radius:22px;box-shadow:var(--schatten);max-width:min(100%,720px)}
img{display:block;max-width:100%;max-height:44vh;border-radius:14px}
.punkte{list-style:none;display:flex;flex-wrap:wrap;gap:10px;justify-content:center;padding:0;margin:1.4em 0 0}
.punkte li{background:var(--karte);box-shadow:0 2px 10px rgba(20,30,70,.1);border-radius:99px;padding:.4em 1em;font-size:1rem}
[data-art=offenes] .rubrik{background:transparent;border:2px solid var(--rot);color:var(--rot)}
[data-teil="3"]>:first-child{margin-top:0}
.zeitstrahl{flex:none;width:100%;max-width:480px;height:auto;font-size:16.5px;fill:var(--fg)}
.chip{fill:var(--ak-hell)}.spur{font-weight:700;fill:var(--ak)}.achse{stroke:var(--ak);stroke-width:3}.punkt{fill:var(--ak)}
.zeit{fill:var(--leise);font-size:14px}.frage{font-weight:600}.warum{fill:var(--leise)}
.ueberholt .punkt{fill:none;stroke:var(--rot);stroke-width:2}
.ueberholt .frage,.ueberholt .wahl{text-decoration:line-through;fill:var(--leise)}.marke{fill:var(--rot);font-weight:600}
.leer{opacity:.7}
.bedienung{position:fixed;bottom:18px;left:0;right:0;z-index:2;display:flex;justify-content:center;gap:16px}
.bedienung button{min-width:56px;min-height:48px;border:0;border-radius:99px;background:var(--ak);color:#fff;font-size:1.6rem;cursor:pointer;box-shadow:var(--schatten)}
@media(max-width:600px){.folie{padding:64px 20px 100px}.kern{font-size:1.1rem}.bedienung button{min-width:64px;min-height:52px}}
"""

_JS = (
    "const m=document.querySelector('main'),f=[...m.children],z=document.getElementById('zaehler'),"
    "t=document.getElementById('teil'),b=document.getElementById('bar');"
    "const ist=()=>Math.min(f.length-1,Math.round(m.scrollLeft/m.clientWidth));"
    "const zeige=()=>{const i=ist();z.textContent=(i+1)+' / '+f.length;t.textContent=f[i].dataset.label;"
    "b.style.width=((i+1)/f.length*100)+'%'};"
    "const geh=d=>m.scrollTo({left:(ist()+d)*m.clientWidth,behavior:'smooth'});"
    "m.addEventListener('scroll',zeige,{passive:true});"
    "document.getElementById('weiter').onclick=()=>geh(1);document.getElementById('zurueck').onclick=()=>geh(-1);"
    "addEventListener('keydown',e=>{if(e.key=='ArrowRight')geh(1);if(e.key=='ArrowLeft')geh(-1)})"
)


def erzeuge(texte: dict[str, Any], eintraege: list[dict[str, str]]) -> str:
    """Fertiges Deck aus Texten + Entscheidungen.

    ``texte``: ``titel``, optional ``datum``; ``teil1``/``teil2``: Listen von Folien mit ``titel``,
    ``kern``, ``bild`` (Dateipfad), optional ``rubrik``, ``punkte`` (bis 3), ``art`` (``offenes``).
    """
    if not any(f.get("art") == "offenes" for f in texte["teil2"]):
        raise ValueError("Teil 2 braucht eine Folie „Offenes“ (art: offenes)")
    titel = texte.get("titel", "Rundschau")
    datum = texte.get("datum") or f"{date.today():%d.%m.%Y}"
    kopf = (
        f'<section class="folie" data-teil="1" data-art="titel" data-label="{TEIL_NAMEN["1"]}">'
        f'<span class="rubrik">Rundschau</span><h2>{html.escape(titel)}</h2><p class="kern">{html.escape(datum)}</p></section>'
    )
    teil1 = [_folie(1, f, bild_pflicht=True) for f in texte["teil1"]]
    teil2 = [
        _folie(2, f, bild_pflicht=f.get("art") != "offenes") for f in texte["teil2"]
    ]
    schluss = (
        f'<section class="folie" data-teil="3" data-art="entscheidungen" data-label="{TEIL_NAMEN["3"]}">'
        f'<span class="rubrik">Bau-Verlauf</span><h2>Entscheidungen</h2>{_grafik(eintraege)}</section>'
    )
    folien = [kopf, *teil1, *teil2, schluss]
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="rundschau-deck" content="1"><title>{html.escape(titel)}</title><style>{_CSS}</style></head><body>'
        f'<header class="leiste"><span id="teil">{TEIL_NAMEN["1"]}</span><span id="zaehler">1 / {len(folien)}</span></header>'
        f'<div class="fortschritt"><i id="bar" style="width:{100 / len(folien):.1f}%"></i></div>'
        f"<main>{''.join(folien)}</main>"
        '<nav class="bedienung"><button id="zurueck" aria-label="Zurück">‹</button>'
        '<button id="weiter" aria-label="Weiter">›</button></nav>'
        f"<script>{_JS}</script></body></html>"
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
        self._zs = False
        self._viz: dict[str, int] | None = None
        self._stapel: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("name") == "rundschau-deck":
            self.deck = True
        if tag in ("script", "style"):
            self._aus += 1
        if tag not in _VOID:
            versteckt = (
                "hidden" in a
                or "display:none" in a.get("style", "").replace(" ", "").lower()
            )
            self._stapel.append((tag, versteckt))
        if tag == "section" and "folie" in a.get("class", "").split():
            self.folien.append(
                {
                    "teil": a.get("data-teil", ""),
                    "art": a.get("data-art", ""),
                    "text": [],
                    "bilder": [],
                    "viz": [],
                    "kern": 0,
                    "h2": 0,
                    "gruppen": [],
                }
            )
        if not self.folien:
            return
        f = self.folien[-1]
        klassen = a.get("class", "").split()
        if tag == "img":
            f["bilder"].append(a.get("src", ""))
        if tag == "h2":
            f["h2"] += 1
        if tag == "p" and "kern" in klassen:
            f["kern"] += 1
        if tag == "svg":
            self._svg += 1
            if "zeitstrahl" in klassen:
                self._zs = True
            if "viz" in klassen:
                self._viz = {"formen": 0}
                f["viz"].append(self._viz)
        elif self._viz is not None and tag in _FORMEN:
            self._viz["formen"] += 1
        if (
            tag == "g"
            and "entscheidung" in klassen
            and self._zs
            and not any(h for _, h in self._stapel)
        ):
            self._grp = {
                "ticket": a.get("data-ticket", ""),
                "ts": a.get("data-ts", ""),
                "text": [],
            }
            f["gruppen"].append(self._grp)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._aus -= 1
        if tag in _VOID:
            return
        for k in range(len(self._stapel) - 1, -1, -1):
            if self._stapel[k][0] == tag:
                del self._stapel[k:]
                break
        if tag == "svg" and self._svg:
            self._svg -= 1
            if not self._svg:
                self._zs = False
                self._viz = None
        if tag == "g":
            self._grp = None

    def handle_data(self, data: str) -> None:
        if self._aus or not self.folien:
            return
        if self._grp is not None:
            self._grp["text"].append(data)
        if self._viz is not None and data.strip():
            self._viz["formen"] += 1
        self.folien[-1]["text"].append(data)


def _norm(s: str) -> str:
    return " ".join(s.split())


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
    if "1" in teile and all(
        f["art"] == "titel" for f in leser.folien if f["teil"] == "1"
    ):
        maengel.append(
            "Teil 1 hat nur die Titel-Folie: mindestens eine Neu-Folie fehlt."
        )
    if "2" in teile and not any(
        f["art"] == "offenes" for f in leser.folien if f["teil"] == "2"
    ):
        maengel.append('Teil 2: Folie „Offenes“ fehlt (data-art="offenes").')
    for f in leser.folien:
        if f["teil"] not in ("1", "2"):
            continue
        name = _norm(" ".join(f["text"]))[:30]
        if not f["h2"]:
            maengel.append(f"Folie „{name}“ (Teil {f['teil']}) ohne Überschrift.")
        if f["art"] not in ("offenes", "titel"):
            bild = any(_uri_echt(s) for s in f["bilder"]) or any(
                v["formen"] for v in f["viz"]
            )
            if not bild:
                maengel.append(
                    f"Folie „{name}“ (Teil {f['teil']}): Bild fehlt, ist leer oder kleiner als "
                    f"{MIN_BILD_BREITE}×{MIN_BILD_HOEHE} px (oder svg.viz ohne Formen/Text)."
                )
        if f["teil"] == "1" and f["art"] != "titel":
            woerter = len(" ".join(f["text"]).split())
            if woerter > MAX_WOERTER_TEIL1 or f["kern"] != 1:
                maengel.append(
                    f"Folie „{name}“ (Teil 1): {woerter} Wörter (max. {MAX_WOERTER_TEIL1}) und {f['kern']} Kernsätze (genau 1)."
                )
    schluss = [f for f in leser.folien if f["teil"] == "3"]
    if schluss:
        maengel += _pruefe_grafik(schluss[-1], eintraege)
    return maengel


def _text_ok(g: dict[str, Any], e: dict[str, str]) -> list[str]:
    """Was in der Beschriftung einer Entscheidung fehlt (Ticket, Zeitpunkt, Frage, Wahl)."""
    gtext = _norm(" ".join(g["text"]))
    frage, wahl = _frage_wahl(e.get("was", ""))
    fehlt = []
    if f"#{e.get('ticket', '')}" not in gtext:
        fehlt.append("Ticket")
    if zeit_text(e.get("ts", "")) not in gtext:
        fehlt.append("Zeitpunkt")
    if _norm(frage) not in gtext:
        fehlt.append("Frage")
    if wahl and _norm(wahl) not in gtext:
        fehlt.append("Wahl")
    return fehlt


def _pruefe_grafik(folie: dict[str, Any], eintraege: list[dict[str, str]]) -> list[str]:
    if not eintraege:
        return (
            []
            if "Keine Entscheidungen" in " ".join(folie["text"])
            else ["Teil 3: Leer-Folie „Keine Entscheidungen“ fehlt."]
        )
    gruppen = list(folie["gruppen"])
    if not gruppen:
        return [
            "Teil 3: keine sichtbare Entscheidungs-Grafik (`g.entscheidung` in `svg.zeitstrahl`) gefunden."
        ]
    maengel: list[str] = []
    sollen = ueberholt_map(eintraege)
    for i, e in enumerate(eintraege):
        t, ts = e.get("ticket", ""), e.get("ts", "")
        if not ts or not t:
            maengel.append(
                f"Entscheidung #{t}: Daten haben keinen {'Zeitpunkt' if t else 'Ticket'}."
            )
            continue
        kand = [g for g in gruppen if (g["ticket"], g["ts"]) == (t, ts)]
        if not kand:
            maengel.append(
                f"Entscheidung #{t} ({ts}) fehlt in der Grafik oder hat dort kein Ticket/keinen Zeitpunkt."
            )
            continue
        g = next((g for g in kand if not _text_ok(g, e)), kand[0])
        gruppen.remove(g)
        fehlt = _text_ok(g, e)
        if fehlt:
            maengel.append(f"Entscheidung #{t}: Beschriftung ohne {', '.join(fehlt)}.")
        if i in sollen and f"überholt durch #{sollen[i]}" not in _norm(
            " ".join(g["text"])
        ):
            maengel.append(f"Entscheidung #{t}: „überholt durch #{sollen[i]}“ fehlt.")
    return maengel
