"""Kern des Skills ``to-spawn`` (Version 2, vorläufig).

Enthält: Repo-Konfiguration, Regularien-Prüfung des Manifests, Bau-Log (JSONL je
Ticket), Stop-/SubagentStop-Hooks und die Staffel-Schleife für Bau-Sessions.
Die Terminal-Adapter (``spawn_local.ps1`` / ``spawn_srv.ps1``) bleiben unverändert;
dieses Paket ruft sie nur auf.
"""

from __future__ import annotations

__all__ = ["bau_log", "bau_loop", "config", "gh", "hooks", "inventur", "manifest", "spawn"]
