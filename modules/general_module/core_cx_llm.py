"""
CX LLM — Step 3 of the General Module pipeline.

Uses the highest available Claude Sonnet model to inspect the top
re-ranked reference cases and produce the best possible ResolutionSuggestion
for the incoming ticket.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget
from modules.general_module.core_cx_retriever import Candidate

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

MODEL = "claude-sonnet-4.6"
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"
MAX_TOKENS = 1200


def judge(
    ticket: dict,
    candidates: list[Candidate],
    module_name: str = "general",
    module_version: str = "2.0.0",
    learned_guidance: str | None = None,
) -> ResolutionSuggestion | None:
    """
    Call Claude Sonnet with the ticket + top reference cases.
    Returns a ResolutionSuggestion, or None if the LLM is unavailable or fails.
    """
    gh_token = os.environ.get("COPILOT_TOKEN", "")
    if not gh_token or OpenAI is None:
        print("[cx_llm] COPILOT_TOKEN not set or openai package missing — skipping")
        return None

    client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)
    prompt = _build_prompt(ticket, candidates, learned_guidance=learned_guidance)

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the CX LLM — Step 3 of the SCD Core autonomous support agent. "
                        "You receive a Jira support ticket and a set of reference cases from "
                        "past resolved tickets and knowledge base articles. "
                        "Your job is to produce the best possible, actionable resolution plan "
                        "for the incoming ticket, informed by the reference cases. "
                        "Output a single JSON object only. Be precise and conservative."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[cx_llm] LLM call failed ({type(exc).__name__}: {exc})")
        return None

    raw = response.choices[0].message.content or ""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(stripped)
        suggestion = _build_suggestion(ticket, parsed, candidates, module_name, module_version)
        # Note: topic is stamped on all suggestions by the orchestrator after module.run()
        if learned_guidance:
            suggestion.sub_agent_attribution["guidance_applied"] = True
        return suggestion
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[cx_llm] Failed to parse LLM response: {exc}")
        return None


def _load_field_options() -> str:
    """
    Load jira_fields.yaml and return a compact reference block listing
    the selectable options for Topic Field, Root Cause, and Product.
    The LLM uses these IDs when producing jira_field_update actions.
    """
    try:
        import yaml
        config_path = Path("configs/jira_fields.yaml")
        if not config_path.exists():
            return ""
        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        fields_cfg = cfg.get("fields", {})
        lines = ["AVAILABLE JIRA FIELD OPTIONS (use these exact IDs in jira_field_update payloads):"]
        for field_id, meta in fields_cfg.items():
            opts = meta.get("options")
            if not opts:
                continue
            lines.append(f"  {field_id} ({meta.get('name', field_id)}):")
            for opt_id, opt_name in opts.items():
                lines.append(f"    id={opt_id}  label=\"{opt_name}\"")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def _build_prompt(ticket: dict, candidates: list[Candidate], learned_guidance: str | None = None) -> str:
    fields = ticket.get("fields", {})
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "")
    topic = (fields.get("customfield_10170") or {}).get("value", "")

    ref_sections = []
    for i, c in enumerate(candidates, 1):
        ref_sections.append(
            f"[REF-{i}] Source: {c.source} | ID: {c.ref_id}\n"
            f"Title: {c.title}\n"
            f"Body: {c.body[:500]}\n"
            f"URL: {c.url}"
        )
    references = "\n\n".join(ref_sections) if ref_sections else "No reference cases available."

    # Human-verified guidance block — authoritative, overrides reference-based guessing
    guidance_block = ""
    if learned_guidance:
        guidance_block = f"""
--- AUTHORITATIVE HUMAN GUIDANCE ---
The following guidance was provided by a human expert for tickets with this topic.
Treat this as the most reliable input — it should directly inform your actions and
raise your confidence level significantly.

{learned_guidance}
--- END HUMAN GUIDANCE ---
"""

    field_options_block = _load_field_options()

    return f"""INCOMING TICKET:
- ID: {ticket.get("key")}
- Summary: {summary}
- Status: {status}
- Topic: {topic}
{guidance_block}
REFERENCE CASES (top matches from closed tickets and knowledge base):
{references}

{field_options_block}

Produce a resolution plan in this exact JSON format:
{{
  "diagnosis": "concise explanation of the issue and what needs to happen",
  "confidence": 0.0,
  "root_cause": "one of: User Behavior or Input Error | Config or Workflow Discrepancy | Integration or Sync Error | Software Bug | Unknown",
  "actions": [
    {{
      "step": 1,
      "type": "jira_public_comment | jira_internal_comment | jira_transition | jira_field_update | notification_log_append",
      "payload": {{}},
      "rationale": "why this action"
    }}
  ],
  "reference_ids": ["REF-1"],
  "notes": "caveats or things the human reviewer must check"
}}

Rules:
- Only use action types listed above
- Maximum 5 actions
- reference_ids must be the REF-N labels from the list above
- For jira_field_update actions: payload must include {{"field": "<customfield_id>", "value": <option_id_as_integer>}}
- Use the AVAILABLE JIRA FIELD OPTIONS above to select the most appropriate option IDs
- If uncertain about any destructive action, use notification_log_append with a detailed note instead"""


def _build_suggestion(
    ticket: dict,
    parsed: dict,
    candidates: list[Candidate],
    module_name: str,
    module_version: str,
) -> ResolutionSuggestion:
    ticket_id = ticket["key"]
    fields = ticket.get("fields", {})
    status = (fields.get("status") or {}).get("name", "")

    actions = []
    for raw_action in parsed.get("actions", [])[:5]:
        step = int(raw_action.get("step", len(actions) + 1))
        action_type = str(raw_action.get("type", "notification_log_append"))
        payload = dict(raw_action.get("payload", {}))

        # Ensure minimum required field for log actions
        if action_type == "notification_log_append" and "log_file" not in payload:
            payload["log_file"] = "cx_llm_proposed"

        actions.append(Action(step=step, type=action_type, payload=payload))

    if not actions:
        actions.append(Action(
            step=1,
            type="notification_log_append",
            payload={
                "log_file": "cx_llm_proposed",
                "note": parsed.get("diagnosis", "No diagnosis produced"),
            },
        ))

    ref_ids = parsed.get("reference_ids", [])
    ref_map = {f"REF-{i + 1}": c for i, c in enumerate(candidates)}
    evidence = []
    for ref_id in ref_ids:
        c = ref_map.get(ref_id)
        if c:
            evidence.append({
                "source": c.source,
                "ref_id": c.ref_id,
                "title": c.title,
                "url": c.url,
            })

    return ResolutionSuggestion(
        ticket_id=ticket_id,
        module=module_name,
        module_version=module_version,
        diagnosis=parsed.get("diagnosis", "CX LLM produced no diagnosis"),
        evidence=evidence,
        revalidation_targets=[
            RevalidationTarget(
                type="jira_field",
                snapshot={"field": "status", "value": status},
            ),
        ],
        actions=actions,
        module_confidence=float(parsed.get("confidence", 0.5)),
        module_notes=(
            f"CX LLM ({MODEL}). "
            f"References used: {', '.join(ref_ids) or 'none'}. "
            f"{parsed.get('notes', '')}"
        ),
    )
