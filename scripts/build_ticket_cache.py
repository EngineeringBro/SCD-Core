"""
build_ticket_cache.py — fetch ALL closed SCD tickets with full comments
and write to knowledge/tickets_cache_{year}.jsonl.gz

Usage:
    python scripts/build_ticket_cache.py --year 2025
    python scripts/build_ticket_cache.py --year 2026

Environment variables:
    CACHE_YEAR           override --year (used by GitHub Actions)
    MAX_CACHE_TICKETS    cap tickets per run (0 = unlimited, default 0)

Writes:
    knowledge/tickets_cache_{year}.jsonl.gz   one file per year

Each JSONL line:
    {
        "key": "SCD-123",
        "summary": "...",
        "description": "...",          # plain text, max 2000 chars
        "topic": "Transaction Errors",
        "root_cause": "Software Bug",
        "resolution": "Fixed",
        "resolutiondate": "2025-01-16T08:30:00.000+0000",
        "status": "Closed",
        "issuetype": "Support",
        "assignee": "John Smith",
        "assignee_email": "john@servicecentral.com",
        "reporter": "Jane Doe",
        "reporter_email": "jane@client.com",
        "priority": "2 - High",
        "product": "RepairQ Enterprise",
        "org": "Mobile Klinik",
        "support_level": "L2",
        "severity": "Sev 3",
        "type_of_work": "Support",
        "labels": ["printing", "mk"],
        "timespent": 3600,             # seconds logged on ticket (null if none)
        "repairq_link": "https://mobileklinik.repairq.io/ticket/2848448",
        "created": "2025-01-15T10:00:00.000+0000",
        "updated": "2025-01-16T08:30:00.000+0000",
        "comments": [
            {"author": "John Smith", "created": "2025-01-15T11:00:00.000+0000", "internal": false, "body": "..."}  # max 10000 chars; internal=true means agent-only note
        ],
        "cached_at": "2026-04-26T..."
    }
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow importing from core/ when run as `python scripts/build_ticket_cache.py`
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.jira_fetcher import JiraReadClient  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────────

PAGE_SIZE         = 100       # tickets per JQL page (Jira max = 100)
RATE_DELAY        = 0.15      # seconds between page requests (avoids rate limiting)
MAX_DESC_CHARS    = 2000      # truncate description text
MAX_COMMENT_CHARS = 10000     # truncate each comment body (captures full resolutions)
MAX_TICKETS       = int(os.environ.get("MAX_CACHE_TICKETS", "0"))  # 0 = unlimited

# Year to cache — set via --year arg or CACHE_YEAR env var
# JQL filters by created date for that calendar year
def _year_jql(year: int) -> str:
    return (
        f'project = SCD AND resolution is not EMPTY '
        f'AND created >= "{year}-01-01" AND created <= "{year}-12-31" '
        f'ORDER BY created ASC'
    )

FIELDS = [
    "summary", "description", "resolution", "resolutiondate", "status", "issuetype",
    "assignee", "reporter", "labels", "timespent", "priority",
    "comment", "created", "updated",
    "customfield_10170",   # Topic Field
    "customfield_10201",   # Root Cause
    "customfield_10158",   # Product
    "customfield_10002",   # Organizations
    "customfield_10143",   # Type of Work
    "customfield_10186",   # Support Level
    "customfield_10036",   # Severity Level
    "customfield_10146",   # RepairQ Ticket Link
    "customfield_10033",   # Reporter email (as submitted via portal)
]

OUTPUT_FILE     = Path("knowledge/tickets_cache.jsonl.gz")  # overridden per year in main()
PROGRESS_FILE   = Path("knowledge/cache_progress.json")


# ── Jira helpers ───────────────────────────────────────────────────────────────

def _fetch_page(jira: JiraReadClient, jql: str, next_page_token: str | None, page_size: int) -> tuple[list[dict], str | None]:
    """Fetch one page. Returns (issues, next_page_token). next_page_token=None means last page."""
    payload: dict = {
        "jql":        jql,
        "maxResults": page_size,
        "fields":     FIELDS,
    }
    if next_page_token:
        payload["nextPageToken"] = next_page_token
    data = jira._search_jql(payload)
    return data.get("issues", []), data.get("nextPageToken")


# ── ADF text extraction ────────────────────────────────────────────────────────

def _adf_text(node, _depth: int = 0) -> str:
    if _depth > 10:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_adf_text(c, _depth + 1) for c in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [_adf_text(v, _depth + 1) for v in node.values() if isinstance(v, (dict, list))]
        return " ".join(p for p in parts if p)
    return ""


def _extract_text(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, dict):
        return _adf_text(raw)
    return str(raw)


# ── Record builder ─────────────────────────────────────────────────────────────

def _build_record(issue: dict) -> dict:
    f = issue.get("fields", {})

    description = _extract_text(f.get("description"))[:MAX_DESC_CHARS]

    # Keep ALL comments with author + timestamp + internal flag so we can tell the
    # full conversation and distinguish internal agent notes from public client replies.
    # jsdPublic=False means internal note (agent-only); True or absent = public reply.
    comments_raw = (f.get("comment") or {}).get("comments", [])
    comments = []
    for c in comments_raw:
        body = _extract_text(c.get("body")).strip()
        if not body:
            continue
        author = (c.get("author") or {}).get("displayName", "")
        # jsdPublic=False → internal note; True or missing → public/customer-visible
        jsd_public = c.get("jsdPublic")
        internal = (jsd_public is False)  # explicit False only; None/True = public
        comments.append({
            "author":   author,
            "created":  c.get("created", ""),
            "internal": internal,
            "body":     body[:MAX_COMMENT_CHARS],
        })

    # multi-select product → first value only for simplicity
    product_field = f.get("customfield_10158") or []
    product = product_field[0].get("value", "") if product_field else ""

    org_field = f.get("customfield_10002") or []
    org = org_field[0].get("name", "") if org_field else ""

    labels = f.get("labels") or []

    return {
        "key":            issue["key"],
        "summary":        f.get("summary", ""),
        "description":    description,
        "topic":          (f.get("customfield_10170") or {}).get("value", ""),
        "root_cause":     (f.get("customfield_10201") or {}).get("value", ""),
        "resolution":     (f.get("resolution") or {}).get("name", ""),
        "resolutiondate": f.get("resolutiondate", ""),
        "status":         (f.get("status") or {}).get("name", ""),
        "issuetype":      (f.get("issuetype") or {}).get("name", ""),
        "assignee":       (f.get("assignee") or {}).get("displayName", ""),
        "assignee_email": (f.get("assignee") or {}).get("emailAddress", ""),
        "reporter":       (f.get("reporter") or {}).get("displayName", ""),
        "reporter_email": (f.get("reporter") or {}).get("emailAddress", "") or f.get("customfield_10033", ""),
        "support_level":  (f.get("customfield_10186") or {}).get("value", ""),
        "severity":       (f.get("customfield_10036") or {}).get("value", ""),
        "priority":       (f.get("priority") or {}).get("name", ""),
        "type_of_work":   (f.get("customfield_10143") or {}).get("value", ""),
        "timespent":      f.get("timespent"),  # seconds logged, None if no time logged
        "repairq_link":   _extract_text(f.get("customfield_10146")) or "",  # RepairQ ticket URL
        "labels":        labels,
        "product":       product,
        "org":           org,
        "created":       f.get("created", ""),
        "updated":       f.get("updated", ""),
        "comments":      comments,
        "cached_at":     datetime.now(timezone.utc).isoformat(),
    }


# ── Progress checkpoint ────────────────────────────────────────────────────────

def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"start_at": 0, "written": 0, "total": 0}


def _save_progress(start_at: int, written: int, total: int) -> None:
    PROGRESS_FILE.write_text(json.dumps({
        "start_at": start_at,
        "written": written,
        "total": total,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build SCD ticket cache for a given year")
    parser.add_argument("--year", type=int,
                        default=int(os.environ.get("CACHE_YEAR", datetime.now().year)),
                        help="Calendar year to cache (default: current year)")
    args = parser.parse_args()
    year = args.year

    jql = _year_jql(year)
    output_file = Path(f"knowledge/tickets_cache_{year}.jsonl.gz")

    jira = JiraReadClient()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cap = MAX_TICKETS if MAX_TICKETS > 0 else 999_999
    print(f"[cache] Year {year} — fetching up to {cap:,} closed SCD tickets")
    print(f"[cache] JQL: {jql}")
    print(f"[cache] Output: {output_file}")

    written        = 0
    errors         = 0
    next_token: str | None = None
    start_time     = time.time()

    with gzip.open(output_file, "wb") as gz:
        while written < cap:
            page_size = min(PAGE_SIZE, cap - written)
            try:
                issues, next_token = _fetch_page(jira, jql, next_token, page_size)
            except Exception as exc:
                print(f"  [error] Page failed: {exc}")
                errors += 1
                if errors >= 5:
                    print("[cache] Too many consecutive errors — aborting.")
                    sys.exit(1)
                time.sleep(5)
                continue

            errors = 0

            if not issues:
                break

            for issue in issues:
                try:
                    record = _build_record(issue)
                    gz.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
                    written += 1
                except Exception as exc:
                    print(f"  [warn] Skipping {issue.get('key', '?')}: {exc}")

            elapsed = time.time() - start_time
            print(f"[cache] written={written:,} | elapsed={elapsed:.1f}s | next_token={'yes' if next_token else 'end'}")

            if not next_token:
                break

            time.sleep(RATE_DELAY)

    elapsed_total = time.time() - start_time
    size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"\n[cache] Done. {written:,} tickets written to {output_file}")
    print(f"[cache] File size: {size_mb:.1f} MB (compressed)")
    print(f"[cache] Total time: {elapsed_total/60:.1f} minutes")

    # Remove checkpoint on clean completion
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
    print("[cache] Checkpoint cleared.")


if __name__ == "__main__":
    main()
