"""
capture_rejection.py — called by the scd-core-reject GitHub Actions workflow
when a human closes an scd-proposal issue WITHOUT the scd-executed label
(meaning they rejected it, not approved it).

Reads environment variables injected by the workflow:
  ISSUE_BODY         — raw issue body (extracts ticket_id, module_name, topic)
  ISSUE_NUMBER       — GitHub issue number
  CLOSING_COMMENT    — last human comment on the issue (the rejection reason)
  CLOSER             — GitHub username of the person who closed it

Calls learner.on_rejection() to store the human's feedback.
"""
from __future__ import annotations

import os
import re
import sys

# Run from repo root: python scripts/capture_rejection.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learner import on_rejection


def _extract_table_field(body: str, field_name: str) -> str:
    """Extract a value from a Markdown table row: | **field_name** | value |"""
    pattern = rf"\|\s*\*\*{re.escape(field_name)}\*\*\s*\|\s*`?([^`|\n]+?)`?\s*\|"
    m = re.search(pattern, body, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_topic_from_json(body: str) -> str:
    """
    Try to extract the topic from the embedded JSON block in the issue body.
    Falls back to empty string if not found.
    """
    import json
    json_blocks = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    for block in reversed(json_blocks):
        try:
            data = json.loads(block)
            # sub_agent_attribution.topic is set by the module
            topic = (data.get("sub_agent_attribution") or {}).get("topic", "")
            if topic:
                return topic
        except (ValueError, KeyError):
            continue
    return ""


def main() -> None:
    issue_body = os.environ.get("ISSUE_BODY", "")
    issue_number = int(os.environ.get("ISSUE_NUMBER", "0"))
    closing_comment = os.environ.get("CLOSING_COMMENT", "").strip()
    closer = os.environ.get("CLOSER", "unknown")

    if not closing_comment:
        print("[capture_rejection] No closing comment found — nothing to learn from this rejection")
        return

    # Extract ticket_id from table row: | **Ticket** | SCD-141831 |
    ticket_id = _extract_table_field(issue_body, "Ticket")
    if not ticket_id:
        # Fall back: extract from issue title SCD Proposal: SCD-XXXXX
        title_match = re.search(r"(SCD-\d+)", os.environ.get("ISSUE_TITLE", ""))
        ticket_id = title_match.group(1) if title_match else "UNKNOWN"

    # Extract module_name from table row: | **Module** | `orphaned_transaction` v1.1 |
    module_raw = _extract_table_field(issue_body, "Module")
    # Strip version suffix e.g. "orphaned_transaction v1.1" -> "orphaned_transaction"
    module_name = re.sub(r"\s+v[\d.]+.*$", "", module_raw).strip() or "general"

    # Extract topic from embedded JSON block
    topic = _extract_topic_from_json(issue_body)
    if not topic:
        topic = "Unknown"

    print(
        f"[capture_rejection] ticket={ticket_id} | module={module_name} | "
        f"topic={topic!r} | closed_by={closer} | issue=#{issue_number}"
    )

    on_rejection(
        issue_number=issue_number,
        module_name=module_name,
        topic=topic,
        ticket_id=ticket_id,
        comment_text=closing_comment,
        provided_by=closer,
    )

    print(f"[capture_rejection] Done — rejection captured for module '{module_name}' topic '{topic}'")


if __name__ == "__main__":
    main()
