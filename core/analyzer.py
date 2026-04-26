"""
Analyzer — Brain 1 LLM layer. Uses Claude Sonnet 4.6 to analyze a ticket
and produce a structured diagnosis before the module applies its action plan.

The module (pure Python) handles routing and action selection.
The analyzer enriches the diagnosis with LLM reasoning.

Called by the orchestrator after module.run() — the analyzer's output
is merged into the ResolutionSuggestion before it reaches the Gatekeeper.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from core.resolution_suggestion import ResolutionSuggestion

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

ANALYZER_MODEL = "claude-sonnet-4.6"
MAX_TOKENS = 600
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"


@dataclass
class AnalysisResult:
    enriched_diagnosis: str
    confidence_adjustment: float   # delta applied to module_confidence (-0.2 to +0.1)
    flags: list[str]               # any concerns the LLM spotted
    skipped: bool = False


def analyze(ticket: dict, suggestion: ResolutionSuggestion) -> AnalysisResult:
    """
    Use Claude Sonnet 4.6 to review the ticket and the module's initial
    ResolutionSuggestion. Returns an enriched diagnosis and any flags.

    If GH_TOKEN is not available, returns the module's original diagnosis unchanged.
    """
    gh_token = os.environ.get("GH_TOKEN", "")
    if not gh_token or OpenAI is None:
        return AnalysisResult(
            enriched_diagnosis=suggestion.diagnosis,
            confidence_adjustment=0.0,
            flags=[],
            skipped=True,
        )

    client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)

    prompt = _build_prompt(ticket, suggestion)

    response = client.chat.completions.create(
        model=ANALYZER_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Brain 1 of the SCD Core autonomous support agent. "
                    "Your job is to analyze a Jira support ticket and a proposed resolution plan, "
                    "then produce an enriched diagnosis. "
                    "Be concise and precise. Output JSON only."
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
        return AnalysisResult(
            enriched_diagnosis=parsed.get("diagnosis", suggestion.diagnosis),
            confidence_adjustment=float(parsed.get("confidence_adjustment", 0.0)),
            flags=parsed.get("flags", []),
        )
    except (json.JSONDecodeError, ValueError):
        return AnalysisResult(
            enriched_diagnosis=suggestion.diagnosis,
            confidence_adjustment=0.0,
            flags=[f"Analyzer returned unparseable response: {raw[:200]}"],
        )


def _build_prompt(ticket: dict, suggestion: ResolutionSuggestion) -> str:
    fields = ticket.get("fields", {})
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "")
    topic = (fields.get("customfield_10170") or {}).get("value", "")
    description = _extract_description(fields.get("description") or {})

    return f"""Analyze this SCD support ticket and the module's proposed resolution.

TICKET:
- ID: {ticket.get("key")}
- Summary: {summary}
- Status: {status}
- Topic Field: {topic}
- Description (first 800 chars): {description[:800]}

MODULE PROPOSED:
- Module: {suggestion.module} v{suggestion.module_version}
- Initial diagnosis: {suggestion.diagnosis}
- Actions: {json.dumps([{{"step": a.step, "type": a.type}} for a in suggestion.actions])}
- Module confidence: {suggestion.module_confidence}

Respond with JSON in this exact shape:
{{
  "diagnosis": "1-3 sentence enriched diagnosis. Be specific about what is wrong and why the proposed resolution is correct (or not).",
  "confidence_adjustment": 0.0,
  "flags": []
}}

confidence_adjustment: float between -0.2 and +0.1. Use negative if something looks wrong or uncertain.
flags: list of strings — any concerns, missing information, or risks. Empty list if none.
"""


def _extract_description(adf: dict) -> str:
    if not adf:
        return ""
    parts = []
    if adf.get("type") == "text":
        parts.append(adf.get("text", ""))
    for child in adf.get("content", []):
        parts.append(_extract_description(child))
    return " ".join(p for p in parts if p)
