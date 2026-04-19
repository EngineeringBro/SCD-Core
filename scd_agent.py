"""
SCD Core Agent v0.1
Autonomous Jira support ticket classifier and resolver.
Fetches open SCD tickets, classifies them, and takes action.

DRY_RUN = True  →  No changes made to Jira. All actions are logged only.
DRY_RUN = False →  Live mode. Agent will post comments and close tickets.
"""

import json
import base64
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error
from urllib.parse import urlencode

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scd_agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scd-core")

# ─── Dry Run Mode ─────────────────────────────────────────────────────────────
# Set to False only when Hussein explicitly approves live execution.
DRY_RUN = True

JIRA_EMAIL    = os.environ["JIRA_EMAIL"]
JIRA_TOKEN    = os.environ["JIRA_API_TOKEN"]
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "https://servicecentral.atlassian.net")

_creds   = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
_headers = {
    "Authorization": f"Basic {_creds}",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}

# ─── Jira API Helpers ─────────────────────────────────────────────────────────

def jira_get(path: str, params: dict = None) -> dict:
    url = JIRA_BASE_URL + path
    if params:
        url += "?" + urlencode(params)
    req = request.Request(url, headers=_headers)
    with request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def jira_post(path: str, body: dict) -> dict:
    url  = JIRA_BASE_URL + path
    data = json.dumps(body).encode()
    req  = request.Request(url, data=data, headers=_headers, method="POST")
    with request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()) if r.length else {}


def jira_put(path: str, body: dict) -> dict:
    url  = JIRA_BASE_URL + path
    data = json.dumps(body).encode()
    req  = request.Request(url, data=data, headers=_headers, method="PUT")
    with request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()) if r.length else {}


# ─── Classifier ───────────────────────────────────────────────────────────────

# Each rule: (pattern, category, confidence, action)
# action: "dismiss" | "human_review" | "close_wontdo"
RULES = [
    # Auto-dismiss — no response needed
    (r"asurion: error saving asurion servicejob",        "revv_error",       0.99, "dismiss"),
    (r"assurant: error updating inventory quantities",   "inventory_auto",   0.99, "dismiss"),
    (r"asurion: error updating inventory quantities",    "inventory_auto",   0.99, "dismiss"),
    (r"revv error report",                               "revv_error",       0.99, "dismiss"),
    (r"azure: (activated|deactivated) severity",         "azure_alert",      0.99, "dismiss"),
    (r"gsx permission violation alert",                  "gsx_alert",        0.99, "dismiss"),
    (r"welcome to repairq",                              "onboarding_auto",  0.99, "dismiss"),
    (r"notify of the tasks completed",                   "task_auto",        0.99, "dismiss"),
    (r"there was an error in the sqs process",           "sqs_error",        0.99, "dismiss"),
    (r"scheduledproccess.*ctsi.import|ctsi.*import.*fail", "ctsi_batch",       0.99, "dismiss"),
    (r"notification of payment received",                "payment_auto",     0.99, "dismiss"),
    (r"problem billing your credit card",                "billing_auto",     0.95, "human_review"),

    # Spam
    (r"suspected robocall",                              "spam",             0.99, "close_wontdo"),
    (r"new fax message from unknown",                    "spam",             0.90, "close_wontdo"),

    # RingCentral — needs classification
    (r"new voice message",                               "ringcentral",      0.95, "human_review"),
    (r"new call from",                                   "ringcentral",      0.95, "human_review"),

    # Real issues — human review until confidence is high
    (r"orphan",                                          "orphaned_tx",      0.90, "human_review"),
    (r"close.{0,10}ticket|close.{0,10}claim|close.{0,10}irp|closing.*ticket|can.t close|cannot close|ticket.*not clos|tickets need closed|close repair|close.*duplicate|mark.*ticket.*(close|reject)", "ticket_status", 0.85, "human_review"),
    (r"reopen|re-open",                                  "ticket_status",    0.85, "human_review"),
    (r"balance and close|force close",                   "ticket_status",    0.85, "human_review"),
    (r"cancel claim|reject claim|decline claim",         "asurion_claim",    0.85, "human_review"),
    (r"google.*(claim|in.warranty|in-warranty|\biw\b|not authoris)", "google_claim", 0.85, "human_review"),
    (r"purchase order|reopen.*po|po.*reopen",            "parts_order",      0.85, "human_review"),
    (r"duplicate.*transaction|orphaned payment",         "duplicate_tx",     0.85, "human_review"),
    (r"cannot log|can.t log|password|reset pin|login issue|login problem|\blogin\b|ip permission", "cannot_login", 0.85, "human_review"),
    (r"\bimei\b",                                        "imei_update",      0.85, "human_review"),
    (r"print(ing)?.*fail|receipt.*print",                "printing",         0.80, "human_review"),
    (r"slow|gateway timeout|timed out",                  "slow_perf",        0.75, "human_review"),
    (r"http 500|internal server error",                  "api_error",        0.80, "human_review"),
]


def classify(summary: str, reporter_email: str) -> tuple[str, float, str]:
    """
    Returns (category, confidence, action).
    """
    s = summary.lower()

    # Reporter-based shortcuts
    if "notify@ringcentral.com" in reporter_email.lower():
        if re.search(r"suspected robocall|fax.*unknown", s):
            return "spam", 0.99, "close_wontdo"
        return "ringcentral", 0.90, "human_review"

    for pattern, category, confidence, action in RULES:
        if re.search(pattern, s):
            return category, confidence, action

    return "unknown", 0.50, "human_review"


# ─── Action Executor ──────────────────────────────────────────────────────────

DISMISS_TRANSITION_ID = "81"    # Closes the ticket
WONTDO_RESOLUTION_ID  = "10001"
FIXED_RESOLUTION_ID   = "10000"


def close_ticket(key: str, resolution_id: str, comment: str = None) -> bool:
    """Transition ticket to Closed with optional comment."""
    try:
        if comment:
            jira_post(f"/rest/api/3/issue/{key}/comment", {
                "body": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": comment}
                    ]}]
                }
            })
        jira_post(f"/rest/api/3/issue/{key}/transitions", {
            "transition": {"id": DISMISS_TRANSITION_ID},
            "fields": {"resolution": {"id": resolution_id}}
        })
        return True
    except error.HTTPError as e:
        log.warning(f"  HTTP {e.code} closing {key}: {e.reason}")
        return False


def add_internal_comment(key: str, text: str) -> bool:
    """Add a true internal note (public=False) via Service Desk API — not visible to customer."""
    try:
        jira_post(f"/rest/servicedeskapi/request/{key}/comment", {
            "body":   f"[SCD Core] {text}",
            "public": False,
        })
        return True
    except error.HTTPError as e:
        log.warning(f"  HTTP {e.code} commenting on {key}: {e.reason}")
        return False


# ─── Audit Log ────────────────────────────────────────────────────────────────

AUDIT_LOG_PATH = Path("logs/audit.jsonl")

def audit(key: str, summary: str, category: str, confidence: float, action: str, result: str):
    entry = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "key":        key,
        "summary":    summary[:120],
        "category":   category,
        "confidence": confidence,
        "action":     action,
        "result":     result,
    }
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Main Loop ────────────────────────────────────────────────────────────────

def fetch_open_tickets() -> list[dict]:
    """Fetch all open SCD tickets."""
    tickets = []
    params  = {
        "jql":        "project = SCD AND status not in (Closed, Resolved) ORDER BY created ASC",
        "maxResults": 100,
        "fields":     "summary,status,reporter,description",
    }
    while True:
        data   = jira_get("/rest/api/3/search/jql", params)
        issues = data.get("issues", [])
        tickets.extend(issues)
        log.info(f"Fetched {len(issues)} tickets (total so far: {len(tickets)})")
        if data.get("isLast") or not data.get("nextPageToken"):
            break
        params["nextPageToken"] = data["nextPageToken"]
    return tickets


def run():
    log.info("=" * 60)
    log.info(f"SCD Core Agent starting — {datetime.now(timezone.utc).isoformat()}")
    if DRY_RUN:
        log.info("*** DRY RUN MODE — No changes will be made to Jira ***")
    log.info("=" * 60)

    tickets = fetch_open_tickets()
    log.info(f"Total open tickets: {len(tickets)}")

    stats = {"dismiss": 0, "close_wontdo": 0, "human_review": 0, "error": 0}

    for issue in tickets:
        key     = issue["key"]
        summary = issue["fields"].get("summary", "")
        reporter = (issue["fields"].get("reporter") or {}).get("emailAddress", "")

        category, confidence, action = classify(summary, reporter)

        log.info(f"{key} | {category} ({confidence:.0%}) | {action} | {summary[:80]}")

        if action == "dismiss":
            if DRY_RUN:
                log.info(f"  [DRY RUN] Would close {key} as Fixed")
                result = "dry_run"
                stats["dismiss"] += 1
            else:
                success = close_ticket(key, FIXED_RESOLUTION_ID)
                result  = "closed" if success else "error"
                if not success:
                    stats["error"] += 1
                else:
                    stats["dismiss"] += 1

        elif action == "close_wontdo":
            if DRY_RUN:
                log.info(f"  [DRY RUN] Would close {key} as Won't Do")
                result = "dry_run"
                stats["close_wontdo"] += 1
            else:
                success = close_ticket(key, WONTDO_RESOLUTION_ID)
                result  = "closed_wontdo" if success else "error"
                if not success:
                    stats["error"] += 1
                else:
                    stats["close_wontdo"] += 1

        else:  # human_review
            if DRY_RUN:
                log.info(f"  [DRY RUN] Would flag {key} for human review")
                result = "dry_run"
                stats["human_review"] += 1
            else:
                note    = f"Classified as '{category}' (confidence {confidence:.0%}) — awaiting human action."
                success = add_internal_comment(key, note)
                result  = "flagged" if success else "error"
                if not success:
                    stats["error"] += 1
                else:
                    stats["human_review"] += 1

        audit(key, summary, category, confidence, action, result)

    log.info("-" * 60)
    log.info(f"Run complete. Dismissed: {stats['dismiss']} | Won't Do: {stats['close_wontdo']} | Human review: {stats['human_review']} | Errors: {stats['error']}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
