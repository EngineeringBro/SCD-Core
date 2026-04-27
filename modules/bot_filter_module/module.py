from __future__ import annotations
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

# Known automated senders — never human, safe to auto-dismiss.
# Supports exact addresses and @domain suffixes (e.g. "@noreply.example.com").
BOT_EMAILS: frozenset[str] = frozenset([
    "noreply@repairq.io",
    "notify@ringcentral.com",
    "azure-noreply@microsoft.com",
    "help@mandrill.com",
    "service@paypal.com",
])


def _is_bot(email: str) -> bool:
    e = (email or "").lower().strip()
    return e in BOT_EMAILS


class BotFilterModule(Module):
    name = "bot_filter"
    version = "1.0.0"

    def matches(self, ticket: dict) -> bool:
        email = (
            (ticket.get("fields", {}).get("reporter") or {}).get("emailAddress", "") or ""
        )
        return _is_bot(email)

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        reporter = ticket.get("fields", {}).get("reporter") or {}
        email = (reporter.get("emailAddress") or "").lower().strip()
        display = reporter.get("displayName") or email
        status = (ticket.get("fields", {}).get("status") or {}).get("name", "")

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=(
                f"Automated bot ticket from '{email}' ({display}). "
                "Reporter is a known no-reply or system sender — no human action required. "
                "Dismiss and close."
            ),
            evidence=[
                {"source": "reporter_email", "value": email},
                {"source": "reporter_display", "value": display},
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
                    type="jira_transition",
                    payload={"to": "Closed", "resolution": "Dismissed"},
                ),
            ],
            module_confidence=0.99,
        )
