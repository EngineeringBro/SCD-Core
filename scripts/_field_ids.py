"""Extract exact field option IDs and user account IDs from cached tickets."""
import gzip, glob, json
from collections import Counter

# Collect raw field values as they appear in cache
# We need option IDs for: severity, support_level, type_of_work
# And account ID for Tim Parrish

assignee_ids = {}   # name -> account_id (if stored)
severity_vals = Counter()
support_vals  = Counter()
work_vals     = Counter()

# Also grab raw Jira field values — cache stores display names not IDs
# so we need to look at the raw json if stored, otherwise note display names

for path in sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            topic = (rec.get("topic") or "").strip()
            if topic != "Revv Error Report":
                continue
            severity_vals[rec.get("severity") or "(not set)"] += 1
            support_vals[rec.get("support_level") or "(not set)"] += 1
            work_vals[rec.get("type_of_work") or "(not set)"] += 1
            assignee = rec.get("assignee") or ""
            assignee_email = rec.get("assignee_email") or ""
            if assignee and assignee not in assignee_ids:
                assignee_ids[assignee] = assignee_email

print("Severity values:", dict(severity_vals.most_common()))
print("Support Level values:", dict(support_vals.most_common()))
print("Type of Work values:", dict(work_vals.most_common()))
print()
print("Assignee name -> email:")
for name, email in sorted(assignee_ids.items()):
    print(f"  {name}: {email}")
