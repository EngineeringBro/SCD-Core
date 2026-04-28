"""
Orphaned Transactions Module v1.1

Brain 1 (Sonnet) + Playwright agentic loop.
Runs locally via localbrain.py — requires a live Chrome session with RepairQ authenticated.

The module:
  1. Reads the full ticket context (Jira snapshot already fetched by Fetcher)
  2. Loads agent_logic.md as the system prompt / operational knowledge
  3. Runs an agentic loop: Sonnet calls navigate/evaluate_js tools against your Chrome
  4. Extracts all transaction data, fills SQL template
  5. Returns a fully resolved ResolutionSuggestion with sql + jira actions
"""
from __future__ import annotations
import json
import os
import re
import yaml
from pathlib import Path
from core.module_base import Module
from core.resolution_suggestion import ResolutionSuggestion, Action, RevalidationTarget

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

LEARNED_DIR = Path(__file__).parent / "learned"
COPILOT_BASE_URL = "https://api.business.githubcopilot.com"
BRAIN1_MODEL = "claude-sonnet-4-5"
MAX_TOOL_TURNS = 20  # max agentic loop iterations before giving up

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

# Playwright tool definitions for Claude tool_use
_PLAYWRIGHT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate_to_page",
            "description": "Navigate the browser to a URL and wait for the page to load.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_js",
            "description": (
                "Execute JavaScript in the current browser page and return the result. "
                "Use page.evaluate() patterns. "
                "Return structured data (objects/arrays) — not HTML."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "JavaScript to execute. Should return a JSON-serializable value.",
                    },
                },
                "required": ["script"],
            },
        },
    },
]


class OrphanedTransactionModule(Module):
    name = "orphaned_transaction"
    version = "1.1"
    needs_local_run = True   # requires Playwright + live Chrome session

    def __init__(self) -> None:
        manifest_path = LEARNED_DIR / "manifest.yaml"
        self._manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        location_cache_path = LEARNED_DIR / "location_cache.yaml"
        self._location_cache = yaml.safe_load(
            location_cache_path.read_text(encoding="utf-8")
        ).get("locations", {})
        self._agent_logic = (LEARNED_DIR / "agent_logic.md").read_text(encoding="utf-8")

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

        client_url = _extract_client_url(fields)
        client_name, client_source = _resolve_client(fields, client_url)
        base_url = CLIENT_TABLE.get(client_name, client_url or "unknown")
        rq_ticket = _extract_rq_ticket(subject, fields)

        extraction_result = self._run_brain1_loop(ticket_id, fields, base_url, rq_ticket)
        sql_statements = _fill_sql(extraction_result, rq_ticket)
        confidence = 0.93 if extraction_result.get("extraction_complete") else 0.40

        return ResolutionSuggestion(
            ticket_id=ticket_id,
            module=self.name,
            module_version=self.version,
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
                {"source": "transaction_count", "value": str(len(extraction_result.get("transactions", [])))},
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
            ],
            module_confidence=confidence,
            module_notes=(
                "Brain 1 + Playwright agentic extraction. "
                f"Transactions found: {len(extraction_result.get('transactions', []))}."
            ),
        )

    def _run_brain1_loop(
        self,
        ticket_id: str,
        fields: dict,
        base_url: str,
        rq_ticket: str | None,
    ) -> dict:
        """
        Run Sonnet with Playwright tools in an agentic loop.
        Returns extraction result dict with transaction data.
        Falls back to empty result if Playwright or API unavailable.
        """
        gh_token = os.environ.get("COPILOT_TOKEN", "")
        chrome_profile = os.environ.get("CHROME_USER_DATA_DIR", "")

        if not gh_token or OpenAI is None:
            print(f"[orphaned_tx] COPILOT_TOKEN not set — skipping Brain1 extraction")
            return {"extraction_complete": False, "transactions": []}

        if not chrome_profile:
            print(f"[orphaned_tx] CHROME_USER_DATA_DIR not set — skipping Playwright extraction")
            return {"extraction_complete": False, "transactions": []}

        ticket_context = _build_ticket_context(
            ticket_id, fields, base_url, rq_ticket, self._location_cache
        )
        system_prompt = (
            "You are Brain 1 of the SCD Core orphaned transaction agent.\n\n"
            "Your operational knowledge (agent_logic.md):\n"
            f"{self._agent_logic}\n\n"
            "Execute the extraction steps using the available tools (navigate_to_page, evaluate_js). "
            "When you have all required data, output a JSON object as your final message:\n"
            '{"extraction_complete": true, "transactions": [{'
            '"transaction_id": "...", "customer_id": "...", "location_id": "...", '
            '"terminal_id": "...", "payment_method_id": "...", "amount": "...", '
            '"card_brand": "...", "last4": "...", "timestamp": "...", "staff_user_id": "..."'
            "}]}\n\n"
            "Output ONLY this JSON as your final response — no prose."
        )

        client = OpenAI(api_key=gh_token, base_url=COPILOT_BASE_URL)
        messages: list[dict] = [{"role": "user", "content": ticket_context}]

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[orphaned_tx] playwright not installed — run: pip install playwright")
            return {"extraction_complete": False, "transactions": []}

        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=chrome_profile,
                headless=False,
                channel="chrome",
                args=["--no-first-run", "--no-default-browser-check"],
            )
            page = browser.new_page()

            try:
                for _turn in range(MAX_TOOL_TURNS):
                    response = client.chat.completions.create(
                        model=BRAIN1_MODEL,
                        messages=[{"role": "system", "content": system_prompt}] + messages,
                        tools=_PLAYWRIGHT_TOOLS,
                        tool_choice="auto",
                        temperature=0.1,
                        max_tokens=2000,
                    )

                    msg = response.choices[0].message
                    tool_calls_serializable = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in (msg.tool_calls or [])
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": tool_calls_serializable,
                    })

                    if not msg.tool_calls:
                        # Final answer
                        raw = (msg.content or "").strip()
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                        try:
                            return json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            print(f"[orphaned_tx] Final answer not valid JSON: {raw[:200]}")
                            return {"extraction_complete": False, "transactions": [], "raw_response": raw}

                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, ValueError):
                            args = {}

                        if tool_name == "navigate_to_page":
                            url = args.get("url", "")
                            print(f"[orphaned_tx] navigate: {url}")
                            try:
                                page.goto(url, wait_until="load", timeout=30000)
                                tool_result: dict = {"navigated": True, "url": url, "title": page.title()}
                            except OSError as exc:
                                tool_result = {"navigated": False, "error": str(exc)}

                        elif tool_name == "evaluate_js":
                            script = args.get("script", "")
                            print(f"[orphaned_tx] evaluate_js ({len(script)} chars)")
                            try:
                                js_result = page.evaluate(script)
                                tool_result = {"result": js_result}
                            except OSError as exc:
                                tool_result = {"error": str(exc)}

                        else:
                            tool_result = {"error": f"Unknown tool: {tool_name}"}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        })

            finally:
                browser.close()

        print(f"[orphaned_tx] Max turns ({MAX_TOOL_TURNS}) reached without final answer")
        return {"extraction_complete": False, "transactions": []}


# ── SQL filling ───────────────────────────────────────────────────────────────

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


def _build_ticket_context(
    ticket_id: str,
    fields: dict,
    base_url: str,
    rq_ticket: str | None,
    location_cache: dict,
) -> str:
    subject = fields.get("summary", "")
    desc = _adf_to_text(fields.get("description") or {})
    comments_raw = (fields.get("comment") or {}).get("comments", [])
    comments = "\n".join(
        f"[{c.get('author', {}).get('displayName', '?')}]: {_adf_to_text(c.get('body') or {})}"
        for c in comments_raw
    )
    cache_yaml = yaml.dump(location_cache, default_flow_style=False)
    return (
        f"Ticket: {ticket_id}\n"
        f"Summary: {subject}\n"
        f"Client base URL: {base_url}\n"
        f"RQ ticket number (extracted from title): {rq_ticket or 'not found — locate in description'}\n\n"
        f"Description:\n{desc}\n\n"
        f"Comments:\n{comments}\n\n"
        f"Location cache (check before Steps 3+4):\n{cache_yaml}\n\n"
        f"Begin extraction following your agent_logic.md knowledge."
    )


# ── Field extraction helpers ──────────────────────────────────────────────────

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
    if url:
        m = re.search(r"https?://([a-z0-9\-]+)\.repairq\.io", url)
        if m:
            return m.group(1), "url"
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
