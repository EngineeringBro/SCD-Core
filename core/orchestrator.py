"""
Orchestrator — Brain 1 top-level flow.

Run this file directly:
    python -m core.orchestrator

Flow:
  1. Load scan state
  2. Search Jira for open SCD tickets (delta since last_run)
  3. For each unprocessed ticket:
     a. Route to a module
     b. module.run() -> ResolutionSuggestion
     c. Gatekeeper.check() -> GateResult
     d. If DENY: log and skip
     e. Validator.review() -> ValidatorResult
     f. Post proposal as GitHub Issue
     g. mark_processed in state
  4. Save state
"""
from __future__ import annotations
import hashlib
import hmac as _hmac
import json
import os
import sys
from dataclasses import asdict
from core.jira_clients import JiraReadClient
from core.router import load_registry, discover_modules, route
from core import gatekeeper, state as state_store
from core.analyzer import analyze as brain1_analyze
from core.validator import review as validator_review
from core.github_issues import post_proposal, is_issue_closed
from core.learning_store import get_guidance_text

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
    registry = load_registry()
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

        module = route(ticket, registry, module_map)
        if module is None:
            print(f"[orchestrator] {ticket_id}: no module matched — skipping")
            continue

        print(f"[orchestrator] {ticket_id}: routed to '{module.name}'")

        try:
            suggestion = module.run(ticket, jira)
        except Exception as exc:
            print(f"[orchestrator] {ticket_id}: module.run() failed — {exc}")
            continue

        # Stamp topic on every suggestion regardless of module, so github_issues.py
        # and the learning store can surface/save it correctly.
        ticket_topic = (ticket.get("fields", {}).get("customfield_10170") or {}).get("value", "Unknown")
        suggestion.sub_agent_attribution.setdefault("topic", ticket_topic)

        # Load any human-verified guidance for this topic (applies to ALL modules)
        learned_guidance = get_guidance_text(ticket_topic)
        if learned_guidance:
            print(f"[orchestrator] {ticket_id}: human guidance found for topic '{ticket_topic}' — injecting into Brain 1")

        # Brain 1 — Claude Sonnet 4.6 enriches the diagnosis
        analysis = brain1_analyze(ticket, suggestion, learned_guidance=learned_guidance)
        suggestion.diagnosis = analysis.enriched_diagnosis
        suggestion.module_confidence = round(
            min(1.0, max(0.0, suggestion.module_confidence + analysis.confidence_adjustment)), 2
        )
        if analysis.flags:
            print(f"[orchestrator] {ticket_id}: Brain1 flags — {analysis.flags}")
        print(f"[orchestrator] {ticket_id}: Brain1 analysis {'applied' if not analysis.skipped else 'skipped'}")

        # Guidance confidence floor — when a human has already told us exactly what to do
        # for this topic, the module's raw score is irrelevant; we trust the guidance.
        # Only apply the floor if Brain 1 didn’t raise blocking flags (flags that indicate
        # the guidance contradicts the proposed actions or something is critically wrong).
        GUIDANCE_CONFIDENCE_FLOOR = 0.85
        if learned_guidance and not analysis.skipped:
            blocking = [f for f in analysis.flags if any(w in f.upper() for w in ("WRONG", "CRITICAL", "CONTRADICT", "MISMATCH"))]
            if not blocking:
                if suggestion.module_confidence < GUIDANCE_CONFIDENCE_FLOOR:
                    print(
                        f"[orchestrator] {ticket_id}: guidance floor applied — "
                        f"{suggestion.module_confidence:.0%} → {GUIDANCE_CONFIDENCE_FLOOR:.0%}"
                    )
                    suggestion.module_confidence = GUIDANCE_CONFIDENCE_FLOOR
            else:
                print(f"[orchestrator] {ticket_id}: guidance floor skipped — blocking flags: {blocking}")

        # Brain 2 — Gatekeeper (passes source ticket_id to guard against prompt injection)
        gate_result = gatekeeper.check(suggestion, source_ticket_id=ticket_id)
        print(f"[orchestrator] {ticket_id}: gatekeeper => {gate_result.verdict}")

        if not gate_result.passed:
            failures = "; ".join(f"{c.rule_id}: {c.reason}" for c in gate_result.failures)
            print(f"[orchestrator] {ticket_id}: DENIED — {failures}")
            skipped_gate += 1
            state_store.mark_processed(current_state, ticket_id, proposal_issue=None)
            continue

        gate_summary = f"ALLOW ({len(gate_result.checks)} checks passed)"

        # Brain 3 — GPT 5.4 independently reviews and produces refined output
        validator_result = validator_review(suggestion)
        print(f"[orchestrator] {ticket_id}: Brain3 => {validator_result.verdict}")

        # Sign the proposal with HMAC before posting so executor can verify it
        hmac_key = os.environ.get("PROPOSAL_HMAC_KEY", "")
        if hmac_key:
            _sign_suggestion(suggestion, hmac_key)

        # Human reviews Brain 3's output — pass the full result
        issue_number = post_proposal(suggestion, gate_summary, validator_result)
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
    with open("run-trace/summary.json", "w") as f:
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
