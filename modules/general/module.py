"""
General Module — fallback for tickets that don't match any specific module.

Logs the ticket to the unrouted notification log and produces a minimal
ResolutionSuggestion with no actions (proposal requires human triage).
"""
from __future__ import annotations
from datetime import datetime, timezone
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget


class GeneralModule(Module):
    name = "general"
    version = "1.0.0"

    def matches(self, ticket: dict) -> bool:
        # General is always the fallback — router assigns it explicitly
        return True

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        fields = ticket.get("fields", {})
        subject = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "")
        topic = (fields.get("customfield_10170") or {})
        topic_id = str(topic.get("id", ""))
        topic_name = topic.get("value", "unknown")

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=(
                f"No specific module matched this ticket. "
                f"Topic: {topic_name} ({topic_id}). Requires human triage."
            ),
            evidence=[
                {"source": "topic_field", "value": f"{topic_id} ({topic_name})"},
                {"source": "summary", "value": subject},
            ],
            revalidation_targets=[
                RevalidationTarget(
                    type="jira_field",
                    snapshot={"field": "status", "value": status},
                ),
            ],
            actions=[
                Action(
                    step=1,
                    type="notification_log_append",
                    payload={
                        "log_file": "unrouted",
                        "row": {
                            "seen_at": datetime.now(timezone.utc).isoformat(),
                            "ticket": ticket_id,
                            "topic_field": f"{topic_id} ({topic_name})",
                            "subject": subject[:200],
                        },
                    },
                ),
            ],
            module_confidence=0.0,
            module_notes="General fallback — no module matched. Logged to unrouted.md for analysis.",
        )
