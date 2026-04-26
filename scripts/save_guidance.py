"""
save_guidance.py — called by the scd-core-learn GitHub Actions workflow
when a human comments on a ``scd-guidance-needed`` issue.

Reads environment variables injected by the workflow:
  COMMENT_BODY   — raw text of the comment
  ISSUE_TITLE    — full issue title (used to extract ticket ID and topic)
  ISSUE_NUMBER   — GitHub issue number
  COMMENTER      — GitHub username of the commenter
  TOPIC          — topic name extracted from the issue body by the workflow step

Appends a guidance entry to ``knowledge/learned/<topic_slug>.yaml``.
"""
from __future__ import annotations

import os
import re
import sys

# Run from repo root: python scripts/save_guidance.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learning_store import save_guidance


def main() -> None:
    comment_body = os.environ.get("COMMENT_BODY", "").strip()
    issue_title = os.environ.get("ISSUE_TITLE", "")
    issue_number = int(os.environ.get("ISSUE_NUMBER", "0"))
    commenter = os.environ.get("COMMENTER", "unknown")
    topic = os.environ.get("TOPIC", "").strip()

    if not comment_body:
        print("[save_guidance] Comment body is empty — nothing to save")
        return

    # Extract ticket ID from issue title e.g.:
    # "[GUIDANCE NEEDED] SCD Guidance: SCD-141990 | Topic: Transaction Errors — summary"
    ticket_match = re.search(r"(SCD-\d+)", issue_title)
    ticket_id = ticket_match.group(1) if ticket_match else "UNKNOWN"

    # Fall back to extracting topic from title if workflow step didn't find it
    if not topic:
        topic_match = re.search(r"Topic:\s*([^|—\n]+)", issue_title)
        topic = topic_match.group(1).strip() if topic_match else "Unknown"

    print(
        f"[save_guidance] ticket={ticket_id} | topic={topic!r} | "
        f"by={commenter} | issue=#{issue_number}"
    )

    save_guidance(
        topic=topic,
        ticket_id=ticket_id,
        guidance=comment_body,
        provided_by=commenter,
        issue_number=issue_number,
    )

    print(f"[save_guidance] Done — guidance for topic '{topic}' saved.")


if __name__ == "__main__":
    main()
