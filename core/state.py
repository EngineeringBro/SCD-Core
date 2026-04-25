"""
State management — read/write scan-state.json.
Records last_run timestamp and per-ticket signatures to enable delta scanning.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path("state/scan-state.json")


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "last_run": None,
        "processed_tickets": {},
    }


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def mark_processed(state: dict, ticket_id: str, proposal_issue: int | None = None) -> None:
    state.setdefault("processed_tickets", {})[ticket_id] = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "proposal_issue": proposal_issue,
    }


def update_last_run(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()


def ticket_needs_processing(state: dict, ticket: dict) -> bool:
    """
    Return True if the ticket has been updated since we last processed it,
    or if we've never seen it.
    """
    ticket_id = ticket["key"]
    updated = ticket.get("fields", {}).get("updated", "")
    processed = state.get("processed_tickets", {}).get(ticket_id)

    if not processed:
        return True

    last_processed = processed.get("processed_at", "")
    return updated > last_processed
