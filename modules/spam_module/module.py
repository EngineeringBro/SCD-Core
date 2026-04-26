from __future__ import annotations
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget


class SpamModule(Module):
    name = "spam"
    version = "1.0.0"

    def matches(self, ticket: dict) -> bool:
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )
        return topic_id == "10438"

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        status = (ticket.get("fields", {}).get("status") or {}).get("name", "")

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis="Spam ticket — dismiss and close per standard profile.",
            evidence=[
                {"source": "topic_field", "value": "10438 (Spam)"},
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
                    type="jira_field_update",
                    payload={"field": "customfield_10170", "value_id": "10438"},
                ),
                Action(
                    step=2,
                    type="jira_transition",
                    payload={"to": "Closed", "resolution": "Dismissed"},
                ),
            ],
            module_confidence=0.99,
        )
