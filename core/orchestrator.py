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
from core.jira_fetcher import JiraReadClient
from core.registry import discover_modules
from core import gatekeeper, state as state_store
from core.router import classify as brain0_classify
from core.resolver import give_proposal, post_module_needed, post_hold_notice, is_issue_closed
from core.learner import get_module_override

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
            # Step 2: Router — classify ticket into a module name (includes hold detection)
            module_name = brain0_classify(ticket)

            if module_name == "hold":
                # Router detected a human instruction in comments to leave ticket alone.
                # Check if a previous hold notice for this ticket was overridden with 'scd-core: proceed'.
                prev_entry = current_state.get("processed_tickets", {}).get(ticket_id, {})
                prev_hold_issue = prev_entry.get("hold_issue")
                proceed_override = False
                if prev_hold_issue:
                    # Check if anyone commented 'scd-core: proceed' on the hold issue
                    proceed_override = _hold_issue_has_proceed(prev_hold_issue)

                if proceed_override:
                    print(f"[orchestrator] {ticket_id}: hold overridden via 'scd-core: proceed' on issue #{prev_hold_issue} — continuing")
                else:
                    # Post hold notice if we haven't already
                    if not prev_hold_issue:
                        hold_issue = post_hold_notice(ticket_id, "")
                        print(f"[orchestrator] {ticket_id}: hold notice posted as issue #{hold_issue}")
                        current_state.setdefault("processed_tickets", {})[ticket_id] = {"hold_issue": hold_issue}
                    else:
                        print(f"[orchestrator] {ticket_id}: hold notice already posted as issue #{prev_hold_issue} — skipping")
                    skipped_gate += 1
                    continue

                # Override granted — re-classify for actual module
                module_name = brain0_classify(ticket)
                if module_name == "hold":
                    module_name = "general"  # fallback if router still says hold after override

            module = module_map.get(module_name)
            if module is None:
                print(f"[orchestrator] {ticket_id}: Router returned '{module_name}' but module not loaded — skipping")
                continue
            print(f"[orchestrator] {ticket_id}: Router → '{module.name}'")

        # If this module requires local Playwright session, post trigger issue and stop
        if module.needs_local_run:
            snapshot = {"ticket": ticket, "module": module.name}
            trigger_issue = post_module_needed(ticket_id, module.name, snapshot)
            print(f"[orchestrator] {ticket_id}: needs_local_run — posted local-brain-caller issue #{trigger_issue}")
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

        issue_number = give_proposal(suggestion, gate_summary)
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


def _hold_issue_has_proceed(issue_number: int) -> bool:
    """Check if the hold notice GitHub Issue has a 'scd-core: proceed' comment."""
    import urllib.request
    repo = os.environ.get("GITHUB_REPO", os.environ.get("GITHUB_REPOSITORY", ""))
    token = os.environ.get("GH_TOKEN", "")
    if not repo or not token:
        return False
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=50"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            comments = json.loads(r.read())
        return any("scd-core: proceed" in (c.get("body") or "").lower() for c in comments)
    except OSError:
        return False


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
