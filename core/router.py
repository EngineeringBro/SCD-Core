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
BRAIN0_FALLBACK_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 100
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"

KNOWN_MODULES = [
    "orphaned_transaction",
    "spam",
    "auto_notification",
    "general",
    "hold",
]

_SYSTEM_PROMPT = (
    "You are Brain 0 of the SCD Core system. Your ONLY job is to classify a "
    "Jira support ticket into one of these module names:\n"
    "- orphaned_transaction: payment transaction processed but not linked to a repair ticket\n"
    "- spam: junk, noise, test tickets, automated alerts with no action needed\n"
    "- auto_notification: automated system notifications (Revv sync errors, Assurant emails)\n"
    "- hold: a human comment instructs the team to leave this ticket alone, not process it, "
    "wait, hold off, or reserve it for a demo/meeting/manual handling\n"
    "- general: everything else that needs a human-style diagnosis\n\n"
    "IMPORTANT: Check the comments carefully. If ANY comment signals the ticket should not "
    "be touched right now (e.g. 'leave this as is', 'do not process', 'on hold', "
    "'using for demo', 'handle manually'), classify as 'hold'.\n\n"
    "Output JSON only: {\"module\": \"<module_name>\", \"hold_reason\": \"<if hold, quote the triggering comment, else empty>\"}\n"
    "No other explanation."
)


def classify(ticket: dict) -> str:
    """
    Classify a ticket into a module name.
    Tier 0: Deterministic rules for high-confidence patterns (topic field + keywords).
            Never overridden by LLM.
    Tier 1: GPT-4o-mini (fast, cheap) for everything else.
    Tier 2: Sonnet fallback if mini returns 'general' on a non-obvious ticket,
            or if mini call fails entirely.
    Tier 3: Deterministic rules again if both LLM calls fail.
    Returns a module name string — always.
    """
    # Tier 0: deterministic-first for patterns we're certain about — LLM cannot override these
    deterministic_first = _deterministic_fallback(ticket)
    if deterministic_first != "general":
        print(f"[router] deterministic-first → '{deterministic_first}' (skipping LLM)")
        return deterministic_first

    gh_token = os.environ.get("COPILOT_TOKEN", "")
    if not gh_token or OpenAI is None:
        return _deterministic_fallback(ticket)

    client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)
    prompt = _build_prompt(ticket)

    # Tier 1: mini
    mini_result = _call_llm(client, BRAIN0_MODEL, prompt)
    if mini_result and mini_result != "general":
        return mini_result

    # Tier 2: Sonnet — when mini failed or returned 'general' (uncertain)
    if mini_result == "general":
        # Only escalate to Sonnet if deterministic rules also say general
        # (avoids wasting Sonnet on tickets where topic field is definitive)
        deterministic = _deterministic_fallback(ticket)
        if deterministic != "general":
            print(f"[router] mini→general but deterministic→{deterministic}, trusting deterministic")
            return deterministic
        print("[router] mini→general + no deterministic match — escalating to Sonnet")
    else:
        print("[router] mini call failed — escalating to Sonnet")

    sonnet_result = _call_llm(client, BRAIN0_FALLBACK_MODEL, prompt)
    if sonnet_result:
        print(f"[router] Sonnet classified as '{sonnet_result}'")
        return sonnet_result

    # Tier 3: deterministic rules
    print("[router] Both LLM tiers failed — using deterministic fallback")
    return _deterministic_fallback(ticket)


def _call_llm(client: "OpenAI", model: str, prompt: str) -> str | None:
    """
    Call the given model with the classification prompt.
    Returns the module name string, or None if the call fails or returns unknown.
    """
    try:
        response = client.chat.completions.create(
            model=model,
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
            print(f"[router] {model}: unknown module '{module_name}' — ignoring")
            return None
        # Attach hold_reason as a side-channel annotation on the string
        # so the orchestrator can surface it without changing the return type
        if module_name == "hold":
            hold_reason = parsed.get("hold_reason", "")
            if hold_reason:
                print(f"[router] hold detected — reason: {hold_reason[:120]}")
        return module_name
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(f"[router] {model} call failed ({type(exc).__name__}: {exc})")
        return None


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

    # Include comments so the router can detect hold instructions
    comments_raw = (fields.get("comment") or {}).get("comments", [])
    comments_text = ""
    if comments_raw:
        lines = []
        for c in comments_raw[-10:]:  # last 10 comments only
            author = (c.get("author") or {}).get("displayName", "?")
            body = _extract_plain_text(c.get("body") or {})
            if body.strip():
                lines.append(f"[{author}]: {body.strip()[:200]}")
        if lines:
            comments_text = "\nComments (latest 10):\n" + "\n".join(lines)

    return (
        f"Ticket: {ticket.get('key', '')}\n"
        f"Summary: {summary}\n"
        f"Topic field: {topic} (id={topic_id})\n"
        f"Reporter email: {reporter_email}\n"
        f"Organization: {org}\n"
        f"Description (first 500 chars): {description}"
        f"{comments_text}\n\n"
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
