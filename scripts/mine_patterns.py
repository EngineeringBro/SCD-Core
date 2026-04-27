"""
Mine resolution patterns from the cached ticket history.

Reads all knowledge/tickets_cache_*.jsonl.gz and writes:
    knowledge/mined_patterns.json

What gets computed
──────────────────
by_topic
    For every topic label: total tickets, resolution breakdown,
    top resolution + its % share.

by_reporter_email
    For every reporter email (min 20 tickets): same breakdown plus
    an is_bot flag (heuristic: top-resolution share > 85% AND
    the email looks automated OR the domain is a known platform).

by_topic_and_email
    For combos with > 10 tickets together.

Confidence formula
──────────────────
  confidence = top_resolution_pct
  Capped at 0.99 so there's always a small residual for human override.

Run
───
    python scripts/mine_patterns.py               # from repo root
    python scripts/mine_patterns.py --min-count 5 # lower threshold
"""
from __future__ import annotations

import argparse
import gzip
import glob
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CACHE_GLOB = "knowledge/tickets_cache_*.jsonl.gz"
OUTPUT_PATH = Path("knowledge/mined_patterns.json")

# Emails / domain patterns that are structurally automated (platform senders).
# The miner uses this to help set is_bot — data-driven frequency is the primary
# signal, this list just breaks ties for low-volume senders.
KNOWN_BOT_PATTERNS: list[str] = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications", "automated", "autonotif",
    "ringcentral.com", "azure.com", "mandrill.com",
    "paypal.com", "assurant.com", "asurion.com",
    "repairq.io",          # all repairq system senders
]

# Minimum tickets for a pattern to be included.
MIN_TOPIC_COUNT  = 10
MIN_EMAIL_COUNT  = 20
MIN_COMBO_COUNT  = 10


def _looks_automated(email: str) -> bool:
    e = email.lower()
    return any(p in e for p in KNOWN_BOT_PATTERNS)


def _top_resolution(counter: Counter) -> tuple[str, float]:
    """Return (resolution_label, pct_of_total) for the most common resolution."""
    total = sum(counter.values())
    if not total:
        return ("(none)", 0.0)
    top, count = counter.most_common(1)[0]
    return (top, round(count / total, 4))


def _build_pattern(counter: Counter, total: int) -> dict:
    top_res, top_pct = _top_resolution(counter)
    return {
        "total": total,
        "resolutions": dict(counter.most_common()),
        "top_resolution": top_res,
        "top_resolution_pct": top_pct,
        "confidence": min(0.99, top_pct),
    }


def mine(min_topic: int = MIN_TOPIC_COUNT,
         min_email: int = MIN_EMAIL_COUNT,
         min_combo: int = MIN_COMBO_COUNT) -> dict:
    topic_res:        dict[str, Counter] = defaultdict(Counter)
    email_res:        dict[str, Counter] = defaultdict(Counter)
    combo_res:        dict[str, Counter] = defaultdict(Counter)   # "topic||email"

    total_tickets = 0

    cache_files = sorted(glob.glob(CACHE_GLOB))
    if not cache_files:
        raise FileNotFoundError(f"No cache files found matching {CACHE_GLOB}")

    for path in cache_files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                total_tickets += 1

                topic    = (rec.get("topic") or "").strip() or "(no topic)"
                email    = (rec.get("reporter_email") or "").strip().lower() or "(unknown)"
                res      = (rec.get("resolution") or "").strip() or "(blank)"

                topic_res[topic][res]       += 1
                email_res[email][res]       += 1
                combo_res[f"{topic}||{email}"][res] += 1

    # ── Build output ──────────────────────────────────────────────────────────

    by_topic: dict[str, dict] = {}
    for topic, counter in topic_res.items():
        total = sum(counter.values())
        if total < min_topic:
            continue
        by_topic[topic] = _build_pattern(counter, total)

    by_reporter_email: dict[str, dict] = {}
    for email, counter in email_res.items():
        total = sum(counter.values())
        if total < min_email:
            continue
        pattern = _build_pattern(counter, total)
        # is_bot: high resolution consistency AND looks automated
        pattern["is_bot"] = (
            pattern["top_resolution_pct"] >= 0.85
            and _looks_automated(email)
        )
        by_reporter_email[email] = pattern

    by_topic_and_email: dict[str, dict] = {}
    for key, counter in combo_res.items():
        total = sum(counter.values())
        if total < min_combo:
            continue
        topic_part, email_part = key.split("||", 1)
        by_topic_and_email[key] = {
            "topic": topic_part,
            "email": email_part,
            **_build_pattern(counter, total),
        }

    # Summary: bot emails ranked by ticket volume
    bot_emails = sorted(
        [(e, p) for e, p in by_reporter_email.items() if p["is_bot"]],
        key=lambda x: x[1]["total"],
        reverse=True,
    )

    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_tickets":      total_tickets,
        "cache_files_used":   cache_files,
        "bot_emails_summary": [
            {"email": e, "total": p["total"],
             "top_resolution": p["top_resolution"],
             "confidence": p["confidence"]}
            for e, p in bot_emails
        ],
        "by_topic":            by_topic,
        "by_reporter_email":   by_reporter_email,
        "by_topic_and_email":  by_topic_and_email,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine resolution patterns from ticket cache.")
    parser.add_argument("--min-count", type=int, default=MIN_TOPIC_COUNT,
                        help="Minimum tickets to include a topic/email pattern.")
    args = parser.parse_args()

    print(f"[mine_patterns] Scanning {CACHE_GLOB} …")
    data = mine(min_topic=args.min_count, min_email=max(args.min_count, 20),
                min_combo=args.min_count)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    total = data["total_tickets"]
    print(f"[mine_patterns] {total:,} tickets processed")
    print(f"[mine_patterns] {len(data['by_topic'])} topic patterns")
    print(f"[mine_patterns] {len(data['by_reporter_email'])} email patterns")
    print(f"[mine_patterns] {len(data['bot_emails_summary'])} bot emails identified")
    print("[mine_patterns] Bot emails (top 10 by volume):")
    for entry in data["bot_emails_summary"][:10]:
        print(f"    {entry['total']:>7,}  conf={entry['confidence']:.2f}  {entry['email']}")
    print(f"[mine_patterns] Written → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
