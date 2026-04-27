from __future__ import annotations
from core.module_base import Module
from core.pattern_store import get_email_pattern, get_bot_emails
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget


class AutoSendersModule(Module):
    """
    Routes tickets from data-confirmed automated system senders.

    These are legitimate no-reply / platform notification addresses
    (e.g. Assurant claims system), NOT malicious bots.  Which emails
    qualify is determined entirely by mine_patterns.py: any email where
    is_bot=True AND confidence >= 0.85.  No hardcoded list — re-run
    mine_patterns.py to update as new senders accumulate history.
    """

    name = "auto_senders"
    version = "1.1.0"

    def matches(self, ticket: dict) -> bool:
        email = (
            (ticket.get("fields", {}).get("reporter") or {}).get("emailAddress", "") or ""
        ).lower().strip()
        return email in set(get_bot_emails(min_confidence=0.85))

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        reporter  = ticket.get("fields", {}).get("reporter") or {}
        email     = (reporter.get("emailAddress") or "").lower().strip()
        display   = reporter.get("displayName") or email
        status    = (ticket.get("fields", {}).get("status") or {}).get("name", "")

        pattern    = get_email_pattern(email, min_confidence=0.0)
        confidence = pattern.confidence if pattern else 0.85
        top_res    = pattern.top_resolution if pattern else "Dismissed"
        total      = pattern.total if pattern else 0
        top_pct    = pattern.top_resolution_pct if pattern else 0.0

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=(
                f"Automated bot ticket from '{email}' ({display}). "
                f"Historical data: {total:,} tickets from this sender, "
                f"{top_pct * 100:.0f}% resolved as '{top_res}'. "
                "No human action required — close."
            ),
            evidence=[
                {"source": "reporter_email",    "value": email},
                {"source": "reporter_display",  "value": display},
                {"source": "historical_pattern",
                 "value": f"{total} tickets, {top_pct*100:.0f}% → {top_res}"},
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
                    payload={"to": "Closed", "resolution": top_res},
                ),
            ],
            module_confidence=confidence,
        )
