"""
Orphaned Transactions Module v1.1

Identifies orphaned transaction tickets and proposes the resolution workflow.
Full SQL generation requires the browser-based extraction workflow documented in
modules/orphaned_transactions/learned/agent_logic.md (ported from v1.1 agent).

At this stage the module:
1. Confirms the ticket is an orphaned transaction
2. Identifies the client and RQ ticket number
3. Checks if the client is known
4. Outputs a proposal directing the resolver to use the v1.1 browser workflow
   to extract IDs and generate the INSERT SQL

Future versions will automate the browser extraction via Playwright when running
in an environment with browser access.
"""
from __future__ import annotations
import re
import yaml
from pathlib import Path
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

LEARNED_DIR = Path(__file__).parent / "learned"

CLIENT_TABLE = {
    "mobileklinik": "https://mobileklinik.repairq.io",
    "uat-us-mk": "https://uat-us-mk.repairq.io",
    "mobileklinik-training": "https://mobileklinik-training.repairq.io",
    "cpr": "https://cpr.repairq.io",
    "cpr-canada": "https://cpr-canada.repairq.io",
    "ismash": "https://ismash.repairq.io",
    "batteriesplus": "https://batteriesplus.repairq.io",
    "experimax": "https://experimax.repairq.io",
    "experimax-au": "https://experimax-au.repairq.io",
    "sosi": "https://sosi.repairq.io",
    "silkroadtelecom": "https://silkroadtelecom.repairq.io",
    "fruitfixed": "https://fruitfixed.repairq.io",
    "compupod": "https://compupod.repairq.io",
    "mobilesnap": "https://mobilesnap.repairq.io",
    "samsungsvc": "https://samsungsvc.repairq.io",
    "idropped": "https://idropped.repairq.io",
}

# Maps reporter email domains to RepairQ subdomains
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

# Maps organization name fragments (lowercase) to RepairQ subdomains
ORG_NAME_MAP: dict[str, str] = {
    "mobile klinik": "mobileklinik",
    "mobileklinik": "mobileklinik",
    "telus": "mobileklinik",
    "cpr cell phone repair": "cpr",
    "cpr canada": "cpr-canada",
    "ismash": "ismash",
    "batteries plus": "batteriesplus",
    "batteriesplus": "batteriesplus",
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


class OrphanedTransactionModule(Module):
    name = "orphaned_transaction"
    version = "1.1"  # conf=0.93 >= 90% -> v1.x; .1 = second edition

    def __init__(self):
        manifest_path = LEARNED_DIR / "manifest.yaml"
        self._manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        location_cache_path = LEARNED_DIR / "location_cache.yaml"
        self._location_cache = yaml.safe_load(
            location_cache_path.read_text(encoding="utf-8")
        ).get("locations", {})

    def matches(self, ticket: dict) -> bool:
        topic_id = str(
            (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
        )
        if topic_id == "10446":
            return True
        subject = (ticket.get("fields", {}).get("summary") or "").lower()
        return any(kw in subject for kw in [
            "orphaned transaction", "orphan transaction",
            "stuck transaction", "transaction not linked",
        ])

    def run(self, ticket: dict, jira) -> ResolutionSuggestion:
        ticket_id = ticket["key"]
        fields = ticket.get("fields", {})
        subject = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "")
        comment_count = fields.get("comment", {}).get("total", 0)

        rq_ticket = _extract_rq_ticket(subject, fields)
        client_url = _extract_client_url(fields)
        client_name, client_source = _resolve_client(fields, client_url)
        known_client = client_name in CLIENT_TABLE

        diagnosis = (
            f"Orphaned transaction ticket for client '{client_name}'. "
            f"RQ ticket: {rq_ticket or 'not yet extracted'}. "
            f"Resolution requires browser-based data extraction (see learned/agent_logic.md) "
            f"to obtain IDs and generate the INSERT SQL."
        )

        internal_comment = (
            f"SCD Core identified this as an orphaned transaction.\n\n"
            f"**Client**: {client_name} _(identified via {client_source}{(', ' + client_url) if client_url else ''})_\n"
            f"**RQ Ticket**: {rq_ticket or 'extract from ticket body'}\n\n"
            f"**Resolution steps** (v1.1 workflow):\n"
            f"1. Run Browser Call 1 to extract customer_id, location, VCT list\n"
            f"2. Run Browser Call 2 to extract transaction row + staff_id\n"
            f"3. Generate INSERT SQL using template below\n"
            f"4. Confirm SQL execution\n"
            f"5. Post resolution comment and close\n\n"
            f"**SQL Template**:\n```sql\n{SQL_TEMPLATE}\n```\n\n"
            f"See full workflow: `modules/orphaned_transactions/learned/agent_logic.md`"
        )

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
            diagnosis=diagnosis,
            evidence=[
                {"source": "topic_field", "value": "10446 (Transaction Errors)"},
                {"source": "subject", "value": subject},
                {"source": "client_identified", "value": client_name},
                {"source": "client_source", "value": client_source},
                {"source": "rq_ticket_extracted", "value": rq_ticket or "not found"},
                {"source": "known_client", "value": str(known_client)},
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
                    step=1,
                    type="jira_internal_comment",
                    payload={"body": internal_comment, "internal": True},
                ),
                Action(
                    step=2,
                    type="jira_field_update",
                    payload={"field": "customfield_10170", "value_id": "10446"},
                ),
                Action(
                    step=3,
                    type="jira_field_update",
                    payload={"field": "customfield_10201", "value_id": "10499"},  # Integration or Sync Error
                ),
                # Steps 4-6 (SQL execution, public comment, resolve) are
                # performed AFTER the human runs the browser workflow and
                # confirms SQL execution. This module creates the proposal;
                # the human closes the loop.
            ],
            module_confidence=0.93,
            module_notes=(
                "v1.1: identifies ticket + client + posts workflow guide. "
                "SQL generation is manual (browser required). "
                "Future: automate via Playwright."
            ),
        )


def _extract_rq_ticket(subject: str, fields: dict) -> str | None:
    match = re.search(r"[Oo]rphaned\s+[Tt]ransaction\s*[-–]?\s*(\d{5,})", subject)
    if match:
        return match.group(1)
    desc = fields.get("description") or {}
    desc_text = _adf_to_text(desc)
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
    """Returns (subdomain_or_unknown, source_label). Tries URL → email → org."""
    # 1. URL in description/comments (most reliable)
    if url:
        m = re.search(r"https?://([a-z0-9\-]+)\.repairq\.io", url)
        if m:
            subdomain = m.group(1)
            name = subdomain if subdomain in CLIENT_TABLE else f"{subdomain} (unknown)"
            return name, "url"

    # 2. Reporter email domain
    reporter = fields.get("reporter") or {}
    email = reporter.get("emailAddress", "")
    if email and "@" in email:
        domain = email.split("@", 1)[1].lower()
        if domain in REPORTER_EMAIL_DOMAIN_MAP:
            return REPORTER_EMAIL_DOMAIN_MAP[domain], "reporter_email"

    # 3. Organization field (customfield_10002)
    orgs = fields.get("customfield_10002") or []
    for org in orgs:
        org_name = (org.get("name") or "").lower()
        for key, subdomain in ORG_NAME_MAP.items():
            if key in org_name:
                return subdomain, "org_field"

    return "unknown", "none"


def _adf_to_text(adf: dict) -> str:
    if not adf:
        return ""
    parts = []
    if adf.get("type") == "text":
        parts.append(adf.get("text", ""))
    for child in adf.get("content", []):
        parts.append(_adf_to_text(child))
    return " ".join(parts)
