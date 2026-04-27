"""
Brain 0 — Router LLM.

Cheap, fast model (GPT-4o mini) whose ONLY job is to classify a ticket
into a module name. No confidence scoring — that is Brain 1's job.

Returns:
    str — module name ("orphaned_transaction", "spam", "auto_notification", "general")
    "general" is the fallback when nothing else matches.

Called by the orchestrator before any module runs.
"""
from __future__ import annotations
import json
import os

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

BRAIN0_MODEL = "gpt-4o-mini"
MAX_TOKENS = 100
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"

KNOWN_MODULES = [
    "orphaned_transaction",
    "spam",
    "auto_notification",
    "general",
]

_SYSTEM_PROMPT = (
    "You are Brain 0 of the SCD Core system. Your ONLY job is to classify a "
    "Jira support ticket into one of these module names:\n"
    "- orphaned_transaction: payment transaction processed but not linked to a repair ticket\n"
    "- spam: junk, noise, test tickets, automated alerts with no action needed\n"
    "- auto_notification: automated system notifications (Revv sync errors, Assurant emails)\n"
    "- general: everything else that needs a human-style diagnosis\n\n"
    "Output JSON only: {\"module\": \"<module_name>\"}\n"
    "No explanation. No confidence score. Just the module name."
)


def classify(ticket: dict) -> str:
    """
    Classify a ticket into a module name.
    Falls back to deterministic topic-field matching if LLM is unavailable.
    Returns a module name string — always.
    """
    gh_token = os.environ.get("COPILOT_TOKEN", "")
    if not gh_token or OpenAI is None:
        return _deterministic_fallback(ticket)

    client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)
    prompt = _build_prompt(ticket)

    try:
        response = client.chat.completions.create(
            model=BRAIN0_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            stripped = stripped.rsplit("```", 1)[0].strip()
        parsed = json.loads(stripped)
        module_name = parsed.get("module", "general")
        if module_name not in KNOWN_MODULES:
            print(f"[brain0] Unknown module '{module_name}' — falling back to general")
            return "general"
        return module_name
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(f"[brain0] LLM call failed ({type(exc).__name__}: {exc}) — using deterministic fallback")
        return _deterministic_fallback(ticket)


def _build_prompt(ticket: dict) -> str:
    fields = ticket.get("fields", {})
    summary = fields.get("summary", "")
    topic = (fields.get("customfield_10170") or {}).get("value", "")
    topic_id = str((fields.get("customfield_10170") or {}).get("id", ""))
    reporter_email = (fields.get("reporter") or {}).get("emailAddress", "")
    org = ""
    orgs = fields.get("customfield_10002") or []
    if orgs:
        org = orgs[0].get("name", "") if isinstance(orgs[0], dict) else str(orgs[0])
    description_raw = fields.get("description") or {}
    description = _extract_plain_text(description_raw)[:500]

    return (
        f"Ticket: {ticket.get('key', '')}\n"
        f"Summary: {summary}\n"
        f"Topic field: {topic} (id={topic_id})\n"
        f"Reporter email: {reporter_email}\n"
        f"Organization: {org}\n"
        f"Description (first 500 chars): {description}\n\n"
        f"Which module should handle this ticket?"
    )


def _deterministic_fallback(ticket: dict) -> str:
    """Rule-based fallback matching the registry — used when LLM is unavailable."""
    fields = ticket.get("fields", {})
    topic_id = str((fields.get("customfield_10170") or {}).get("id", ""))
    summary = (fields.get("summary") or "").lower()
    reporter_email = ((fields.get("reporter") or {}).get("emailAddress", "") or "").lower()

    if topic_id == "10446":
        return "orphaned_transaction"
    if topic_id == "10438":
        return "spam"
    if topic_id in ("10404", "10494"):
        return "auto_notification"
    if reporter_email.endswith("@assurant.com"):
        return "auto_notification"
    if any(kw in summary for kw in [
        "orphaned transaction", "orphan transaction",
        "stuck transaction", "transaction not linked", "missing transaction"
    ]):
        return "orphaned_transaction"
    return "general"


def _extract_plain_text(node) -> str:
    """Recursively extract plain text from Jira ADF description."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = []
        for child in node.get("content", []):
            parts.append(_extract_plain_text(child))
        return " ".join(p for p in parts if p)
    return ""
