"""
Orchestrator — top-level pipeline.

Run this file directly:
    python -m core.orchestrator

Flow:
  1. Load scan state
  2. Fetch ticket(s) from Jira (Fetcher)
  3. For each unprocessed ticket:
     a. Brain 0 classifies → module name (Router)
     b. If module.needs_local_run:
          → post scd-module-needed GitHub Issue with snapshot JSON
          → mark pending, skip to next ticket
          → localbrain.py picks it up locally and posts back scd-module-complete
     c. Otherwise: module.run() → ResolutionSuggestion
     d. Gatekeeper.check() → GateResult
     e. If DENY: log and skip
     f. Post proposal as GitHub Issue (SQL + Jira steps clearly separated)
     g. mark_processed in state
  4. Save state
"""
from __future__ import annotations
import hashlib
import hmac as _hmac
import json
import os
from dataclasses import asdict
from core.jira_clients import JiraReadClient
from core.registry import discover_modules
from core import gatekeeper, state as state_store
from core.router import classify as brain0_classify
from core.resolver import post_proposal, post_module_needed, is_issue_closed
from core.learning_store import get_module_override

# JQL to find open SCD tickets
JQL_BASE = (
    'project = SCD AND status != Closed AND status != Resolved '
    'AND assignee is EMPTY '
    'ORDER BY created ASC'
)

FIELDS = [
    "summary", "description", "status", "assignee", "reporter",
    "comment", "updated", "created",
    "customfield_10170",  # Topic Field
    "customfield_10201",  # Root Cause
    "customfield_10158",  # Product
    "customfield_10002",  # Organizations
]


def _sign_suggestion(suggestion, key: str) -> None:
    """Compute HMAC-SHA256 of the suggestion and store it in suggestion.hmac_signature."""
    payload = json.dumps(asdict(suggestion), sort_keys=True, ensure_ascii=False)
    payload_no_sig = json.loads(payload)
    payload_no_sig["hmac_signature"] = ""
    canonical = json.dumps(payload_no_sig, sort_keys=True, ensure_ascii=False).encode()
    suggestion.hmac_signature = _hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()


def run() -> None:
    print("[orchestrator] Starting scan")

    jira = JiraReadClient()
    current_state = state_store.load()
    module_map = discover_modules()

    print(f"[orchestrator] Modules loaded: {list(module_map.keys())}")

    # Single-ticket mode: fetch directly by key (bypasses JQL search index — more reliable for JSM)
    scan_ticket_id = os.environ.get("SCAN_TICKET_ID", "").strip()
    if scan_ticket_id:
        print(f"[orchestrator] Single-ticket mode: {scan_ticket_id}")
        raw = jira.get_issue(scan_ticket_id, fields=FIELDS)
        tickets = [raw]
    else:
        last_run = current_state.get("last_run")
        jql = JQL_BASE
        if last_run:
            date_part = last_run[:10].replace("-", "/")
            jql = (
                f'project = SCD AND status != Closed AND status != Resolved '
                f'AND assignee is EMPTY AND updated >= "{date_part}" '
                f'ORDER BY created ASC'
            )
        print(f"[orchestrator] JQL: {jql}")
        tickets = jira.search(jql, fields=FIELDS, max_results=100)

    print(f"[orchestrator] {len(tickets)} ticket(s) returned")

    proposals_posted = 0
    skipped_gate = 0
    skipped_state = 0

    for ticket in tickets:
        ticket_id = ticket["key"]

        if not state_store.ticket_needs_processing(current_state, ticket):
            # If the previous proposal Issue was closed (executed, guidance captured, dismissed)
            # the ticket is eligible for a fresh scan — clear it from state and continue.
            prev_entry = current_state.get("processed_tickets", {}).get(ticket_id, {})
            prev_issue = prev_entry.get("proposal_issue")
            if prev_issue and is_issue_closed(prev_issue):
                print(f"[orchestrator] {ticket_id}: previous issue #{prev_issue} is closed — clearing state, reprocessing")
                current_state.get("processed_tickets", {}).pop(ticket_id, None)
            else:
                print(f"[orchestrator] {ticket_id}: already processed, skipping")
                skipped_state += 1
                continue

        # Check if a human has instructed a specific module for this ticket
        ticket_topic = (ticket.get("fields", {}).get("customfield_10170") or {}).get("value", "Unknown")
        forced_module_name = get_module_override(ticket_topic, ticket_id)
        if forced_module_name and forced_module_name in module_map:
            module = module_map[forced_module_name]
            print(f"[orchestrator] {ticket_id}: human override — force-routed to '{module.name}'")
        else:
            # Step 2: Router — classify ticket into a module name
            module_name = brain0_classify(ticket)
            module = module_map.get(module_name)
            if module is None:
                print(f"[orchestrator] {ticket_id}: Router returned '{module_name}' but module not loaded — skipping")
                continue
            print(f"[orchestrator] {ticket_id}: Router → '{module.name}'")

        # If this module requires local Playwright session, post trigger issue and stop
        if module.needs_local_run:
            snapshot = {"ticket": ticket, "module": module.name}
            trigger_issue = post_module_needed(ticket_id, module.name, snapshot)
            print(f"[orchestrator] {ticket_id}: needs_local_run — posted scd-module-needed issue #{trigger_issue}")
            state_store.mark_processed(current_state, ticket_id, proposal_issue=None)
            continue

        try:
            suggestion = module.run(ticket, jira)
        except Exception as exc:  # noqa: BLE001
            print(f"[orchestrator] {ticket_id}: module.run() failed — {exc}")
            continue

        # Stamp topic on every suggestion regardless of module, so resolver.py
        # and the learning store can surface/save it correctly.
        ticket_topic = (ticket.get("fields", {}).get("customfield_10170") or {}).get("value", "Unknown")
        suggestion.sub_agent_attribution.setdefault("topic", ticket_topic)

        # Step 4: Gatekeeper — hardcoded safety rules, no LLM
        gate_result = gatekeeper.check(suggestion, source_ticket_id=ticket_id)
        print(f"[orchestrator] {ticket_id}: gatekeeper => {gate_result.verdict}")

        if not gate_result.passed:
            failures = "; ".join(f"{c.rule_id}: {c.reason}" for c in gate_result.failures)
            print(f"[orchestrator] {ticket_id}: DENIED — {failures}")
            skipped_gate += 1
            state_store.mark_processed(current_state, ticket_id, proposal_issue=None)
            continue

        gate_summary = f"ALLOW ({len(gate_result.checks)} checks passed)"

        # Sign the proposal with HMAC before posting so executor can verify it
        hmac_key = os.environ.get("PROPOSAL_HMAC_KEY", "")
        if hmac_key:
            _sign_suggestion(suggestion, hmac_key)

        issue_number = post_proposal(suggestion, gate_summary)
        print(f"[orchestrator] {ticket_id}: proposal posted as GitHub Issue #{issue_number}")

        state_store.mark_processed(current_state, ticket_id, proposal_issue=issue_number)
        proposals_posted += 1

    state_store.update_last_run(current_state)
    state_store.save(current_state)

    print(
        f"[orchestrator] Done. "
        f"proposals_posted={proposals_posted} "
        f"skipped_gate={skipped_gate} "
        f"skipped_state={skipped_state}"
    )

    # Write run summary for GitHub Actions artifact
    summary = {
        "proposals_posted": proposals_posted,
        "skipped_gate": skipped_gate,
        "skipped_state": skipped_state,
        "tickets_scanned": len(tickets),
    }
    os.makedirs("run-trace", exist_ok=True)
    with open("run-trace/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SCD Core Orchestrator")
    parser.add_argument(
        "--mode",
        choices=["scan", "propose"],
        default="propose",
        help="Accepted for workflow compatibility. Full pipeline always runs.",
    )
    parser.parse_args()
    run()
