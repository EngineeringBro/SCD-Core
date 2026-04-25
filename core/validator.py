"""
Validator — Brain 3. Uses GPT to cross-review every ResolutionSuggestion.
Operates after the Gatekeeper ALLOWS a proposal. Adds notes but cannot block
(the human approval step is the true gate). Returns structured validator output.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from core.resolution_suggestion import ResolutionSuggestion
from dataclasses import asdict

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


VALIDATOR_MODEL = "gpt-4o"
MAX_TOKENS = 512


@dataclass
class ValidatorResult:
    verdict: str        # APPROVED | FLAGGED | POLISHED
    notes: str          # Human-readable assessment for the GitHub Issue
    raw_response: str   # Full model response for traceability


def review(suggestion: ResolutionSuggestion) -> ValidatorResult:
    """
    Ask GPT to review the proposal. Returns ValidatorResult.
    If OPENAI_API_KEY is not set, skips review (returns SKIPPED).
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or OpenAI is None:
        return ValidatorResult(
            verdict="SKIPPED",
            notes="Validator skipped — OPENAI_API_KEY not available.",
            raw_response="",
        )

    client = OpenAI(api_key=api_key)

    prompt = _build_prompt(suggestion)

    response = client.chat.completions.create(
        model=VALIDATOR_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict support-ticket resolution auditor. "
                    "You review proposed resolutions to Jira support tickets and identify "
                    "any risks, missing steps, or incorrect field values. "
                    "Be concise. Output JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=MAX_TOKENS,
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""

    try:
        parsed = json.loads(raw)
        verdict = parsed.get("verdict", "FLAGGED").upper()
        notes = parsed.get("notes", raw)
    except json.JSONDecodeError:
        verdict = "FLAGGED"
        notes = f"Validator returned non-JSON response: {raw[:300]}"

    return ValidatorResult(verdict=verdict, notes=notes, raw_response=raw)


def _build_prompt(suggestion: ResolutionSuggestion) -> str:
    data = asdict(suggestion)
    # Remove hmac — not useful for review
    data.pop("hmac_signature", None)
    return f"""Review this SCD support ticket resolution proposal.

Proposal JSON:
{json.dumps(data, indent=2, ensure_ascii=False)}

Respond with JSON in this exact shape:
{{
  "verdict": "APPROVED" | "FLAGGED" | "POLISHED",
  "notes": "1-3 sentence assessment. If FLAGGED, state exactly what is wrong. If POLISHED, state what was improved."
}}

Verdict meanings:
- APPROVED: proposal is correct and safe to execute
- FLAGGED: something looks wrong — wrong field value, missing step, risky action
- POLISHED: correct but notes include a minor improvement suggestion
"""
