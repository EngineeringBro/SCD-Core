from __future__ import annotations
from core.module_base import Module
from core.pattern_store import get_topic_pattern, summarise
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

SPAM_TOPIC_NAME = "Spam"
SPAM_TOPIC_ID   = "10438"


class SpamModule(Module):
    """
    Handles tickets tagged with Topic = Spam (customfield_10170 id 10438).

    Confidence comes from the mined historical pattern, not a hardcoded value.
    Historical data (465 tickets): ~50% Dismissed, ~24% Done, ~23% Fixed.
    That 50% means the spam label alone is a weak signal — Brain 1 is expected
    to look at content and adjust confidence before the gatekeeper runs.
    """

    name = "spam"
    version = "0.1"  # conf=0.50 (dynamic, mined) < 90% -> v0.x; .1 = first edition

    def matches(self, ticket: dict) -> bool:
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )
        return topic_id == SPAM_TOPIC_ID

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        status    = (ticket.get("fields", {}).get("status") or {}).get("name", "")
        email     = (
            (ticket.get("fields", {}).get("reporter") or {}).get("emailAddress") or ""
        ).lower().strip()

        pattern    = get_topic_pattern(SPAM_TOPIC_NAME, min_confidence=0.0)
        confidence = pattern.confidence if pattern else 0.50
        top_res    = pattern.top_resolution if pattern else "Dismissed"
        history    = summarise(SPAM_TOPIC_NAME, email or None)

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=(
                f"Ticket tagged as Spam (topic id {SPAM_TOPIC_ID}). "
                f"{history}. "
                "Verify the content is genuinely unsolicited noise before closing."
            ),
            evidence=[
                {"source": "topic_field", "value": f"{SPAM_TOPIC_ID} ({SPAM_TOPIC_NAME})"},
                {"source": "historical_pattern", "value": history},
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
