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
    "jira_internal_note",
    "jira_public_comment",
    "jira_transition",
    "jira_field_update",
    "jira_assign",
    "jira_log_time",
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


def check(suggestion: ResolutionSuggestion, source_ticket_id: str | None = None) -> GateResult:
    checks: list[GateCheck] = []

    # Rule: ticket_id in the suggestion must match the ticket being processed
    # Prevents prompt-injected suggestions from targeting a different ticket
    if source_ticket_id is not None:
        checks.append(GateCheck(
            rule_id="proposal.ticket_id_matches_source",
            passed=suggestion.ticket_id == source_ticket_id,
            reason=(
                f"Proposal ticket_id '{suggestion.ticket_id}' does not match "
                f"source ticket '{source_ticket_id}' — possible prompt injection."
            ) if suggestion.ticket_id != source_ticket_id else "",
        ))

    # Rule: action count must be reasonable (injection guard)
    checks.append(GateCheck(
        rule_id="proposal.action_count_reasonable",
        passed=len(suggestion.actions) <= 10,
        reason=f"Proposal has {len(suggestion.actions)} actions — exceeds maximum of 10." if len(suggestion.actions) > 10 else "",
    ))

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
        if suggestion.module == "orphaned_transaction":
            _allowed_comments = {
                "Hello,\n\nWe have added the transaction. Let us know if you need anything else!",
                "Hello,\n\nWe have added the transactions. Let us know if you need anything else!",
            }
            checks.append(GateCheck(
                rule_id=f"step{action.step}.orphaned_tx_public_comment_exact",
                passed=body in _allowed_comments,
                reason="Public comment body does not match the predefined orphaned_transaction template." if body not in _allowed_comments else "",
            ))
    if action.type == "jira_internal_note":
        body = action.payload.get("body", "")
        if suggestion.module == "orphaned_transaction":
            _exact_note = "This ticket was resolved using my AI Agent"
            checks.append(GateCheck(
                rule_id=f"step{action.step}.orphaned_tx_internal_note_exact",
                passed=body == _exact_note,
                reason=f"Internal note body must be exactly: '{_exact_note}'" if body != _exact_note else "",
            ))
        else:
            checks.append(GateCheck(
                rule_id=f"step{action.step}.internal_note_nonempty",
                passed=bool(body.strip()),
                reason="Internal note body is empty." if not body.strip() else "",
            ))

    return checks


def _check_sql(action: Action, suggestion: ResolutionSuggestion) -> list[GateCheck]:
    checks: list[GateCheck] = []
    stmt = action.payload.get("statement", "").upper()
    rollback = action.payload.get("rollback", "")
    is_orphaned_tx = suggestion.module == "orphaned_transaction"

    # orphaned_transaction uses INSERT (no WHERE needed) — waive this check
    if not is_orphaned_tx:
        checks.append(GateCheck(
            rule_id=f"step{action.step}.sql_has_where",
            passed="WHERE" in stmt,
            reason="SQL statement missing WHERE clause." if "WHERE" not in stmt else "",
        ))

    # rollback waived for orphaned_transaction — execute is manual-only (human approval required)
    if not is_orphaned_tx:
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
