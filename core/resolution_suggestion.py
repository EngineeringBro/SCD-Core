"""
ResolutionSuggestion — the uniform output shape every module must produce.
All brains (Gatekeeper, Validator, Executor) operate on this schema.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    step: int
    type: str           # sql | jira_internal_comment | jira_public_comment |
                        # jira_transition | jira_field_update | notification_log_append
    payload: dict       # type-specific fields (statement, rollback, body, to, etc.)


@dataclass
class RevalidationTarget:
    type: str           # db_row | jira_field | jira_comment_count
    snapshot: dict      # value at Brain-1 scan time — executor diffs against current


@dataclass
class ResolutionSuggestion:
    ticket_id: str
    module: str
    module_version: str
    diagnosis: str
    evidence: list[dict]
    revalidation_targets: list[RevalidationTarget]
    actions: list[Action]
    module_confidence: float
    module_notes: str = ""
    sub_agent_attribution: dict = field(default_factory=dict)
    hmac_signature: str = ""    # filled by Gatekeeper after PASS
