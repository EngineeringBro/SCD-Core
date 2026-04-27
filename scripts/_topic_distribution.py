"""
Topic distribution analysis across all cached SCD tickets.
Reads all knowledge/tickets_cache_*.jsonl.gz files.
"""
import gzip
import glob
import json
from collections import Counter

cache_files = sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz"))

topic_counter    = Counter()
resolution_counter = Counter()
assignee_counter = Counter()
reporter_email_counter = Counter()
issuetype_counter = Counter()
no_topic = 0
total = 0

for path in cache_files:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            total += 1
            topic = rec.get("topic", "").strip()
            if topic:
                topic_counter[topic] += 1
            else:
                no_topic += 1
            resolution_counter[rec.get("resolution", "(blank)") or "(blank)"] += 1
            assignee_counter[rec.get("assignee", "(unassigned)") or "(unassigned)"] += 1
            reporter_email_counter[rec.get("reporter_email", "(unknown)") or "(unknown)"] += 1
            issuetype_counter[rec.get("issuetype", "(blank)") or "(blank)"] += 1

print(f"Total tickets: {total:,}")
print(f"Tickets with topic set: {total - no_topic:,} ({100*(total-no_topic)//total}%)")
print(f"Tickets with NO topic:  {no_topic:,} ({100*no_topic//total}%)")

print("\n── TOP TOPICS (ranked by volume) ────────────────────────────────")
for topic, count in topic_counter.most_common(40):
    bar = "█" * (count * 40 // topic_counter.most_common(1)[0][1])
    print(f"  {count:>6,}  {bar:<40}  {topic}")

print("\n── RESOLUTION BREAKDOWN ─────────────────────────────────────────")
for res, count in resolution_counter.most_common():
    print(f"  {count:>6,}  {res}")

print("\n── ISSUE TYPE BREAKDOWN ─────────────────────────────────────────")
for it, count in issuetype_counter.most_common():
    print(f"  {count:>6,}  {it}")

print("\n── TOP 20 ASSIGNEES (who closes most tickets) ───────────────────")
for name, count in assignee_counter.most_common(20):
    print(f"  {count:>6,}  {name}")

print("\n── TOP 20 REPORTER EMAILS (bot detection) ───────────────────────")
for email, count in reporter_email_counter.most_common(20):
    print(f"  {count:>6,}  {email}")
