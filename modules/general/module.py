"""
General Module v2 — three-step CX pipeline for unrouted tickets.

Step 1 — cx_retriever : fetch 10-50 candidate reference cases from
          closed SCD tickets and the JSM knowledge base
Step 2 — cx_reranker  : BM25-rank candidates, select top 5
Step 3 — cx_llm       : Claude Sonnet inspects top candidates and
          produces the best possible ResolutionSuggestion

Falls back to a minimal unrouted log entry if any step fails or if
COPILOT_TOKEN is unavailable.
"""
from __future__ import annotations
from datetime import datetime, timezone
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget
from modules.general import core_cx_retriever, core_cx_reranker, core_cx_llm
import os


class GeneralModule(Module):
    name = "general"
    version = "2.0.0"

    def matches(self, ticket: dict) -> bool:
        # General is always the fallback — router assigns it explicitly
        return True

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        fields = ticket.get("fields", {})
        subject = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "")
        topic = (fields.get("customfield_10170") or {})
        topic_id = str(topic.get("id", ""))
        topic_name = topic.get("value", "unknown")

        # Step 1 — Retrieve candidates from closed tickets + KB
        candidates = core_cx_retriever.retrieve(ticket, jira)

        # Step 2 — BM25 rerank, keep top N (configurable via TOP_CANDIDATES env var, default 5)
        top_k = int(os.environ.get("TOP_CANDIDATES", "5"))
        top_candidates = core_cx_reranker.rerank(candidates, ticket, top_k=top_k) if candidates else []

        # Step 3 — LLM judge
        if top_candidates:
            suggestion = core_cx_llm.judge(
                ticket,
                top_candidates,
                module_name=self.name,
                module_version=self.version,
            )
            if suggestion:
                return suggestion

        # Fallback — minimal suggestion with unrouted log
        print("[general] CX pipeline produced no result — falling back to minimal suggestion")
        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=(
                f"No specific module matched this ticket. "
                f"Topic: {topic_name} ({topic_id}). Requires human triage."
            ),
            evidence=[
                {"source": "topic_field", "value": f"{topic_id} ({topic_name})"},
                {"source": "summary", "value": subject},
            ],
            revalidation_targets=[
                RevalidationTarget(
                    type="jira_field",
                    snapshot={"field": "status", "value": status},
                ),
            ],
            actions=[
                Action(
                    step=1,
                    type="notification_log_append",
                    payload={
                        "log_file": "unrouted",
                        "row": {
                            "seen_at": datetime.now(timezone.utc).isoformat(),
                            "ticket": ticket_id,
                            "topic_field": f"{topic_id} ({topic_name})",
                            "subject": subject[:200],
                        },
                    },
                ),
            ],
            module_confidence=0.0,
            module_notes="General fallback — CX pipeline failed or no candidates found. Logged to unrouted for analysis.",
        )
