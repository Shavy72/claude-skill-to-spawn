<p align="center">
  <a href="https://github.com/Shavy72/claude-skill-to-spawn/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Shavy72/claude-skill-to-spawn" alt="License"></a>
  <a href="https://github.com/Shavy72/claude-skill-to-spawn/stargazers"><img src="https://img.shields.io/github/stars/Shavy72/claude-skill-to-spawn" alt="Stars"></a>
</p>

# claude-skill-to-spawn

**Spawn every ticket session of a spec at once — one terminal window, one tab per ticket, zero tokens while waiting.**

The last link of a speed-first workflow chain for Claude Code: split the work into small, parallelisable tickets, then start *all* of them in one go. Blocked tickets wait outside Claude (a GitHub poll, no context, no cost) and start themselves the moment their blockers are closed and merged. One extra tab runs a **watcher** session that checks the seams between tickets and never builds anything.

```
 /to-spec  →  /to-tickets  →  /to-spawn  →  bau <N> × n  +  wache <S>
 spec        vertical slices   one window,    each tab: wait (0 tokens) → claim →
 (issue)     + native          all tabs at     implement → tests → close issue
             blocked_by edges  once            watcher: seams, proofs, deploy gate
```

Why it is fast: tickets are cut fine (each fits one fresh context window), everything without a blocker runs in parallel, and nothing waits *inside* a paid session.

## What you get

| File | Purpose |
|---|---|
| `SKILL.md` | The skill Claude Code loads on `/to-spawn` (German — the author's working language). |
| `spawn_local.ps1` | Deterministic launcher: reads the spec manifest, skips closed/running tickets, opens one Windows Terminal window with `wache <S>` + `bau <N>` tabs, then prints the session table. |
| `install.ps1` / `install.sh` | Copies the skill to `~/.claude/skills/to-spawn` (old copy moved to `~/.claude/skills/_alt/`), installs the alias skills, optionally copies the repo forwarders (`-Repo` / `--repo`, missing files only) and creates `.to-spawn/config.json`. `install.ps1` also adds the `bau` / `wache` / `sessions` PowerShell functions. |
| `aliase/` | Alias skills: `/to-spawn-local <S>` (`--ziel local`), `/to-spawn-remote <S>` (`--ziel srv`), `/meta-exec` (old name of `/to-spawn`). |
| `docs/kontext-manifest.md` | The `/to-tickets` add-on this chain needs: manifest schema, **native GitHub `blocked_by` edges** (the launcher waits on them), per-ticket context package, watcher. Copy into your repo's `docs/agents/`. |
| `skripte/` | The logic: `bau.py` (one ticket session, waits for blockers outside Claude), `wache.py` (watcher session), `sessions_stand.py` (which sessions are on: off / waiting / running since / ORPHANED), `spec_stand.py` (one line per ticket for the watcher), `spawn_srv.sh` (tmux launcher on the build server). Repo = `TO_SPAWN_REPO`, else the git root of the current directory. |
| `repo-scripts/` | The per-repo half: thin forwarders with the same names (+ `_to_spawn_weiterleitung.py`) that jump into `skripte/`, plus `_default.json`. Per-repo settings live in `.to-spawn/config.json`. |

## Install

```powershell
git clone https://github.com/Shavy72/claude-skill-to-spawn "$env:TEMP\claude-skill-to-spawn"
pwsh -File "$env:TEMP\claude-skill-to-spawn\install.ps1"            # skill + profile functions
pwsh -File "$env:TEMP\claude-skill-to-spawn\install.ps1" -Repo .     # additionally copy repo-scripts/ into ./scripts of the current repo
```

Requirements: Windows Terminal (`wt`), PowerShell 7, Python 3.12+, `gh` (logged in), Claude Code. The repo needs the manifest convention `docs/agents/manifests/spec-<S>.json` and `docs/agents/manifests/_default.json` (created by `/to-tickets`; a minimal `_default.json` is included).

## The chain, end to end

1. `/to-spec` — grill the problem, write the spec as an issue (words, decisions, acceptance, ticket cut proposal, tools table).
2. `/to-tickets` — vertical slices, one issue each, **native `blocked_by` edges** + sub-issues, `## Kontext-Paket` per ticket, manifest `spec-<S>.json` (see `docs/kontext-manifest.md`). Build size per ticket (`klein` / `groß` / `regulär`) decides how much review runs *during* the build; the heavy review panel + deploy run once per wave, not per ticket.
3. `/to-spawn <S>` — one window, all tabs. Blocked tickets wait for free (GitHub poll every 10 min), start themselves, claim, implement, prove, close. The watcher tab checks seams and proofs and never builds.
4. `sessions <S>` — see what is on at any time.

## Use

```
/to-spawn 182                 # everything of spec #182 (skips closed + already running tickets)
/to-spawn 182 --tickets 188,189,190
sessions 182                  # any time, any terminal: off / waiting / running since HH:MM / ORPHANED
```

## Nest (server setup, #210)

`nest/nest_push.sh` (source machine) and `nest/nest_server.sh` (root on a fresh Debian/Ubuntu
server) turn any repo into a build server; repo-specific steps live in the repo's
`.to-spawn/nest_repo.sh`. Logic sits in `to_spawn.py nest …` (onboarding, sandbox, secrets
via `bws`, tools from `.to-spawn/werkzeuge.json`, permissions shown but only a human
enters them with `nest rechte --eintragen`). Sandbox per worktree (`srt`) is opt-in:
`.to-spawn/config.json` → `sandbox.modus: "an"`; with `sandbox.pflicht` (default) a session
never starts unsandboxed. Known limit: `~/.claude/.credentials.json` stays readable inside
the sandbox — Claude Code does not start without it (checked 2026-09-19: "Not logged in").
Readiness check of a started session (tmux prompt, `git status`/`gh issue edit` without
prompts) is ticket #214.

## Hard rules (learned the expensive way)

- **Never kill `bau.py` without reading `sessions <S>` first.** Only state `waiting` may be killed. A killed `bau.py` leaves its Claude child orphaned; restarting creates a duplicate session on the same worktree.
- **No environment variables and no `;` inside the tab command** — `wt` treats `;` as a tab separator and you get a second window full of broken tabs. Transcript persistence (`CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`) is set by `bau.py` / `wache.py` themselves.
- After spawning, verify by process list. Never claim "running" from the launcher's exit code.

## Roadmap

- `/to-spawn srv <S>` — same sessions as tmux/cmux windows on a build server so the PC can be switched off. Not built yet; the skill answers with the open questions instead of starting anything.

## License

MIT
