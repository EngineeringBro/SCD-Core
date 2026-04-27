"""
Validator — Brain 3. GPT 5.4 independently reviews the Brain 1 proposal and
produces its own refined output. This refined output is what the human sees
and approves — NOT the raw Brain 1 output.

Brain 3 can:
  - Rewrite the diagnosis with more precision
  - Flag individual actions as risky or incorrect
  - Add missing context
  - Mark the overall proposal as APPROVED / FLAGGED / NEEDS_REVISION

The human always reviews Brain 3's output. Brain 1 output is shown as
reference only (collapsed in the GitHub Issue).
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from core.resolution_suggestion import ResolutionSuggestion
from dataclasses import asdict

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


VALIDATOR_MODEL = "gpt-5.4"
MAX_TOKENS = 2048

# GitHub Copilot Business endpoint (confirmed from subscription API).
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"


@dataclass
class ActionAssessment:
    step: int
    status: str         # OK | RISKY | WRONG
    note: str           # explanation if not OK


@dataclass
class ValidatorResult:
    verdict: str                            # APPROVED | FLAGGED | NEEDS_REVISION
    refined_diagnosis: str                  # GPT 5.4's own diagnosis — shown as primary
    action_assessments: list[ActionAssessment]
    overall_notes: str                      # Final note to the human reviewer
    raw_response: str                       # Full model response for traceability
    reasoning: str = ""                    # step-by-step chain-of-thought from the model
    skipped: bool = False


def review(suggestion: ResolutionSuggestion) -> ValidatorResult:
    """
    GPT 5.4 independently reviews the proposal and produces its own output.
    The human reviews THIS output, not Brain 1's raw output.
    """
    gh_token = os.environ.get("COPILOT_TOKEN", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")

    if OpenAI is None:
        return _skipped(suggestion, "openai package not installed")

    if openai_key:
        client = OpenAI(api_key=openai_key)
    elif gh_token:
        client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)
    else:
        return _skipped(suggestion, "neither OPENAI_API_KEY nor COPILOT_TOKEN available")

    prompt = _build_prompt(suggestion)

    try:
        response = client.chat.completions.create(
            model=VALIDATOR_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Brain 3 of SCD Core, an autonomous support-ticket resolution system. "
                        "Brain 1 (Claude) has analyzed a ticket and proposed a resolution. "
                        "Your job is to independently review that proposal and produce your own "
                        "refined output. Think step-by-step in the 'reasoning' field before writing "
                        "your verdict. This is what the human reviewer will see and act on. "
                        "Be precise. Flag anything risky. Output JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=MAX_TOKENS,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        print(f"[validator] Brain3 API call failed ({type(exc).__name__}: {exc}) — skipping")
        return _skipped(suggestion, f"Brain3 API error: {type(exc).__name__}")

    raw = response.choices[0].message.content or ""

    try:
        parsed = json.loads(raw)
        assessments = [
            ActionAssessment(
                step=a.get("step", i + 1),
                status=a.get("status", "OK").upper(),
                note=a.get("note", ""),
            )
            for i, a in enumerate(parsed.get("action_assessments", []))
        ]
        return ValidatorResult(
            verdict=parsed.get("verdict", "FLAGGED").upper(),
            refined_diagnosis=parsed.get("refined_diagnosis", suggestion.diagnosis),
            action_assessments=assessments,
            overall_notes=parsed.get("overall_notes", ""),
            raw_response=raw,
            reasoning=parsed.get("reasoning", ""),
        )
    except (json.JSONDecodeError, KeyError):
        return ValidatorResult(
            verdict="FLAGGED",
            refined_diagnosis=suggestion.diagnosis,
            action_assessments=[],
            overall_notes=f"Brain 3 returned unparseable response: {raw[:300]}",
            raw_response=raw,
        )


def _skipped(suggestion: ResolutionSuggestion, reason: str) -> ValidatorResult:
    return ValidatorResult(
        verdict="SKIPPED",
        refined_diagnosis=suggestion.diagnosis,
        action_assessments=[],
        overall_notes=f"Brain 3 skipped — {reason}. Human must review Brain 1 output directly.",
        raw_response="",
        skipped=True,
    )


def _build_prompt(suggestion: ResolutionSuggestion) -> str:
    data = asdict(suggestion)
    data.pop("hmac_signature", None)

    actions_summary = "\n".join(
        f"  Step {a.step}: {a.type} — {json.dumps(a.payload, ensure_ascii=False)[:1500]}"
        for a in suggestion.actions
    )

    return f"""Brain 1 (Claude Sonnet 4.6) proposed the following resolution for a Jira support ticket.

TICKET: {suggestion.ticket_id}
MODULE: {suggestion.module} v{suggestion.module_version}
BRAIN 1 DIAGNOSIS: {suggestion.diagnosis}
BRAIN 1 CONFIDENCE: {suggestion.module_confidence}

PROPOSED ACTIONS:
{actions_summary}

EVIDENCE:
{json.dumps(suggestion.evidence, ensure_ascii=False)}

--- JIRA FIELD REFERENCE (authoritative — do NOT flag these as unknown) ---
customfield_10170 (Topic):
  10446 = Transaction Errors
  10438 = Spam
  10361 = Claims
  10354 = Billing
  10414 = Printing
  10439 = Square Integration
  10390 = Heartland
  10352 = Asurion
  10351 = Assurant
  10404 = Automatic Notifications
  10495 = Azure Notification
  10413 = Pricing Increase
  10427 = RMAs
  10434 = Settings & Configuration Questions
  10494 = Revv Error Report
  10496 = Ring Central Alert
  10405 = Onboarding
  10435 = Signup Issues
  10380 = Google
  10369 = Error - 500
  10366 = Error - 400
  10368 = Error - 403
  10393 = Inventory
  10424 = Reports
  10443 = Ticket/Device Update
  10469 = Re-Open Ticket

customfield_10201 (Root Cause):
  10497 = User Behavior or Input Error
  10498 = Config or Workflow Discrepancy
  10499 = Integration or Sync Error
  10500 = Software Bug
  10501 = Unknown
  10764 = Infrastructure

customfield_10158 (Product):
  10336 = RepairQ OTS
  10340 = RepairQ Enterprise
  10334 = ServiceManager
  10335 = ServiceNetwork

Resolution options: Fixed/Completed, Dismissed, Duplicate, Declined/Canceled,
Done, Known Error, Moved to CS, Not Fixable, Software failure, Won't Do,
Works as Designed, Cannot Reproduce
--- END FIELD REFERENCE ---

IMPORTANT: Your role is to catch REAL threats — wrong module choice, dangerous SQL,
actions that could corrupt live data, or missing critical identifiers. Do NOT flag
actions simply because you are unfamiliar with a field value — use the reference above.
Do NOT flag "URL not found" parentheticals in comment bodies as a risk — client
identification via reporter email or org field is a valid, trusted fallback.

Your task: independently assess this proposal and produce your own refined output.

Respond with JSON in this exact shape:
{{
  "reasoning": "Step-by-step thinking. Walk through: is the module correct for this ticket? Is each action safe? Are there real risks (wrong module, dangerous data change, missing critical identifiers)? 4-10 sentences.",
  "verdict": "APPROVED" | "FLAGGED" | "NEEDS_REVISION",
  "refined_diagnosis": "Your own 1-3 sentence diagnosis. Should be more precise than Brain 1's if possible.",
  "action_assessments": [
    {{"step": 1, "status": "OK" | "RISKY" | "WRONG", "note": "brief note, empty string if OK"}},
    ...one entry per proposed action...
  ],
  "overall_notes": "Final note to the human reviewer. What to watch for, what was changed, or why it's safe to approve."
}}

reasoning: your thinking process — written before the verdict, not a summary of it.
Verdict meanings:
- APPROVED: safe to execute as-is
- FLAGGED: do not execute — something is clearly wrong (wrong module, dangerous action)
- NEEDS_REVISION: a specific action has a genuine risk that needs human adjustment
"""

