"""
build_ticket_cache.py — fetch ALL closed SCD tickets with full comments
and write to knowledge/tickets_cache.jsonl.gz

Usage:
    python scripts/build_ticket_cache.py            # full build
    python scripts/build_ticket_cache.py --resume   # continue from last checkpoint

Writes:
    knowledge/tickets_cache.jsonl.gz   gzip-compressed JSONL, one ticket per line
    knowledge/cache_progress.json      checkpoint; deleted on successful completion

Each JSONL line:
    {
        "key": "SCD-123",
        "summary": "...",
        "description": "...",        # plain text, max 1000 chars
        "topic": "Transaction Errors",
        "root_cause": "Software Bug",
        "resolution": "Fixed",
        "product": "RepairQ Enterprise",
        "org": "Mobile Klinik",
        "created": "2025-01-15T10:00:00.000+0000",
        "updated": "2025-01-16T08:30:00.000+0000",
        "comments": ["comment text 1", "comment text 2", ...],  # max 500 chars each
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
from core.jira_clients import JiraReadClient  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────────

PAGE_SIZE       = 100       # tickets per JQL page (Jira max = 100)
RATE_DELAY      = 0.15      # seconds between page requests (avoids rate limiting)
MAX_DESC_CHARS  = 1000      # truncate description text
MAX_COMMENT_CHARS = 500     # truncate each comment body
CHECKPOINT_EVERY = 500      # save checkpoint every N tickets processed
MAX_TICKETS     = int(os.environ.get("MAX_CACHE_TICKETS", "1000"))  # 0 = unlimited
JQL = "project = SCD AND resolution is not EMPTY ORDER BY updated DESC"

FIELDS = [
    "summary", "description", "resolution", "comment", "created", "updated",
    "customfield_10170",   # Topic Field
    "customfield_10201",   # Root Cause
    "customfield_10158",   # Product
    "customfield_10002",   # Organizations
]

OUTPUT_FILE     = Path("knowledge/tickets_cache.jsonl.gz")
PROGRESS_FILE   = Path("knowledge/cache_progress.json")


# ── Jira helpers ───────────────────────────────────────────────────────────────

def _count_total(jira: JiraReadClient) -> int:
    data = jira._search_jql({"jql": JQL, "maxResults": 0, "fields": ["summary"]})
    return data.get("total", 0)


def _fetch_page(jira: JiraReadClient, start_at: int) -> list[dict]:
    data = jira._search_jql({
        "jql":        JQL,
        "startAt":    start_at,
        "maxResults": PAGE_SIZE,
        "fields":     FIELDS,
    })
    return data.get("issues", [])


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

    comments_raw = (f.get("comment") or {}).get("comments", [])
    comments = [
        _extract_text(c.get("body"))[:MAX_COMMENT_CHARS]
        for c in comments_raw
        if _extract_text(c.get("body")).strip()
    ]

    # multi-select product → first value only for simplicity
    product_field = f.get("customfield_10158") or []
    product = product_field[0].get("value", "") if product_field else ""

    org_field = f.get("customfield_10002") or []
    org = org_field[0].get("name", "") if org_field else ""

    return {
        "key":         issue["key"],
        "summary":     f.get("summary", ""),
        "description": description,
        "topic":       (f.get("customfield_10170") or {}).get("value", ""),
        "root_cause":  (f.get("customfield_10201") or {}).get("value", ""),
        "resolution":  (f.get("resolution") or {}).get("name", ""),
        "product":     product,
        "org":         org,
        "created":     f.get("created", ""),
        "updated":     f.get("updated", ""),
        "comments":    comments,
        "cached_at":   datetime.now(timezone.utc).isoformat(),
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
    parser = argparse.ArgumentParser(description="Build SCD ticket cache")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from last checkpoint instead of starting fresh")
    args = parser.parse_args()

    jira = JiraReadClient()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Count total first so we know what we're dealing with
    print("[cache] Counting closed SCD tickets...")
    total = _count_total(jira)
    print(f"[cache] Total closed tickets: {total:,}")
    # Apply cap
    if MAX_TICKETS > 0:
        total = min(total, MAX_TICKETS)
        print(f"[cache] Capping to most recent {MAX_TICKETS:,} tickets (set MAX_CACHE_TICKETS=0 for all)")
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    est_minutes = round(pages * (RATE_DELAY + 0.8) / 60, 1)
    print(f"[cache] Pages to fetch: {pages} | Estimated time: ~{est_minutes} min")

    if args.resume and PROGRESS_FILE.exists():
        progress = _load_progress()
        start_at = progress["start_at"]
        written  = progress["written"]
        print(f"[cache] Resuming from startAt={start_at} ({written:,} already written)")
        file_mode = "ab"   # append to existing gz
    else:
        start_at = 0
        written  = 0
        print("[cache] Starting fresh build")
        file_mode = "wb"   # overwrite

    errors = 0
    start_time = time.time()

    with gzip.open(OUTPUT_FILE, file_mode) as gz:
        while start_at < total:
            try:
                issues = _fetch_page(jira, start_at)
            except Exception as exc:
                print(f"  [error] Page startAt={start_at} failed: {exc}")
                errors += 1
                if errors >= 5:
                    print("[cache] Too many consecutive errors — aborting. Run with --resume to continue.")
                    _save_progress(start_at, written, total)
                    sys.exit(1)
                time.sleep(5)
                continue

            errors = 0  # reset consecutive error count on success

            for issue in issues:
                try:
                    record = _build_record(issue)
                    line = json.dumps(record, ensure_ascii=False) + "\n"
                    gz.write(line.encode("utf-8"))
                    written += 1
                except Exception as exc:
                    print(f"  [warn] Skipping {issue.get('key', '?')}: {exc}")

            start_at += len(issues)

            # Stop once we've collected enough tickets
            if MAX_TICKETS > 0 and start_at >= MAX_TICKETS:
                print(f"[cache] Reached cap of {MAX_TICKETS:,} tickets — stopping")
                break

            elapsed = time.time() - start_time
            pct = (start_at / total * 100) if total else 0
            speed = written / elapsed if elapsed > 0 else 0
            remaining = (total - start_at) / speed if speed > 0 else 0
            print(
                f"[cache] {start_at:,}/{total:,} ({pct:.1f}%) | "
                f"written={written:,} | "
                f"elapsed={elapsed/60:.1f}m | "
                f"eta={remaining/60:.1f}m"
            )

            # Save checkpoint periodically
            if written % CHECKPOINT_EVERY == 0:
                _save_progress(start_at, written, total)

            if not issues:
                break   # Jira returned empty page — we're done

            time.sleep(RATE_DELAY)

    elapsed_total = time.time() - start_time
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"\n[cache] Done. {written:,} tickets written to {OUTPUT_FILE}")
    print(f"[cache] File size: {size_mb:.1f} MB (compressed)")
    print(f"[cache] Total time: {elapsed_total/60:.1f} minutes")

    # Remove checkpoint on clean completion
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
    print("[cache] Checkpoint cleared.")


if __name__ == "__main__":
    main()
