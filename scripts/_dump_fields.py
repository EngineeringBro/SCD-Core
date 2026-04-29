"""Dump ALL custom fields from a real SCD ticket to find the Internal Note field."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.jira_fetcher import JiraReadClient

jira = JiraReadClient()

# Fetch a recent closed SCD ticket with ALL fields (no field filter)
data = jira._search_jql({
    "jql": "project = SCD AND resolution is not EMPTY ORDER BY updated DESC",
    "maxResults": 1,
    # no "fields" key = Jira returns everything
})

issue = data["issues"][0]
fields = issue["fields"]
key = issue["key"]

print(f"Ticket: {key}\n")
print("All non-null fields:")
for k, v in sorted(fields.items()):
    if v is None or v == "" or v == [] or v == {}:
        continue
    # Truncate long values
    display = str(v)
    if len(display) > 200:
        display = display[:200] + "..."
    print(f"  {k}: {display}")
