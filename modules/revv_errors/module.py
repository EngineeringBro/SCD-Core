from __future__ import annotations
from datetime import datetime, timezone
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget


class RevvErrorsModule(Module):
    name = "revv_errors"
    version = "1.0.0"

    def matches(self, ticket: dict) -> bool:
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )
        return topic_id == "10494"

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        subject = ticket.get("fields", {}).get("summary", "")
        status = (ticket.get("fields", {}).get("status") or {}).get("name", "")
        reporter = (ticket.get("fields", {}).get("reporter") or {}).get("displayName", "system")

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis="Automated Revv sync error report. No customer impact.",
            evidence=[
                {"source": "topic_field", "value": "10494 (Revv Error Report)"},
                {"source": "reporter", "value": reporter},
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
                    payload={"field": "customfield_10170", "value_id": "10494"},
                ),
                Action(
                    step=2,
                    type="jira_field_update",
                    payload={"field": "customfield_10201", "value_id": "10499"},
                ),
                Action(
                    step=3,
                    type="jira_internal_comment",
                    payload={"body": "Auto-closed Revv error notification. No customer impact."},
                ),
                Action(
                    step=4,
                    type="notification_log_append",
                    payload={
                        "log_file": "revv-reports",
                        "row": {
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                            "ticket": ticket_id,
                            "client": _extract_client(ticket),
                            "summary": subject[:200],
                        },
                    },
                ),
                Action(
                    step=5,
                    type="jira_transition",
                    payload={"to": "Closed", "resolution": "Known Error"},
                ),
            ],
            module_confidence=0.97,
        )


def _extract_client(ticket: dict) -> str:
    org = ticket.get("fields", {}).get("customfield_10002")
    if org and isinstance(org, list) and org:
        return org[0].get("name", "unknown")
    return "unknown"
