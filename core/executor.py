"""
Executor — Brain 4. Runs after human approval inside the scd-execute Environment.

Flow:
1. Re-validate the proposal against current Jira state (diffs revalidation_targets)
2. If diff detected: post diff comment to GitHub Issue and EXIT (human re-approves)
3. Verify HMAC signature on the proposal
4. Execute each action in step order using JiraWriteClient
5. Append notification log rows where applicable
6. Post execution summary to GitHub Issue and close it
7. Commit notifications/ and state/ back to the repo
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import sys
import yaml
from dataclasses import asdict
from core.jira_clients import JiraReadClient, JiraWriteClient
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget
from core.notification_logs import append_row
from core.github_issues import close_proposal
import core.state as state_store

# Field option lookups for field updates
# These map value_id -> ADF-compatible value shape
_SINGLE_SELECT_FIELDS = {
    "customfield_10170",  # Topic Field
    "customfield_10201",  # Root Cause
    "customfield_10143",  # Type of Work
    "customfield_10186",  # Support Level
    "customfield_10036",  # Severity Level
    "customfield_10142",  # Billing Reference
    "customfield_10144",  # Billing Method
}

# Resolution name -> id mapping (from jira_fields.yaml conceptually)
_RESOLUTION_MAP = {
    "Fixed / Completed": "10000",
    "Dismissed": "10001",
    "Duplicate": "10002",
    "Declined / Canceled": "10003",
    "Done": "10004",
    "Known Error": "10005",
    "Moved to CS": "10006",
    "Not Fixable": "10007",
    "Software failure": "10008",
    "Won't Do": "10009",
    "Works as Designed": "10010",
    "Cannot Reproduce": "10011",
}


def run(
    suggestion: ResolutionSuggestion,
    proposal_issue_number: int,
    hmac_key: str,
) -> None:
    """
    Main entry point. Called by the execute workflow after approval.
    Raises SystemExit(1) if revalidation detects drift or HMAC fails.
    """
    jira_read = JiraReadClient()
    ticket_id = suggestion.ticket_id

    # Step 1 — Re-validate
    drift = _revalidate(suggestion, jira_read)
    if drift:
        drift_report = "\n".join(f"- **{d['target']}**: was `{d['was']}`, now `{d['now']}`" for d in drift)
        close_proposal(
            proposal_issue_number,
            f"⚠️ **Re-validation detected drift** — execution halted.\n\n{drift_report}\n\n"
            f"Please re-trigger **SCD Core — Execute** after reviewing.",
        )
        print(f"[executor] Drift detected on {ticket_id}. Execution halted.")
        sys.exit(1)

    # Step 2 — Verify HMAC
    if hmac_key:
        _verify_hmac(suggestion, hmac_key)

    # Step 3 — Execute actions
    jira_write = JiraWriteClient()
    execution_log: list[str] = []

    for action in sorted(suggestion.actions, key=lambda a: a.step):
        result = _execute_action(action, ticket_id, jira_write)
        execution_log.append(f"Step {action.step} ({action.type}): {result}")
        print(f"[executor] {execution_log[-1]}")

    # Step 4 — Update state
    current_state = state_store.load()
    state_store.mark_processed(current_state, ticket_id, proposal_issue_number)
    state_store.save(current_state)

    # Step 5 — Close the proposal issue
    summary = "\n".join(execution_log)
    close_proposal(
        proposal_issue_number,
        f"✅ **Execution complete** for `{ticket_id}`.\n\n```\n{summary}\n```",
    )
    print(f"[executor] Execution complete for {ticket_id}.")


def _revalidate(suggestion: ResolutionSuggestion, jira: JiraReadClient) -> list[dict]:
    """Check each revalidation target against current Jira state. Returns list of diffs."""
    drifts = []
    ticket = jira.get_issue(suggestion.ticket_id)

    for target in suggestion.revalidation_targets:
        if target.type == "jira_field":
            field = target.snapshot.get("field")
            expected = target.snapshot.get("value")
            if field == "status":
                current = (ticket.get("fields", {}).get("status") or {}).get("name", "")
                if current != expected:
                    drifts.append({"target": f"status", "was": expected, "now": current})
        elif target.type == "jira_comment_count":
            expected_count = target.snapshot.get("count", 0)
            comments = jira.get_comments(suggestion.ticket_id)
            current_count = len(comments)
            if current_count != expected_count:
                drifts.append({
                    "target": "comment_count",
                    "was": expected_count,
                    "now": current_count,
                })

    return drifts


def _verify_hmac(suggestion: ResolutionSuggestion, key: str) -> None:
    stored_sig = suggestion.hmac_signature
    if not stored_sig:
        return  # No signature stored — skip (pre-HMAC proposals)

    payload = json.dumps(asdict(suggestion), sort_keys=True, ensure_ascii=False)
    payload_no_sig = json.loads(payload)
    payload_no_sig["hmac_signature"] = ""
    canonical = json.dumps(payload_no_sig, sort_keys=True, ensure_ascii=False).encode()
    expected = hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, stored_sig):
        raise ValueError(f"HMAC verification failed for {suggestion.ticket_id}. Execution blocked.")


def _execute_action(action: Action, ticket_id: str, jira: JiraWriteClient) -> str:
    t = action.type
    p = action.payload

    if t == "jira_field_update":
        field = p["field"]
        value_id = p.get("value_id")
        if field in _SINGLE_SELECT_FIELDS and value_id:
            value = {"id": str(value_id)}
        else:
            value = p.get("value", value_id)
        jira.update_field(ticket_id, field, value)
        return f"Updated {field} = {value_id or value}"

    elif t == "jira_transition":
        to_name = p.get("to", "")
        resolution = p.get("resolution", "")
        transition_id = _resolve_transition_id(to_name)
        resolution_id = _RESOLUTION_MAP.get(resolution)
        jira.transition(ticket_id, transition_id, resolution_id)
        return f"Transitioned to '{to_name}' with resolution '{resolution}'"

    elif t in ("jira_internal_comment", "jira_public_comment"):
        body_text = p.get("body", "")
        internal = p.get("internal", t == "jira_internal_comment")
        adf_body = _text_to_adf(body_text)
        jira.add_comment(ticket_id, adf_body, internal=internal)
        return f"Posted {'internal' if internal else 'public'} comment ({len(body_text)} chars)"

    elif t == "notification_log_append":
        append_row(action)
        return f"Appended row to log '{p.get('log_file')}'"

    elif t == "sql":
        # SQL is executed by a human — agent only verifies it was included
        return f"SQL action present (human-executed): {p.get('statement', '')[:80]}"

    else:
        return f"UNKNOWN action type '{t}' — skipped"


def _load_transitions() -> dict[str, str]:
    """Load transition name-to-ID mapping from configs/jira_fields.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "jira_fields.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {str(k): str(v) for k, v in (data.get("transitions") or {}).items()}
    except Exception:
        return {}


def _resolve_transition_id(to_name: str) -> str:
    """Map human-readable transition name to Jira transition ID from configs/jira_fields.yaml."""
    # Aliases: modules use names like 'Closed' or 'Resolve'
    _aliases = {
        "closed": "close",
        "resolve": "resolve",
        "resolved": "resolve",
        "in progress": "in_progress",
        "pending": "pending",
        "open": "open",
    }
    transitions = _load_transitions()
    key = _aliases.get(to_name.lower(), to_name.lower())
    tid = transitions.get(key)
    if not tid:
        raise ValueError(
            f"Unknown transition target: '{to_name}'. "
            f"Add it to configs/jira_fields.yaml under 'transitions:'"
        )
    return tid


def _text_to_adf(text: str) -> dict:
    """Convert plain text to Atlassian Document Format (ADF) body."""
    paragraphs = []
    for line in text.split("\n"):
        if line.strip():
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            })
    return {
        "version": 1,
        "type": "doc",
        "content": paragraphs or [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


if __name__ == "__main__":
    import argparse
    from dataclasses import fields as _dc_fields
    from core.github_issues import fetch_proposal_json

    parser = argparse.ArgumentParser(description="SCD Core Executor")
    parser.add_argument("--mode", choices=["execute"], default="execute")
    parser.add_argument("--issue", type=int, required=True, help="Proposal GitHub Issue number")
    parser.add_argument("--ticket", required=True, help="Jira ticket ID — must match proposal")
    args = parser.parse_args()

    proposal_data = fetch_proposal_json(args.issue)

    # Deserialize ResolutionSuggestion from the embedded JSON
    known = {f.name for f in _dc_fields(ResolutionSuggestion)}
    flat = {k: v for k, v in proposal_data.items() if k in known and k not in ("actions", "revalidation_targets")}
    suggestion = ResolutionSuggestion(**flat)
    suggestion.actions = [Action(**a) for a in proposal_data.get("actions", [])]
    suggestion.revalidation_targets = [
        RevalidationTarget(**t) for t in proposal_data.get("revalidation_targets", [])
    ]

    # Safety: ticket must match the input argument
    if suggestion.ticket_id != args.ticket:
        print(
            f"[executor] ABORT: Proposal ticket_id '{suggestion.ticket_id}' "
            f"!= --ticket '{args.ticket}'. Refusing to execute."
        )
        sys.exit(1)

    hmac_key = os.environ.get("PROPOSAL_HMAC_KEY", "")
    run(suggestion, args.issue, hmac_key)
