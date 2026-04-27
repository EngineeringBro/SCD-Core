"""Deep inspection of Automatic Notifications and Assurant tickets."""
import gzip, glob, json
from collections import Counter

PROFILES = {
    "Automatic Notifications": {"topic": "Automatic Notifications"},
    "Assurant (email)": {"emails": {
        "april.somerville@assurant.com", "brett.heisler@assurant.com",
        "storerepair@assurant.com", "joshua.baham@assurant.com",
        "michael.beechey@assurant.com",
    }},
}

buckets = {k: {"resolution": Counter(), "root_cause": Counter(), "assignee": Counter(),
               "assignee_email": {}, "support_level": Counter(), "severity": Counter(),
               "type_of_work": Counter(), "timespent": [], "total": 0,
               "comment_count": 0} for k in PROFILES}

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
            if topic == "Automatic Notifications":
                key = "Automatic Notifications"
            elif email in PROFILES["Assurant (email)"]["emails"]:
                key = "Assurant (email)"
            if not key:
                continue

            b = buckets[key]
            b["total"] += 1
            b["resolution"][rec.get("resolution") or "(blank)"] += 1
            b["root_cause"][rec.get("root_cause") or "(not set)"] += 1
            assignee = rec.get("assignee") or "(unassigned)"
            b["assignee"][assignee] += 1
            if assignee != "(unassigned)":
                b["assignee_email"][assignee] = rec.get("assignee_email") or ""
            b["support_level"][rec.get("support_level") or "(not set)"] += 1
            b["severity"][rec.get("severity") or "(not set)"] += 1
            b["type_of_work"][rec.get("type_of_work") or "(not set)"] += 1
            ts = rec.get("timespent")
            if ts:
                b["timespent"].append(ts)
            if rec.get("comments"):
                b["comment_count"] += 1

for key, b in buckets.items():
    total = b["total"]
    print(f"\n{'='*60}")
    print(f"  {key}  ({total} tickets)")
    print(f"{'='*60}")

    def show(label, counter, n=6):
        print(f"  {label}:")
        for val, cnt in counter.most_common(n):
            pct = cnt * 100 // total
            print(f"    {cnt:>5,} ({pct:>2}%)  {val}")

    show("Resolution",    b["resolution"])
    show("Root Cause",    b["root_cause"])
    show("Assignee",      b["assignee"])
    show("Support Level", b["support_level"])
    show("Severity",      b["severity"])
    show("Type of Work",  b["type_of_work"])
    ts = b["timespent"]
    if ts:
        avg = sum(ts) // len(ts)
        print(f"  Time logged: {len(ts)}/{total}, avg={avg}s ({avg//60}m)")
    else:
        print(f"  Time logged: 0/{total}")
    print(f"  Had comments: {b['comment_count']}/{total}")
    print(f"  Assignee emails: {b['assignee_email']}")
