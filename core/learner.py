"""
Learner — captures knowledge from human outcomes AND exposes the learning store.

Triggered by two events (NOT inline during ticket processing):

  1. REJECTION: Human closes a GitHub Issue proposal with a comment explaining
     what was wrong. The closing comment IS the learning signal.
     Called by: GitHub Actions workflow on issues.closed event (when issue
     was NOT executed — i.e. closed without the scd-executed label).

  2. EXECUTION SUCCESS: Executor completes successfully for a ticket.
     Called by: core/executor.py after close_proposal().

Memory is per-module: knowledge/learned/<module>/<topic_slug>.yaml
Each module only learns from its own outcomes — no shared memory.

Public API (importable from core.learner):
  on_rejection()          — record human rejection comment
  on_execution()          — record successful execution
  save_guidance()         — low-level write (used internally + scripts)
  get_guidance_text()     — formatted block for LLM prompts
  load_guidance()         — raw entries list
  count_verified_guidance() — count of executed entries
  get_module_override()   — human-forced module routing for a ticket
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # PyYAML — already in requirements.txt

from core.resolution_suggestion import ResolutionSuggestion

# Relative to repo root; orchestrator / Actions both run from there.
_KNOWLEDGE_BASE = Path("knowledge/learned")


# ── Internal helpers ────────────────────────────────────────────────────────────

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


# ── Read API ────────────────────────────────────────────────────────────────────

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
        print(f"[learner] Marked verified: module={module_name} topic='{topic}' ticket={ticket_id}")


def get_module_override(topic: str, ticket_id: str) -> str | None:
    """
    Return the module name a human has instructed for *ticket_id* within *topic*,
    or None if no override exists.
    Checks all module directories — overrides are not module-scoped.
    """
    slug = _topic_slug(topic)
    if _KNOWLEDGE_BASE.exists():
        for module_dir in _KNOWLEDGE_BASE.iterdir():
            path = module_dir / f"{slug}.yaml"
            if not path.exists():
                path = module_dir / "unknown.yaml"
            if path.exists():
                with open(path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                override = data.get("ticket_overrides", {}).get(ticket_id)
                if override:
                    return override
    return None


# ── Write API ───────────────────────────────────────────────────────────────────

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

    overrides = data.setdefault("ticket_overrides", {})
    if module_override:
        overrides[ticket_id] = module_override
        print(f"[learner] module_override set: {ticket_id} -> '{module_override}'")

    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    print(f"[learner] Saved: module={module_name} topic='{topic}' outcome={outcome} ticket={ticket_id}")


def on_rejection(
    issue_number: int,
    module_name: str,
    topic: str,
    ticket_id: str,
    comment_text: str,
    provided_by: str = "human",
) -> None:
    """
    Called when a human closes a proposal issue with a rejection comment.

    The comment_text is the human's explanation of what was wrong —
    this becomes the learning entry for this module + topic combination.

    Args:
        issue_number:  GitHub Issue number that was rejected
        module_name:   Which module produced the rejected proposal
        topic:         Jira topic field value (e.g. "Transaction Errors")
        ticket_id:     Jira ticket ID (e.g. "SCD-141831")
        comment_text:  The human's closing comment — the learning signal
        provided_by:   GitHub username of the reviewer (for attribution)
    """
    if not comment_text.strip():
        print(f"[learner] on_rejection: no comment text for {ticket_id} — nothing to learn")
        return

    save_guidance(
        topic=topic,
        module_name=module_name,
        ticket_id=ticket_id,
        guidance=comment_text.strip(),
        provided_by=provided_by,
        issue_number=issue_number,
        outcome="rejected",
    )
    print(f"[learner] Rejection captured: module={module_name} topic='{topic}' ticket={ticket_id}")


def on_execution(
    module_name: str,
    topic: str,
    ticket_id: str,
    suggestion: ResolutionSuggestion,
    issue_number: int,
) -> None:
    """
    Called by executor.py after a proposal executes successfully.

    Records what worked — diagnosis, module, actions — as a positive
    learning example for this module + topic combination.

    Args:
        module_name:   Which module produced the executed proposal
        topic:         Jira topic field value
        ticket_id:     Jira ticket ID
        suggestion:    The ResolutionSuggestion that was executed
        issue_number:  GitHub Issue number that was approved and executed
    """
    guidance_text = (
        f"EXECUTED SUCCESSFULLY. "
        f"Diagnosis: {suggestion.diagnosis} "
        f"Actions: {[a.type for a in suggestion.actions]}. "
        f"Confidence at execution: {suggestion.module_confidence}."
    )

    save_guidance(
        topic=topic,
        module_name=module_name,
        ticket_id=ticket_id,
        guidance=guidance_text,
        provided_by="executor",
        issue_number=issue_number,
        outcome="executed",
    )
    print(f"[learner] Execution captured: module={module_name} topic='{topic}' ticket={ticket_id}")
