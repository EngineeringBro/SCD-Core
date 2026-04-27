"""
GitHub Issues — posts a ResolutionSuggestion as a GitHub Issue for human review.
Each proposal becomes one Issue with structured body, labels, and the HMAC-signed
proposal JSON attached as a code block.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.parse
from core.resolution_suggestion import ResolutionSuggestion
from dataclasses import asdict


LABELS = ["scd-proposal", "awaiting-approval"]
GUIDANCE_LABEL = "scd-guidance-needed"   # added when module_confidence < 0.9
CAPTURED_LABEL = "scd-guidance-captured"  # applied by the learn workflow after capture
MODULE_NEEDED_LABEL = "scd-module-needed"  # posted by orchestrator when module needs local run
MODULE_COMPLETE_LABEL = "scd-module-complete"  # posted by localbrain when module is done
CONFIDENCE_THRESHOLD = 0.9              # below this → ask human for guidance
REPO_ENV_VAR = "GITHUB_REPOSITORY"   # set automatically by GitHub Actions (owner/repo)
GH_TOKEN_VAR = "GH_TOKEN"            # auto-injected Actions token — used only for GitHub Issues API


def _headers() -> dict:
    token = os.environ[GH_TOKEN_VAR]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    repo = os.environ.get(REPO_ENV_VAR, "")
    if not repo:
        raise RuntimeError(f"Env var {REPO_ENV_VAR} not set")
    return repo


def post_proposal(
    suggestion: ResolutionSuggestion,
    gate_summary: str,
) -> int:
    """
    Post the action plan as a GitHub Issue for human review.
    Issue body shows SQL to run first, then Jira steps execute will apply.
    Returns the created issue number.
    """
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/issues"

    low_confidence = suggestion.module_confidence < CONFIDENCE_THRESHOLD
    confidence_prefix = "[LOW CONFIDENCE] " if low_confidence else ""
    title = f"{confidence_prefix}SCD Proposal: {suggestion.ticket_id} — {suggestion.module} v{suggestion.module_version}"

    body = _build_body(suggestion, gate_summary, low_confidence=low_confidence)

    labels = list(LABELS)
    if low_confidence:
        labels.append(GUIDANCE_LABEL)

    payload = {
        "title": title,
        "body": body,
        "labels": labels,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        response = json.loads(r.read())
    return response["number"]


def post_module_needed(ticket_id: str, module_name: str, snapshot: dict) -> int:
    """
    Post a trigger issue for modules that require a local run (e.g. orphaned_transaction).
    localbrain.py watches for this label and fires the module locally.
    Returns the created issue number.
    """
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/issues"

    snapshot_json = json.dumps(snapshot, indent=2, ensure_ascii=False)
    title = f"[MODULE NEEDED] {ticket_id} — {module_name}"
    body = (
        f"## Local Module Run Required\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Ticket** | `{ticket_id}` |\n"
        f"| **Module** | `{module_name}` |\n\n"
        f"localbrain.py will pick this up automatically.\n\n"
        f"<details>\n"
        f"<summary>Ticket Snapshot (for module input)</summary>\n\n"
        f"```json\n{snapshot_json}\n```\n"
        f"</details>\n"
    )

    payload = {
        "title": title,
        "body": body,
        "labels": [MODULE_NEEDED_LABEL],
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        response = json.loads(r.read())
    return response["number"]


def close_proposal(issue_number: int, comment: str) -> None:
    """Close a proposal issue with a final comment (used after execution)."""
    repo = _repo()
    # Post comment first
    comment_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    comment_payload = json.dumps({"body": comment}).encode()
    req = urllib.request.Request(comment_url, data=comment_payload, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass

    # Then close the issue
    issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    close_payload = json.dumps({"state": "closed"}).encode()
    req = urllib.request.Request(issue_url, data=close_payload, headers=_headers(), method="PATCH")
    with urllib.request.urlopen(req, timeout=15):
        pass


def is_issue_closed(issue_number: int) -> bool:
    """
    Return True if the GitHub Issue is in 'closed' state.
    Used by the orchestrator to decide whether a previously-processed ticket
    is eligible for a fresh scan (proposal was executed, guidance captured, etc.).
    Returns False on any network error so the ticket stays blocked (safe default).
    """
    try:
        repo = _repo()
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        req = urllib.request.Request(url, headers=_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            issue = json.loads(r.read())
        return issue.get("state") == "closed"
    except Exception:  # noqa: BLE001
        return False


def fetch_proposal_json(issue_number: int) -> dict:
    """
    Fetch a proposal GitHub Issue and extract the embedded ResolutionSuggestion JSON.
    The JSON is stored in the Brain 1 Raw Output <details> block as a ```json code fence.
    """
    import re
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        issue = json.loads(r.read())
    body = issue.get("body", "")
    # Find the last ```json block (Brain 1 raw output is the only JSON block in the body)
    matches = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    if not matches:
        raise ValueError(f"No JSON block found in proposal Issue #{issue_number}")
    return json.loads(matches[-1])


def _build_body(
    suggestion: ResolutionSuggestion,
    gate_summary: str,
    low_confidence: bool = False,
) -> str:
    """Build the GitHub Issue body for a proposal.

    Structure:
    1. Summary table
    2. Diagnosis
    3. SQL section — human runs this first (if any sql actions present)
    4. Jira steps — what Execute will apply
    5. Evidence
    6. Execute instructions
    7. Collapsible: full JSON for executor
    """
    topic_field = (suggestion.sub_agent_attribution or {}).get("topic", "Unknown")

    # Split actions: SQL (human-run) vs Jira (executor-run)
    sql_actions = [a for a in suggestion.actions if a.type == "sql"]
    jira_actions = [a for a in suggestion.actions if a.type != "sql"]

    # SQL section
    if sql_actions:
        sql_blocks = "\n\n".join(
            f"```sql\n{a.payload.get('statement', '(no statement)')}\n```"
            for a in sql_actions
        )
        sql_section = (
            f"## 1️⃣ Run This SQL First\n\n{sql_blocks}\n\n"
            "> Run against RepairQ DB. Confirm it worked before triggering Execute.\n"
        )
    else:
        sql_section = ""

    # Jira steps section
    jira_steps_md = "".join(
        f"- **{a.type}**: `{json.dumps(a.payload, ensure_ascii=False)}`\n"
        for a in jira_actions
    )
    jira_section = f"## 2️⃣ Jira Steps (Execute will apply these)\n\n{jira_steps_md}" if jira_steps_md else ""

    # Evidence
    evidence_md = "\n".join(
        f"  - `{e.get('source', '?')}`: {e.get('value', '')}"
        for e in suggestion.evidence
    )

    # Guidance section for low-confidence tickets
    guidance_section = ""
    if low_confidence:
        guidance_section = (
            f"\n---\n## ⚠️ Guidance Needed — Confidence Below {int(CONFIDENCE_THRESHOLD * 100)}%\n\n"
            f"| Field | Value |\n|-------|-------|\n"
            f"| **Confidence** | {suggestion.module_confidence:.0%} |\n"
            f"| **Topic** | {topic_field} |\n"
            f"| **Ticket** | {suggestion.ticket_id} |\n\n"
            "The agent is not confident enough to act autonomously. "
            "Please reply with the correct resolution approach to train the knowledge store.\n"
        )

    brain1_json = json.dumps(asdict(suggestion), indent=2, ensure_ascii=False)

    return (
        f"| Field | Value |\n|-------|-------|\n"
        f"| **Ticket** | {suggestion.ticket_id} |\n"
        f"| **Module** | `{suggestion.module}` v{suggestion.module_version} |\n"
        f"| **Confidence** | {suggestion.module_confidence:.0%} |\n"
        f"| **Gatekeeper** | {gate_summary} |\n\n"
        f"## Diagnosis\n\n{suggestion.diagnosis}\n\n"
        f"{sql_section}\n"
        f"{jira_section}\n\n"
        f"### Evidence\n{evidence_md}\n\n"
        f"---\n### ✅ To Execute\n"
        f"1. Run the SQL above and confirm it worked\n"
        f"2. Trigger **SCD Core — Execute** with:\n"
        f"   - `proposal_issue_number`: this issue number\n"
        f"   - `ticket_id`: `{suggestion.ticket_id}`\n"
        f"{guidance_section}\n"
        f"<details>\n<summary>Full JSON (for executor)</summary>\n\n"
        f"```json\n{brain1_json}\n```\n</details>\n"
    )
