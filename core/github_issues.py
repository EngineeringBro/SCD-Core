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
REPO_ENV_VAR = "GITHUB_REPOSITORY"   # set automatically by GitHub Actions (owner/repo)
GH_TOKEN_VAR = "GH_TOKEN"


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
    validator_notes: str,
) -> int:
    """
    Post the proposal as a GitHub Issue.
    Returns the created issue number.
    """
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/issues"

    title = f"[SCD Proposal] {suggestion.ticket_id} — {suggestion.module} v{suggestion.module_version}"

    body = _build_body(suggestion, gate_summary, validator_notes)

    payload = {
        "title": title,
        "body": body,
        "labels": LABELS,
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


def _build_body(
    suggestion: ResolutionSuggestion,
    gate_summary: str,
    validator_notes: str,
) -> str:
    actions_md = "\n".join(
        f"  {a.step}. **{a.type}** — `{json.dumps(a.payload, ensure_ascii=False)}`"
        for a in suggestion.actions
    )

    evidence_md = "\n".join(
        f"  - `{e.get('source', '?')}`: {e.get('value', '')}"
        for e in suggestion.evidence
    )

    revalidation_md = "\n".join(
        f"  - `{r.type}`: {json.dumps(r.snapshot)}"
        for r in suggestion.revalidation_targets
    )

    proposal_json = json.dumps(asdict(suggestion), indent=2, ensure_ascii=False)

    return f"""## SCD Core Proposal

| Field | Value |
|-------|-------|
| **Ticket** | {suggestion.ticket_id} |
| **Module** | `{suggestion.module}` v{suggestion.module_version} |
| **Confidence** | {suggestion.module_confidence:.0%} |
| **HMAC** | `{suggestion.hmac_signature[:16]}…` |

### Diagnosis
{suggestion.diagnosis}

### Evidence
{evidence_md}

### Proposed Actions
{actions_md}

### Revalidation Targets
{revalidation_md}

### Gatekeeper
{gate_summary}

### Validator Notes
{validator_notes}

---
### Approval Instructions
To approve and execute, trigger the **SCD Core — Execute** workflow with:
- `proposal_issue_number`: this issue number
- `ticket_id`: `{suggestion.ticket_id}`

**Re-validation runs automatically** at execution time. If the ticket state has changed,
you will see a diff posted here before execution proceeds.

<details>
<summary>Raw Proposal JSON</summary>

```json
{proposal_json}
```
</details>
"""
