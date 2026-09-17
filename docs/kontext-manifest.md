# Kontext-Manifest für Ticket-Sessions

Status: gültig seit 2026-09-12 (portiert aus FLOWBASE, projektbezogen angepasst).

## Was das Manifest ist

Pro Spec-Feature liegt unter `docs/agents/manifests/spec-<S>.json` eine Datei, die für
jedes Ticket festlegt, welche Skills, MCP-Server und Docs eine frische Ticket-Session
zusätzlich zum Core-Set laden soll, plus die konkreten Datei-Stellen (`Pfad:Start-Ende`),
die die Session lesen muss — ohne selbst zu explorieren. `bau <N>` (`scripts/bau.py`)
liest diese Datei, startet damit eine schlanke Claude-Session und übergibt den
Loop-Prompt (Domino: wartet auf Blocker → claimt Ticket → `/implement` → schließt Issue
→ Loop endet, nächstes Terminal übernimmt das nächste freie Ticket).

## Schema (exakt, SSOT)

```json
{
  "spec": 83,
  "feature": "grundbilder-upgrade",
  "tickets": {
    "71": {
      "title": "Grundbilder-Raster …",
      "skills": ["user-flow-lens", "duoplus-api"],
      "mcp": ["claude-in-chrome"],
      "docs": ["docs/SPEC_grundbilder_upgrade.md"],
      "files": ["web/services/real_model_intake.py:944-1160", "web/static/js/studio.js:6730-6900"]
    }
  }
}
```

- `skills`/`mcp` sind **zusätzlich** zum Core-Set (siehe unten) — Core nicht auflisten.
- `docs` sind Pfade, die die Session gezielt lesen soll (kein Auto-Load).
- `files` sind die Kontext-Paket-Stellen aus dem Ticket-Issue (`Pfad:Start-Ende`), damit
  die Session nicht selbst explorieren muss.
- Ticket-Schlüssel = echte GitHub-Issue-Nummer (String).

## Core-Set (immer geladen, unabhängig vom Manifest)

`docs/agents/manifests/_default.json`:

- Skills: `fable-1080`, `implement`, `prp-commit`, `code-review`, `verify-hard`,
  `german-umlauts`, `loop`, `run`, `diagnosing-bugs`
- MCP: leer (Core bringt keine MCP-Server mit — jedes Ticket listet seine selbst)
- Prompt-Template: enthält die Projekt-Regeln aus `CLAUDE.md` (Worktree `C:/dev/wt-<N>`,
  Pathspec-Commit mit `[skip ci]`, Deploy nur per `safe_deploy_vps.sh --skip-ci` nach
  Live-Jobs-Check, `checkpoint:human` = warten auf Davids GitHub-Kommentar).

Verfügbare MCP-Namen zur Auswahl im Manifest (Stand 2026-09-12, `bau.py` löst sie über
`~/.claude.json` global + Projekt-Eintrag, Repo-`.mcp.json` und
`~/.claude/toolbox/mcp-archive.json` auf): `claude-in-chrome` (Builtin),
`chrome-devtools`, `firecrawl`, `perplexity`, `sensei`, `postproxy` (Projekt),
`n8n-mcp`, `n8n-mcp-lite`, `context7`, `kapture`, `windows-mcp`, `scrape-creators`,
`instagram-mcp`, `replicate`, `dataforseo`, `shadcn`, `stitch`, `supabase`.
Nicht steuerbar: die claude.ai-Connectoren (Airtable, Gmail, Notion, …) — sie hängen
am Login, nicht an `--mcp-config`.

Projekt-Pflicht-Skills je Bereich (aus `CLAUDE.md`/`projekt-routing`), die ein Ticket
bei Bedarf zusätzlich listet: `duoplus-api` (Cloud-Phone-API), `postproxy-api` /
`postproxy-n8n` (Posting), `n8n-mastery` (Workflows), `mastery-knowledge` (Proxy/APIs),
`user-flow-lens` + `auto-polish` (UI-Fläche), `asteragon-reel-concept-scanner` (Reels).

## Ablauf: to-spec → to-tickets → to-spawn → `bau N`

1. **`to-spec`** entwirft die Spec und ergänzt am Ende `## Werkzeuge`: pro Arbeitsbereich
   der Spec eine Zeile Skills · MCPs · Docs (Toolbox `toolbox.py find <wort>` zur Suche).
2. **`to-tickets`** übernimmt beim Explore-Schritt pro Ticket die konkreten Datei-Stellen
   als `## Kontext-Paket (nur das lesen)` in den Issue-Body, dazu `## Werkzeuge` aus der
   Spec-Tabelle, setzt native Blocker-Kanten (`dependencies/blocked_by`) und schreibt
   `docs/agents/manifests/spec-<S>.json` (Ticket-Nummern = echte Issue-Nummern).
3. **`/to-spawn <S>`** (Skill `~/.claude/skills/to-spawn`, GitHub `Shavy72/claude-skill-to-spawn`) öffnet
   ein Windows-Terminal-Fenster mit `wache <S>` + einem Tab `bau <N>` je offenem Ticket — alle auf
   einmal, geblockte warten im Skript (0 Token). Kontrolle: `sessions <S>`.
4. **`bau <N>`** (`scripts/bau.py`) liest `spec-<S>.json`, baut daraus eine
   `--settings`-Datei mit reduzierter Skill-Liste + `--mcp-config`/`--strict-mcp-config`,
   startet die Session und übergibt den Loop-Prompt.

## Was sich pro Session steuern lässt

| Posten | Mechanismus | Ersparnis |
|---|---|---|
| MCP-Server | `claude --mcp-config manifest.json --strict-mcp-config` | mittel — Tool-Schemas sind deferred (ToolSearch), es bleiben Namen + Server-Anweisungen (Connector-Blöcke ≈ 5–10K) |
| Skills | `--settings` mit reduzierter Skill-Liste (skillOverrides) | hoch — Listung liegt hier bei ~26K (Budget 20K), Ziel ~10–15 Einträge |
| Rules | nur path-scoped Rules sind bedingt; Rest lädt immer | gering ohne Umbau |
| Dateien | Kontext-Paket (`files` im Manifest, aus dem Ticket-Issue übernommen) | hoch — adressiert den größten Read-Posten direkt |
| Memory/MEMORY.md | lädt immer | 0 |

## Grenzen

- **Rules bleiben ~77K.** Das Manifest fasst Rules nicht an — es steuert nur Skills, MCP
  und Datei-Zugriff. Der globale Read-Range-Hook (`~/.claude/hooks/read-range-guard.mjs`)
  gilt ohnehin überall und wird hier nicht neu gebaut.
- **Subagent 7a erbt die Tools der Hauptsession.** Läuft ein Ticket als Subagent im
  Orchestrator-Modus (Schritt 7a), wirkt das Manifest nicht. Es wirkt **nur** bei einem
  eigenen Terminal (`bau <N>` bzw. Hand-Modus 7b), weil dort eine frische Claude-Session
  mit eigener `--settings`/`--mcp-config` startet.
- **Geteilter Hauptbaum.** `bau <N>` startet im Repo-Ordner; die Session legt sich laut
  Prompt-Template selbst einen Worktree `C:/dev/wt-<N>` an und arbeitet nur dort.
