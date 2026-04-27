"""Fetch option IDs for Severity, Support Level, Type of Work from Jira.

Strategy: the context API requires admin rights. Instead, fetch real tickets that
have each target field set and read the raw {id, value} from the response.
We fetch several tickets across different severity/support/work-type values so we
capture all option IDs, not just one.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.jira_clients import JiraReadClient

TARGET_FIELDS = {
    "customfield_10036": "Severity Level",
    "customfield_10143": "Type of Work",
    "customfield_10186": "Support Level",
}

j = JiraReadClient()

# Collect all distinct option {id: value} pairs seen across multiple tickets
seen: dict[str, dict[str, str]] = {fid: {} for fid in TARGET_FIELDS}

# Fetch up to 50 recently closed SCD tickets — enough to see all option variants
issues = j.search(
    jql="project = SCD AND resolution is not EMPTY ORDER BY updated DESC",
    fields=list(TARGET_FIELDS.keys()),
    max_results=50,
)

for issue in issues:
    f = issue.get("fields", {})
    for fid in TARGET_FIELDS:
        raw = f.get(fid)
        if isinstance(raw, dict) and "id" in raw and "value" in raw:
            seen[fid][raw["id"]] = raw["value"]

print("Option IDs found from live tickets:\n")
for fid, fname in TARGET_FIELDS.items():
    print(f"{fname} ({fid}):")
    opts = seen[fid]
    if opts:
        for oid, oval in sorted(opts.items(), key=lambda x: int(x[0])):
            print(f"  {oid}: {oval}")
    else:
        print("  (no tickets with this field set in last 50)")
    print()

