from __future__ import annotations

import re

from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget


REPORTER_EMAIL_DOMAIN_MAP: dict[str, str] = {
    "mobileklinik.ca": "mobileklinik",
    "telus.com": "mobileklinik",
    "cpr-corporate.com": "cpr",
    "ismash.com": "ismash",
    "batteriesplus.com": "batteriesplus",
    "silkroadtelecom.com": "silkroadtelecom",
    "fruitfixed.com": "fruitfixed",
    "compupod.com": "compupod",
    "mobilesnap.ca": "mobilesnap",
    "idropped.com": "idropped",
    "experimax.com": "experimax",
    "sosi.com": "sosi",
    "assurant.com": "sosi",
}

ORG_NAME_MAP: dict[str, str] = {
    "mobile klinik": "mobileklinik",
    "mobileklinik": "mobileklinik",
    "telus": "mobileklinik",
    "cpr cell phone repair": "cpr",
    "cpr canada": "cpr-canada",
    "ismash": "ismash",
    "batteries plus": "batteriesplus",
    "silk road": "silkroadtelecom",
    "fruit fixed": "fruitfixed",
    "compupod": "compupod",
    "mobilesnap": "mobilesnap",
    "samsung service": "samsungsvc",
    "idropped": "idropped",
    "experimax": "experimax",
    "sosi": "sosi",
    "assurant": "sosi",
}

SQL_TEMPLATE = (
    "insert into `transaction` values ("
    "null, '{rq_ticket}', 'Customer', '{customer_id}', '{location_id}', "
    "'{terminal_id}', 3, '{payment_method_id}', '{amount}', "
    "'{card_brand} #{last4}', '{timestamp}', '{staff_user_id}', "
    "'in', null, '{amount}', '{transaction_id}', null, null, null, null, '0', 0, 0);"
)


def build_suggestion_from_extraction(ticket: dict, extraction_result: dict) -> ResolutionSuggestion:
    ticket_id = ticket["key"]
    fields = ticket.get("fields", {})
    subject = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "")
    comment_count = fields.get("comment", {}).get("total", 0)

    client_url = _extract_client_url(fields)
    client_name, client_source = _resolve_client(fields, client_url)
    rq_ticket = _extract_rq_ticket(subject, fields)

    sql_statements = _fill_sql(extraction_result, rq_ticket)
    transactions = extraction_result.get("transactions", [])
    confidence = 0.93 if extraction_result.get("extraction_complete") else 0.40
    transaction_word = "transactions" if len(transactions) > 1 else "transaction"

    closing_actions: list[Action] = []
    if extraction_result.get("extraction_complete"):
        closing_actions = [
            Action(
                step=len(sql_statements) + 4,
                type="jira_internal_note",
                payload={"body": "This ticket was resolved using my AI Agent", "public": False},
            ),
            Action(
                step=len(sql_statements) + 5,
                type="jira_public_comment",
                payload={"body": f"Hello,\n\nWe have added the {transaction_word}. Let us know if you need anything else!"},
            ),
        ]

    return ResolutionSuggestion(
        ticket_id=ticket_id,
        module="orphaned_transaction",
        module_version="1.1",
        diagnosis=(
            f"Orphaned transaction on {client_name}. "
            f"Payment processed through Global Payments Terminal but not linked to "
            f"RQ ticket {rq_ticket or '(see extraction)'}. "
            f"SQL generated to re-link the transaction."
        ),
        evidence=[
            {"source": "topic_field", "value": "10446 (Transaction Errors)"},
            {"source": "subject", "value": subject},
            {"source": "client_identified", "value": client_name},
            {"source": "client_source", "value": client_source},
            {"source": "rq_ticket_extracted", "value": rq_ticket or "not found"},
            {"source": "extraction_complete", "value": str(extraction_result.get("extraction_complete", False))},
            {"source": "transaction_count", "value": str(len(transactions))},
        ],
        revalidation_targets=[
            RevalidationTarget(
                type="jira_field",
                snapshot={"field": "status", "value": status},
            ),
            RevalidationTarget(
                type="jira_comment_count",
                snapshot={"ticket": ticket_id, "count": comment_count},
            ),
        ],
        actions=[
            Action(
                step=i + 1,
                type="sql",
                payload={"statement": stmt},
            )
            for i, stmt in enumerate(sql_statements)
        ] + [
            Action(
                step=len(sql_statements) + 1,
                type="jira_field_update",
                payload={"field": "customfield_10170", "value_id": "10446"},
            ),
            Action(
                step=len(sql_statements) + 2,
                type="jira_field_update",
                payload={"field": "customfield_10201", "value_id": "10499"},
            ),
            Action(
                step=len(sql_statements) + 3,
                type="jira_transition",
                payload={"to": "Resolved", "resolution": "Fixed / Completed"},
            ),
        ] + closing_actions,
        module_confidence=confidence,
        module_notes=(
            "Brain 1 extraction via Playwright. "
            f"Transactions found: {len(transactions)}."
        ),
    )


def _fill_sql(extraction: dict, rq_ticket: str | None) -> list[str]:
    transactions = extraction.get("transactions", [])
    if not transactions:
        return ["-- No transactions extracted. Manual intervention required."]
    statements = []
    for tx in transactions:
        stmt = SQL_TEMPLATE.format(
            rq_ticket=rq_ticket or tx.get("rq_ticket", ""),
            customer_id=tx.get("customer_id", ""),
            location_id=tx.get("location_id", ""),
            terminal_id=tx.get("terminal_id", ""),
            payment_method_id=tx.get("payment_method_id", ""),
            amount=tx.get("amount", ""),
            card_brand=tx.get("card_brand", ""),
            last4=tx.get("last4", ""),
            timestamp=tx.get("timestamp", ""),
            staff_user_id=tx.get("staff_user_id", ""),
            transaction_id=tx.get("transaction_id", ""),
        )
        statements.append(stmt)
    return statements


def _extract_rq_ticket(subject: str, fields: dict) -> str | None:
    match = re.search(r"[Oo]rphaned\s+[Tt]ransaction\s*[-–]?\s*(\d{5,})", subject)
    if match:
        return match.group(1)
    desc_text = _adf_to_text(fields.get("description") or {})
    match = re.search(r"(?:ticket|RQ)[^\d]*(\d{5,})", desc_text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_client_url(fields: dict) -> str | None:
    desc_text = _adf_to_text(fields.get("description") or {})
    match = re.search(r"https?://([a-z0-9\-]+)\.repairq\.io", desc_text)
    if match:
        return f"https://{match.group(1)}.repairq.io"
    for comment in (fields.get("comment") or {}).get("comments", []):
        body_text = _adf_to_text(comment.get("body") or {})
        match = re.search(r"https?://([a-z0-9\-]+)\.repairq\.io", body_text)
        if match:
            return f"https://{match.group(1)}.repairq.io"
    return None


def _resolve_client(fields: dict, url: str | None) -> tuple[str, str]:
    if url:
        match = re.search(r"https?://([a-z0-9\-]+)\.repairq\.io", url)
        if match:
            return match.group(1), "url"
    reporter = fields.get("reporter") or {}
    email = reporter.get("emailAddress", "")
    if email and "@" in email:
        domain = email.split("@", 1)[1].lower()
        if domain in REPORTER_EMAIL_DOMAIN_MAP:
            return REPORTER_EMAIL_DOMAIN_MAP[domain], "reporter_email"
    orgs = fields.get("customfield_10002") or []
    for org in orgs:
        org_name = (org.get("name") or "").lower()
        for key, subdomain in ORG_NAME_MAP.items():
            if key in org_name:
                return subdomain, "org_field"
    return "unknown", "not_found"


def _adf_to_text(adf: dict) -> str:
    if not adf:
        return ""
    parts = []
    if adf.get("type") == "text":
        parts.append(adf.get("text", ""))
    for child in adf.get("content", []):
        parts.append(_adf_to_text(child))
    return " ".join(parts)