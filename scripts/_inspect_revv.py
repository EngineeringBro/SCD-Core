"""
Deep inspection of Revv Error Report tickets.
Shows exact field values that were set on closed tickets.
"""
import gzip, glob, json
from collections import Counter

topic_target = "Revv Error Report"

resolution_c   = Counter()
root_cause_c   = Counter()
assignee_c     = Counter()
support_level_c = Counter()
severity_c     = Counter()
type_of_work_c = Counter()
product_c      = Counter()
timespent_vals = []
has_comment    = 0
internal_comments = 0
public_comments   = 0
total = 0

samples = []  # keep a few for full inspection

for path in sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if (rec.get("topic") or "").strip() != topic_target:
                continue
            total += 1
            resolution_c[rec.get("resolution") or "(blank)"] += 1
            root_cause_c[rec.get("root_cause") or "(not set)"] += 1
            assignee_c[rec.get("assignee") or "(unassigned)"] += 1
            support_level_c[rec.get("support_level") or "(not set)"] += 1
            severity_c[rec.get("severity") or "(not set)"] += 1
            type_of_work_c[rec.get("type_of_work") or "(not set)"] += 1
            product_c[str(rec.get("product") or "(not set)")] += 1
            ts = rec.get("timespent")
            if ts:
                timespent_vals.append(ts)

            comments = rec.get("comments") or []
            if comments:
                has_comment += 1
                for c in comments:
                    if isinstance(c, dict):
                        if c.get("internal"):
                            internal_comments += 1
                        else:
                            public_comments += 1

            if len(samples) < 10:
                samples.append(rec)

print(f"Total Revv Error Report tickets: {total}\n")

def show(label, counter, n=8):
    print(f"  {label}:")
    for val, cnt in counter.most_common(n):
        pct = cnt * 100 // total
        print(f"    {cnt:>5,} ({pct:>2}%)  {val}")
    print()

show("Resolution",    resolution_c)
show("Root Cause",    root_cause_c)
show("Assignee",      assignee_c)
show("Support Level", support_level_c)
show("Severity",      severity_c)
show("Type of Work",  type_of_work_c)
show("Product",       product_c)

if timespent_vals:
    avg = sum(timespent_vals) // len(timespent_vals)
    print(f"  Time logged: {len(timespent_vals)}/{total} tickets, avg={avg}s ({avg//60}m {avg%60}s)")
else:
    print(f"  Time logged: 0/{total}")
print(f"  Comments: {has_comment}/{total} tickets had comments")
print(f"    internal: {internal_comments}, public: {public_comments}")

print("\n--- SAMPLE TICKETS (10) ---")
for rec in samples:
    key = rec.get("key", "?")
    summary = (rec.get("summary") or "")[:100]
    res = rec.get("resolution", "")
    assignee = rec.get("assignee", "")
    rc = rec.get("root_cause", "")
    ts = rec.get("timespent", 0)
    comments = rec.get("comments") or []
    comment_bodies = []
    for c in comments:
        if isinstance(c, dict):
            internal_flag = "[INT]" if c.get("internal") else "[PUB]"
            comment_bodies.append(f"{internal_flag} {c.get('author','?')}: {str(c.get('body',''))[:120]}")
        else:
            comment_bodies.append(str(c)[:120])
    print(f"\n{key}  [{res}]  assignee={assignee}  rc={rc}  time={ts}s")
    print(f"  summary: {summary}")
    for cb in comment_bodies[:3]:
        print(f"  {cb}")
