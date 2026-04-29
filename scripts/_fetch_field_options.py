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
from core.jira_fetcher import JiraReadClient

TARGET_FIELDS = {
    "customfield_10036": "Severity Level",
    "customfield_10143": "Type of Work",
    "customfield_10186": "Support Level",
}

# JQL to find tickets where each field is set (use cf[...] syntax for custom fields)
TARGETED_JQL = {
    "customfield_10036": 'project = SCD AND "Severity Level" is not EMPTY AND resolution is not EMPTY ORDER BY updated DESC',
    "customfield_10143": 'project = SCD AND "Type of Work" is not EMPTY AND resolution is not EMPTY ORDER BY updated DESC',
    "customfield_10186": 'project = SCD AND "Support Level" is not EMPTY AND resolution is not EMPTY ORDER BY updated DESC',
}

j = JiraReadClient()

print("Option IDs found from live tickets:\n")
for fid, fname in TARGET_FIELDS.items():
    jql = TARGETED_JQL[fid]
    issues = j.search(jql=jql, fields=[fid], max_results=50)
    seen: dict[str, str] = {}
    for issue in issues:
        raw = issue.get("fields", {}).get(fid)
        if isinstance(raw, dict) and "id" in raw and "value" in raw:
            seen[raw["id"]] = raw["value"]
    print(f"{fname} ({fid}):")
    if seen:
        for oid, oval in sorted(seen.items(), key=lambda x: int(x[0])):
            print(f"  {oid}: {oval}")
    else:
        print("  (no tickets with this field set)")
    print()

