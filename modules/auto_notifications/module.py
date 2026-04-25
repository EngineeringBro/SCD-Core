from __future__ import annotations
from datetime import datetime, timezone
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

RINGCENTRAL_TOPIC_ID = "10496"
AUTO_NOTIF_TOPIC_ID = "10404"


class AutoNotificationsModule(Module):
    name = "auto_notifications"
    version = "1.0.0"

    def matches(self, ticket: dict) -> bool:
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )
        return topic_id in (RINGCENTRAL_TOPIC_ID, AUTO_NOTIF_TOPIC_ID)

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        subject = ticket.get("fields", {}).get("summary", "")
        status = (ticket.get("fields", {}).get("status") or {}).get("name", "")
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )

        is_ringcentral = topic_id == RINGCENTRAL_TOPIC_ID
        log_file = "ringcentral" if is_ringcentral else "automatic-notifications"
        field_value = RINGCENTRAL_TOPIC_ID if is_ringcentral else AUTO_NOTIF_TOPIC_ID

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=f"Automated {'RingCentral alert' if is_ringcentral else 'system notification'} — close per profile.",
            evidence=[
                {"source": "topic_field", "value": f"{topic_id}"},
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
                    payload={"field": "customfield_10170", "value_id": field_value},
                ),
                Action(
                    step=2,
                    type="jira_internal_comment",
                    payload={"body": f"Auto-closed {'RingCentral alert' if is_ringcentral else 'automatic notification'}."},
                ),
                Action(
                    step=3,
                    type="notification_log_append",
                    payload={
                        "log_file": log_file,
                        "row": {
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                            "ticket": ticket_id,
                            "summary": subject[:200],
                        },
                    },
                ),
                Action(
                    step=4,
                    type="jira_transition",
                    payload={"to": "Closed", "resolution": "Dismissed"},
                ),
            ],
            module_confidence=0.96,
        )
