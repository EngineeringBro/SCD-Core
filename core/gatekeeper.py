"""
Gatekeeper — Brain 2. Pure Python. No LLM. No network calls.
Applies hard safety rules to every ResolutionSuggestion before it reaches the validator.
"""
from __future__ import annotations
from dataclasses import dataclass
from core.resolution_suggestion import ResolutionSuggestion, Action

# Terminal claim/warranty statuses — no modifications allowed
TERMINAL_STATUSES = frozenset([
    "fulfilled", "declined", "rejected", "cancelled",
    "ntf", "returned", "rejected_returned", "whole_unit_replacement",
])

# Action types that are always safe (no write risk)
SAFE_ACTION_TYPES = frozenset([
    "jira_internal_comment",
    "jira_public_comment",
    "jira_transition",
    "jira_field_update",
    "notification_log_append",
])


@dataclass
class GateCheck:
    rule_id: str
    passed: bool
    reason: str = ""


@dataclass
class GateResult:
    verdict: str        # ALLOW | DENY
    checks: list[GateCheck]

    @property
    def passed(self) -> bool:
        return self.verdict == "ALLOW"

    @property
    def failures(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.passed]


def check(suggestion: ResolutionSuggestion) -> GateResult:
    checks: list[GateCheck] = []

    for action in suggestion.actions:
        checks.extend(_check_action(action, suggestion))

    verdict = "ALLOW" if all(c.passed for c in checks) else "DENY"
    return GateResult(verdict=verdict, checks=checks)


def _check_action(action: Action, suggestion: ResolutionSuggestion) -> list[GateCheck]:
    checks: list[GateCheck] = []

    # Rule: action type must be registered
    checks.append(GateCheck(
        rule_id=f"step{action.step}.known_action_type",
        passed=action.type in SAFE_ACTION_TYPES or action.type == "sql",
        reason=f"Unknown action type: {action.type}" if action.type not in SAFE_ACTION_TYPES | {"sql"} else "",
    ))

    if action.type == "sql":
        checks.extend(_check_sql(action, suggestion))

    if action.type == "notification_log_append":
        checks.extend(_check_log_append(action))

    if action.type == "jira_public_comment":
        body = action.payload.get("body", "")
        checks.append(GateCheck(
            rule_id=f"step{action.step}.no_unfilled_placeholders",
            passed="{" not in body,
            reason="Public comment body contains unfilled placeholders." if "{" in body else "",
        ))

    return checks


def _check_sql(action: Action, suggestion: ResolutionSuggestion) -> list[GateCheck]:
    checks: list[GateCheck] = []
    stmt = action.payload.get("statement", "").upper()
    rollback = action.payload.get("rollback", "")

    checks.append(GateCheck(
        rule_id=f"step{action.step}.sql_has_where",
        passed="WHERE" in stmt,
        reason="SQL statement missing WHERE clause." if "WHERE" not in stmt else "",
    ))
    checks.append(GateCheck(
        rule_id=f"step{action.step}.sql_has_rollback",
        passed=bool(rollback.strip()),
        reason="SQL action missing rollback statement." if not rollback.strip() else "",
    ))
    checks.append(GateCheck(
        rule_id=f"step{action.step}.sql_no_drop",
        passed="DROP" not in stmt and "TRUNCATE" not in stmt,
        reason="SQL contains DROP or TRUNCATE — forbidden." if "DROP" in stmt or "TRUNCATE" in stmt else "",
    ))

    # Check revalidation_targets for terminal status
    for target in suggestion.revalidation_targets:
        if target.type == "db_row":
            status = target.snapshot.get("claim_status") or target.snapshot.get("status", "")
            if status.lower() in TERMINAL_STATUSES:
                checks.append(GateCheck(
                    rule_id=f"step{action.step}.no_terminal_status_row",
                    passed=False,
                    reason=f"Target row has terminal status '{status}' — no modifications allowed.",
                ))

    return checks


def _check_log_append(action: Action) -> list[GateCheck]:
    checks: list[GateCheck] = []
    log_file = action.payload.get("log_file", "")
    row = action.payload.get("row", {})

    checks.append(GateCheck(
        rule_id=f"step{action.step}.log_file_specified",
        passed=bool(log_file),
        reason="notification_log_append missing log_file." if not log_file else "",
    ))
    checks.append(GateCheck(
        rule_id=f"step{action.step}.log_row_not_empty",
        passed=bool(row),
        reason="notification_log_append missing row data." if not row else "",
    ))

    return checks
