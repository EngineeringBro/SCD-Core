"""
Learning Store — persistent human guidance for low-confidence tickets.

Guidance is organised by Jira topic name. Each topic gets one YAML file:
  knowledge/learned/<topic_slug>.yaml

When the CX pipeline runs, relevant guidance is loaded by module.py and
injected into the core_cx_llm prompt so future tickets on the same topic
start with an authoritative human answer rather than guessing from analogies.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # PyYAML — already in requirements.txt

# Relative to repo root; orchestrator / Actions both run from there.
_KNOWLEDGE_DIR = Path("knowledge/learned")


def _topic_slug(topic: str) -> str:
    """Convert a Jira topic name into a filesystem-safe, lowercase slug."""
    slug = re.sub(r"[^\w\s-]", "", topic.lower())
    slug = re.sub(r"[\s-]+", "_", slug.strip())
    return slug or "unknown"


# ── Public read API ────────────────────────────────────────────────────────────

def load_guidance(topic: str) -> list[dict[str, Any]]:
    """
    Return all saved guidance entries for *topic*.
    Returns an empty list if the topic has no learned data yet.
    """
    path = _KNOWLEDGE_DIR / f"{_topic_slug(topic)}.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("entries", [])


def get_guidance_text(topic: str) -> str | None:
    """
    Convenience wrapper: returns a ready-to-inject guidance block for the LLM
    prompt, or None if no guidance exists for the topic.

    Format:
        HUMAN-VERIFIED GUIDANCE (from {n} saved entr{y|ies}):
        1. <guidance text>
        2. <guidance text>
        ...
    """
    entries = load_guidance(topic)
    if not entries:
        return None
    lines = [f"HUMAN-VERIFIED GUIDANCE ({len(entries)} saved {'entry' if len(entries) == 1 else 'entries'} for topic \"{topic}\"):"]
    for i, e in enumerate(entries, 1):
        by = e.get("provided_by", "unknown")
        lines.append(f"{i}. [{by}] {e.get('guidance', '').strip()}")
    return "\n".join(lines)


# ── Public write API ───────────────────────────────────────────────────────────

def save_guidance(
    topic: str,
    ticket_id: str,
    guidance: str,
    provided_by: str,
    issue_number: int,
) -> None:
    """
    Append a new guidance entry for *topic*.
    Creates ``knowledge/learned/<slug>.yaml`` if it doesn't exist.
    """
    slug = _topic_slug(topic)
    _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _KNOWLEDGE_DIR / f"{slug}.yaml"

    if path.exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {"topic": topic, "topic_slug": slug, "entries": []}

    data.setdefault("entries", []).append(
        {
            "ticket_id": ticket_id,
            "guidance": guidance.strip(),
            "provided_by": provided_by,
            "provided_at": datetime.now(timezone.utc).isoformat(),
            "issue_number": issue_number,
        }
    )
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    print(f"[learning_store] Saved guidance for topic='{topic}' (slug={slug})")
