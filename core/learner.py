"""
Learner — captures knowledge from human outcomes.

Triggered by two events (NOT inline during ticket processing):

  1. REJECTION: Human closes a GitHub Issue proposal with a comment explaining
     what was wrong. The closing comment IS the learning signal.
     Called by: GitHub Actions workflow on issues.closed event (when issue
     was NOT executed — i.e. closed without the scd-executed label).

  2. EXECUTION SUCCESS: Executor completes successfully for a ticket.
     Called by: core/executor.py after close_proposal().

Memory is per-module: knowledge/learned/<module>/<topic_slug>.yaml
Each module only learns from its own outcomes — no shared memory.
"""
from __future__ import annotations
from core.learning_store import save_guidance
from core.resolution_suggestion import ResolutionSuggestion


def on_rejection(
    issue_number: int,
    module_name: str,
    topic: str,
    ticket_id: str,
    comment_text: str,
    provided_by: str = "human",
) -> None:
    """
    Called when a human closes a proposal issue with a rejection comment.

    The comment_text is the human's explanation of what was wrong —
    this becomes the learning entry for this module + topic combination.

    Args:
        issue_number:  GitHub Issue number that was rejected
        module_name:   Which module produced the rejected proposal
        topic:         Jira topic field value (e.g. "Transaction Errors")
        ticket_id:     Jira ticket ID (e.g. "SCD-141831")
        comment_text:  The human's closing comment — the learning signal
        provided_by:   GitHub username of the reviewer (for attribution)
    """
    if not comment_text.strip():
        print(f"[learner] on_rejection: no comment text for {ticket_id} — nothing to learn")
        return

    save_guidance(
        topic=topic,
        module_name=module_name,
        ticket_id=ticket_id,
        guidance=comment_text.strip(),
        provided_by=provided_by,
        issue_number=issue_number,
        outcome="rejected",
    )
    print(f"[learner] Rejection captured: module={module_name} topic='{topic}' ticket={ticket_id}")


def on_execution(
    module_name: str,
    topic: str,
    ticket_id: str,
    suggestion: ResolutionSuggestion,
    issue_number: int,
) -> None:
    """
    Called by executor.py after a proposal executes successfully.

    Records what worked — diagnosis, module, actions — as a positive
    learning example for this module + topic combination.

    Args:
        module_name:   Which module produced the executed proposal
        topic:         Jira topic field value
        ticket_id:     Jira ticket ID
        suggestion:    The ResolutionSuggestion that was executed
        issue_number:  GitHub Issue number that was approved and executed
    """
    guidance_text = (
        f"EXECUTED SUCCESSFULLY. "
        f"Diagnosis: {suggestion.diagnosis} "
        f"Actions: {[a.type for a in suggestion.actions]}. "
        f"Confidence at execution: {suggestion.module_confidence}."
    )

    save_guidance(
        topic=topic,
        module_name=module_name,
        ticket_id=ticket_id,
        guidance=guidance_text,
        provided_by="executor",
        issue_number=issue_number,
        outcome="executed",
    )
    print(f"[learner] Execution captured: module={module_name} topic='{topic}' ticket={ticket_id}")
