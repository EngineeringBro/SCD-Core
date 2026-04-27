"""
For each known auto-notification topic, show what fields were actually set
on closed tickets: topic, root_cause, assignee, resolution, timespent.
"""
import gzip
import glob
import json
from collections import Counter

TOPICS_OF_INTEREST = {
    "Revv Error Report",
    "Automatic Notifications",
    "Ring Central Alert",
    "Azure Notification",
    "SQS Errors",
    "Asurion",
    "Onboarding",
    "Signup Issues",
    "Mandrill",
}

# Also catch by email for Assurant
ASSURANT_EMAILS = {"april.somerville@assurant.com", "brett.heisler@assurant.com",
                   "storerepair@assurant.com", "joshua.baham@assurant.com",
                   "michael.beechey@assurant.com"}

buckets: dict[str, dict] = {t: {"resolution": Counter(), "root_cause": Counter(),
                                 "assignee": Counter(), "timespent": []} for t in TOPICS_OF_INTEREST}
buckets["Assurant (email)"] = {"resolution": Counter(), "root_cause": Counter(),
                                "assignee": Counter(), "timespent": []}

for path in sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            topic = (rec.get("topic") or "").strip()
            email = (rec.get("reporter_email") or "").strip().lower()

            key = None
            if topic in TOPICS_OF_INTEREST:
                key = topic
            elif email in ASSURANT_EMAILS:
                key = "Assurant (email)"

            if key:
                b = buckets[key]
                b["resolution"][rec.get("resolution") or "(blank)"] += 1
                b["root_cause"][rec.get("root_cause") or "(not set)"] += 1
                b["assignee"][rec.get("assignee") or "(unassigned)"] += 1
                ts = rec.get("timespent")
                if ts:
                    b["timespent"].append(ts)

for topic, b in buckets.items():
    total = sum(b["resolution"].values())
    if total == 0:
        continue
    print(f"\n{'='*60}")
    print(f"  {topic}  ({total} tickets)")
    print(f"{'='*60}")
    print(f"  Resolutions:  {dict(b['resolution'].most_common(5))}")
    print(f"  Root causes:  {dict(b['root_cause'].most_common(5))}")
    print(f"  Assignees:    {dict(b['assignee'].most_common(5))}")
    ts_vals = b["timespent"]
    if ts_vals:
        avg = sum(ts_vals) // len(ts_vals)
        print(f"  Time logged:  {len(ts_vals)}/{total} tickets logged time, avg={avg}s")
    else:
        print(f"  Time logged:  0/{total} tickets logged time")
