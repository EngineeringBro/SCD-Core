"""
AutoNotificationsModule — handles all deterministic automated notification tickets.

Profiles (data-driven from 50,630 historical SCD tickets):

  revv_error:
    Signal:     topic = Revv Error Report (10494)
    Confidence: 0.90  (hardcoded guidance — user confirmed)
    Actions:    Sev 3, Level 1, Support, root_cause=Unknown, assign Tim Parrish,
                log 1m, close Done

  auto_notif:
    Signal:     topic = Automatic Notifications (10404)
    Confidence: 0.76  (76% dismissed in historical data)
    Actions:    Sev 3, Level 1, root_cause=Unknown, assign John Ryon,
                log 1m, close Dismissed

  assurant_email:
    Signal:     reporter_email ends with @assurant.com
    Confidence: 0.96  (96% Fixed/Completed in historical data)
    Actions:    Sev 3, Level 2, Support, root_cause=Integration or Sync Error,
                assign Ian Turner, log 1m, close Fixed / Completed

Explicitly excluded:
  RingCentral Alert (10496) — real customer voicemail/call tickets, never touched.
"""
from __future__ import annotations
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

# Jira field option IDs (confirmed from live Jira API, 2026-04-27)
_SEV_3        = "10043"
_LEVEL_1      = "10471"
_LEVEL_2      = "10472"
_SUPPORT      = "10313"
_ROOT_UNKNOWN = "10501"   # Unknown
_ROOT_INTEG   = "10499"   # Integration or Sync Error

# Topic IDs
_TOPIC_REVV       = "10494"
_TOPIC_AUTO_NOTIF = "10404"
_TOPIC_RINGCENTRAL = "10496"  # explicitly excluded

# Assignee emails
_ASSIGNEE_TIM  = "tim.parrish@servicecentral.com"
_ASSIGNEE_JOHN = "john.ryon@servicecentral.com"
_ASSIGNEE_IAN  = "ian.turner@servicecentral.com"


def _topic_id(ticket: dict) -> str:
    return str((ticket.get("fields", {}).get("customfield_10170") or {}).get("id", ""))


def _reporter_email(ticket: dict) -> str:
    return ((ticket.get("fields", {}).get("reporter") or {}).get("emailAddress", "") or "").lower().strip()


def _status(ticket: dict) -> str:
    return (ticket.get("fields", {}).get("status") or {}).get("name", "")


def _build_actions(
    topic_value_id: str | None,
    severity_id: str,
    support_level_id: str,
    type_of_work_id: str | None,
    root_cause_id: str,
    assignee_email: str,
    resolution: str,
) -> list[Action]:
    """Build the standard action sequence for a notification profile."""
    step = 1
    actions: list[Action] = []

    if topic_value_id:
        actions.append(Action(
            step=step, type="jira_field_update",
            payload={"field": "customfield_10170", "value_id": topic_value_id},
        ))
        step += 1

    actions.append(Action(
        step=step, type="jira_field_update",
        payload={"field": "customfield_10036", "value_id": severity_id},
    ))
    step += 1

    actions.append(Action(
        step=step, type="jira_field_update",
        payload={"field": "customfield_10186", "value_id": support_level_id},
    ))
    step += 1

    if type_of_work_id:
        actions.append(Action(
            step=step, type="jira_field_update",
            payload={"field": "customfield_10143", "value_id": type_of_work_id},
        ))
        step += 1

    actions.append(Action(
        step=step, type="jira_field_update",
        payload={"field": "customfield_10201", "value_id": root_cause_id},
    ))
    step += 1

    actions.append(Action(
        step=step, type="jira_assign",
        payload={"email": assignee_email},
    ))
    step += 1

    actions.append(Action(
        step=step, type="jira_log_time",
        payload={"time_spent": "1m"},
    ))
    step += 1

    actions.append(Action(
        step=step, type="jira_transition",
        payload={"to": "Closed", "resolution": resolution},
    ))

    return actions


class AutoNotificationsModule(Module):
    name = "auto_notifications"
    version = "2.0.0"

    def matches(self, ticket: dict) -> bool:
        tid = _topic_id(ticket)
        email = _reporter_email(ticket)
        # RingCentral is explicitly excluded — real customer tickets
        if tid == _TOPIC_RINGCENTRAL:
            return False
        return (
            tid in (_TOPIC_REVV, _TOPIC_AUTO_NOTIF)
            or email.endswith("@assurant.com")
        )

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        tid = _topic_id(ticket)
        email = _reporter_email(ticket)
        status = _status(ticket)

        # ── Classify into a profile ──────────────────────────────────────────
        if tid == _TOPIC_REVV:
            profile = "revv_error"
            confidence = 0.90
            diagnosis = "Automated Revv sync error notification. No customer impact."
            evidence_value = f"{tid} (Revv Error Report)"
            actions = _build_actions(
                topic_value_id=_TOPIC_REVV,
                severity_id=_SEV_3,
                support_level_id=_LEVEL_1,
                type_of_work_id=_SUPPORT,
                root_cause_id=_ROOT_UNKNOWN,
                assignee_email=_ASSIGNEE_TIM,
                resolution="Done",
            )

        elif tid == _TOPIC_AUTO_NOTIF:
            profile = "auto_notif"
            confidence = 0.76
            diagnosis = "Automated system notification. Dismissed per historical pattern (76%)."
            evidence_value = f"{tid} (Automatic Notifications)"
            actions = _build_actions(
                topic_value_id=_TOPIC_AUTO_NOTIF,
                severity_id=_SEV_3,
                support_level_id=_LEVEL_1,
                type_of_work_id=None,   # 82% of historical auto_notif tickets had no type_of_work
                root_cause_id=_ROOT_UNKNOWN,
                assignee_email=_ASSIGNEE_JOHN,
                resolution="Dismissed",
            )

        else:
            # Assurant email sender
            profile = "assurant_email"
            confidence = 0.96
            diagnosis = "Automated Assurant claims notification. Resolved per historical pattern (96%)."
            evidence_value = f"reporter_email={email} (@assurant.com)"
            actions = _build_actions(
                topic_value_id=None,    # topic may vary; don't overwrite
                severity_id=_SEV_3,
                support_level_id=_LEVEL_2,
                type_of_work_id=_SUPPORT,
                root_cause_id=_ROOT_INTEG,
                assignee_email=_ASSIGNEE_IAN,
                resolution="Fixed / Completed",
            )

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=diagnosis,
            evidence=[
                {"source": "notification_profile", "value": profile},
                {"source": "match_signal", "value": evidence_value},
            ],
            revalidation_targets=[
                RevalidationTarget(
                    type="jira_field",
                    snapshot={"field": "status", "value": status},
                ),
            ],
            actions=actions,
            module_confidence=confidence,
        )

