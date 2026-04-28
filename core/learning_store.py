"""
Learning Store — persistent human knowledge, captured from human outcomes.

Memory is per-module — each module only learns from its own outcomes:
  knowledge/learned/<module_name>/<topic_slug>.yaml

Two learning events feed this store:
  1. Rejection: human closes a proposal issue with a comment
  2. Execution: executor completes successfully

Guidance is loaded by each module internally and injected into its own LLM
prompt — no cross-module memory sharing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # PyYAML — already in requirements.txt

# Relative to repo root; orchestrator / Actions both run from there.
_KNOWLEDGE_BASE = Path("knowledge/learned")


def _module_dir(module_name: str) -> Path:
    """Return the per-module knowledge directory, creating it if needed."""
    path = _KNOWLEDGE_BASE / module_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _topic_slug(topic: str) -> str:
    """Convert a Jira topic name into a filesystem-safe, lowercase slug."""
    slug = re.sub(r"[^\w\s-]", "", topic.lower())
    slug = re.sub(r"[\s-]+", "_", slug.strip())
    return slug or "unknown"


# ── Public read API ────────────────────────────────────────────────────────────

def load_guidance(topic: str, module_name: str = "general") -> list[dict[str, Any]]:
    """
    Return all saved guidance entries for *topic* within *module_name*'s memory.
    Returns an empty list if no data exists yet.
    """
    path = _module_dir(module_name) / f"{_topic_slug(topic)}.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("entries", [])


def get_guidance_text(topic: str, module_name: str = "general") -> str | None:
    """
    Returns a ready-to-inject guidance block for the LLM prompt,
    or None if no guidance exists for this module + topic combination.

    Format:
        LEARNED GUIDANCE (from {n} entr{y|ies} for module '{module_name}'):
        1. [executed] <guidance text>
        2. [rejected] <guidance text>
        ...
    """
    entries = load_guidance(topic, module_name=module_name)
    if not entries:
        return None
    lines = [f"LEARNED GUIDANCE ({len(entries)} {'entry' if len(entries) == 1 else 'entries'} for module '{module_name}', topic \"{topic}\"):"]
    for i, e in enumerate(entries, 1):
        by = e.get("provided_by", "unknown")
        outcome = e.get("outcome", "unknown")
        lines.append(f"{i}. [{outcome} by {by}] {e.get('guidance', '').strip()}")
    return "\n".join(lines)


def count_verified_guidance(topic: str, module_name: str = "general") -> int:
    """Return the count of executed (positive) guidance entries for this module + topic."""
    entries = load_guidance(topic, module_name=module_name)
    return sum(1 for e in entries if e.get("outcome") == "executed")


def mark_guidance_verified(topic: str, ticket_id: str, module_name: str = "general") -> None:
    """Mark all guidance entries for *ticket_id* in this module + topic as verified."""
    path = _module_dir(module_name) / f"{_topic_slug(topic)}.yaml"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    changed = False
    for entry in data.get("entries", []):
        if entry.get("ticket_id") == ticket_id and not entry.get("verified"):
            entry["verified"] = True
            changed = True
    if changed:
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        print(f"[learning_store] Marked verified: module={module_name} topic='{topic}' ticket={ticket_id}")


# ── Public write API ───────────────────────────────────────────────────────────

def save_guidance(
    topic: str,
    ticket_id: str,
    guidance: str,
    provided_by: str,
    issue_number: int,
    module_name: str = "general",
    outcome: str = "unknown",
    module_override: str | None = None,
) -> None:
    """
    Append a new guidance entry for *module_name* + *topic*.
    Creates ``knowledge/learned/<module>/<slug>.yaml`` if it doesn't exist.

    outcome: 'rejected' | 'executed' | 'unknown'
    If *module_override* is provided, it is stored per-ticket so the orchestrator
    can force-route future runs of that ticket to the specified module.
    """
    slug = _topic_slug(topic)
    path = _module_dir(module_name) / f"{slug}.yaml"

    if path.exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {"topic": topic, "topic_slug": slug, "entries": []}

    entry: dict[str, Any] = {
        "ticket_id": ticket_id,
        "guidance": guidance.strip(),
        "provided_by": provided_by,
        "provided_at": datetime.now(timezone.utc).isoformat(),
        "issue_number": issue_number,
        "outcome": outcome,
    }
    if module_override:
        entry["module_override"] = module_override

    data.setdefault("entries", []).append(entry)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Keep a ticket-level override index for fast lookup by orchestrator
    overrides = data.setdefault("ticket_overrides", {})
    if module_override:
        overrides[ticket_id] = module_override
        print(f"[learning_store] module_override set: {ticket_id} -> '{module_override}'")

    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    print(f"[learning_store] Saved: module={module_name} topic='{topic}' outcome={outcome} ticket={ticket_id}")


def get_module_override(topic: str, ticket_id: str) -> str | None:
    """
    Return the module name a human has instructed for *ticket_id* within *topic*,
    or None if no override exists.
    Checks all module directories — overrides are not module-scoped.
    """
    slug = _topic_slug(topic)
    # Search all module subdirectories for an override for this ticket
    if _KNOWLEDGE_BASE.exists():
        for module_dir in _KNOWLEDGE_BASE.iterdir():
            path = module_dir / f"{slug}.yaml"
            if not path.exists():
                # Also check unknown slug
                path = module_dir / "unknown.yaml"
            if path.exists():
                with open(path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                override = data.get("ticket_overrides", {}).get(ticket_id)
                if override:
                    return override
    return None


def _get_module_override_legacy(topic: str, ticket_id: str) -> str | None:
    """Legacy single-file lookup — kept for migration reference."""
    path = _KNOWLEDGE_BASE / f"{_topic_slug(topic)}.yaml"
    if not path.exists():
        path = _KNOWLEDGE_BASE / "unknown.yaml"
        if not path.exists():
            return None
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("ticket_overrides", {}).get(ticket_id)
