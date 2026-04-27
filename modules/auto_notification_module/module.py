"""
AutoNotificationModule — classifies ticket into a notification profile and
returns its pre-defined action sequence. No business logic lives here.

All decisions (field IDs, assignees, resolution, confidence) are in profiles/:
  profiles/revv_error_v1_0.py      — topic 10494 (Revv Error Report)     [v1.0, conf=0.90]
  profiles/auto_notif_v0_1.py      — topic 10404 (Automatic Notifications) [v0.1, conf=0.76]
  profiles/assurant_email_v1_0.py  — reporter @assurant.com               [v1.0, conf=0.96]

Explicitly excluded:
  RingCentral Alert (topic 10496) — real customer voicemail/call tickets.
"""
from __future__ import annotations
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, RevalidationTarget
from modules.auto_notification_module.profiles import (
    revv_error_v1_0,
    auto_notif_v0_1,
    assurant_email_v1_0,
)

_TOPIC_REVV        = "10494"
_TOPIC_AUTO_NOTIF  = "10404"
_TOPIC_RINGCENTRAL = "10496"  # excluded — real customer tickets


def _topic_id(ticket: dict) -> str:
    return str((ticket.get("fields", {}).get("customfield_10170") or {}).get("id", ""))


def _reporter_email(ticket: dict) -> str:
    return ((ticket.get("fields", {}).get("reporter") or {}).get("emailAddress", "") or "").lower().strip()


def _status(ticket: dict) -> str:
    return (ticket.get("fields", {}).get("status") or {}).get("name", "")


class AutoNotificationModule(Module):
    name = "auto_notification"
    version = "1.0"

    def matches(self, ticket: dict) -> bool:
        tid = _topic_id(ticket)
        if tid == _TOPIC_RINGCENTRAL:
            return False
        return (
            tid in (_TOPIC_REVV, _TOPIC_AUTO_NOTIF)
            or _reporter_email(ticket).endswith("@assurant.com")
        )

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        tid = _topic_id(ticket)
        email = _reporter_email(ticket)
        status = _status(ticket)

        if tid == _TOPIC_REVV:
            profile = revv_error_v1_0
            signal  = f"topic={tid} (Revv Error Report)"
        elif tid == _TOPIC_AUTO_NOTIF:
            profile = auto_notif_v0_1
            signal  = f"topic={tid} (Automatic Notifications)"
        else:
            profile = assurant_email_v1_0
            signal  = f"reporter_email={email}"

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=f"{self.version}+profile/{profile.__name__.rsplit('.', 1)[-1]}",
            diagnosis=profile.DIAGNOSIS,
            evidence=[
                {"source": "notification_profile", "value": profile.__name__.rsplit(".", 1)[-1]},
                {"source": "match_signal",          "value": signal},
            ],
            revalidation_targets=[
                RevalidationTarget(
                    type="jira_field",
                    snapshot={"field": "status", "value": status},
                ),
            ],
            actions=list(profile.ACTIONS),
            module_confidence=profile.CONFIDENCE,
        )

