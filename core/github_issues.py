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
    validator_result,
) -> int:
    """
    Post the proposal as a GitHub Issue.
    The body is built around Brain 3 (GPT 5.4)'s refined output — that is
    what the human reviews and approves. Brain 1 output is shown as reference.
    Returns the created issue number.
    """
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/issues"

    verdict = getattr(validator_result, 'verdict', 'SKIPPED')
    title = f"[{verdict}] SCD Proposal: {suggestion.ticket_id} — {suggestion.module} v{suggestion.module_version}"

    body = _build_body(suggestion, gate_summary, validator_result)

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
    validator_result,
) -> str:
    # Brain 3 output
    verdict = getattr(validator_result, 'verdict', 'SKIPPED')
    refined_diagnosis = getattr(validator_result, 'refined_diagnosis', suggestion.diagnosis)
    overall_notes = getattr(validator_result, 'overall_notes', '')
    action_assessments = getattr(validator_result, 'action_assessments', [])
    skipped = getattr(validator_result, 'skipped', False)

    verdict_emoji = {
        'APPROVED': '✅',
        'FLAGGED': '🚫',
        'NEEDS_REVISION': '⚠️',
        'SKIPPED': '⏭️',
    }.get(verdict, '❓')

    # Build action table with Brain 3 assessments
    assessment_map = {a.step: a for a in action_assessments}
    actions_md = ""
    for a in suggestion.actions:
        assessment = assessment_map.get(a.step)
        status = getattr(assessment, 'status', 'OK') if assessment else '—'
        note = getattr(assessment, 'note', '') if assessment else ''
        status_icon = {'OK': '✅', 'RISKY': '⚠️', 'WRONG': '🚫'}.get(status, '—')
        note_text = f" — {note}" if note else ""
        actions_md += f"  {a.step}. {status_icon} **{a.type}**{note_text}\n  `{json.dumps(a.payload, ensure_ascii=False)}`\n\n"

    evidence_md = "\n".join(
        f"  - `{e.get('source', '?')}`: {e.get('value', '')}"
        for e in suggestion.evidence
    )

    brain1_json = json.dumps(asdict(suggestion), indent=2, ensure_ascii=False)

    brain3_section = (
        f"> ⏭️ Brain 3 was skipped. Review Brain 1 output directly.\n"
        if skipped else
        f"{overall_notes}"
    )

    return f"""## {verdict_emoji} Brain 3 Verdict: {verdict}

> This is Brain 3 (GPT 5.4)'s independent assessment. **This is what you are approving.**

| Field | Value |
|-------|-------|
| **Ticket** | {suggestion.ticket_id} |
| **Module** | `{suggestion.module}` v{suggestion.module_version} |
| **Brain 1 Confidence** | {suggestion.module_confidence:.0%} |
| **Gatekeeper** | {gate_summary} |

### Brain 3 Diagnosis
{refined_diagnosis}

### Brain 3 Action Review
{actions_md}
### Brain 3 Notes to Reviewer
{brain3_section}

### Evidence
{evidence_md}

---
### ✅ To Approve & Execute
Trigger **SCD Core — Execute** with:
- `proposal_issue_number`: this issue number
- `ticket_id`: `{suggestion.ticket_id}`

Re-validation runs automatically at execution time.

<details>
<summary>Brain 1 Raw Output (reference)</summary>

```json
{brain1_json}
```
</details>
"""
