"""Fetch option IDs for Severity, Support Level, Type of Work from Jira."""
import sys
sys.path.insert(0, ".")
from core.jira_clients import JiraReadClient

j = JiraReadClient()
fields = {
    "customfield_10036": "Severity Level",
    "customfield_10143": "Type of Work",
    "customfield_10186": "Support Level",
}
for fid, fname in fields.items():
    data = j._get(f"/rest/api/3/field/{fid}/context")
    contexts = data.get("values", [])
    if not contexts:
        print(f"{fname}: no contexts found")
        continue
    ctx_id = contexts[0]["id"]
    opts = j._get(f"/rest/api/3/field/{fid}/context/{ctx_id}/option")
    print(f"\n{fname} ({fid}):")
    for o in opts.get("values", []):
        oid = o["id"]
        oval = o["value"]
        print(f"  {oid}: {oval}")
