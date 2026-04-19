"""
Cleanup script — deletes all [SCD Core] comments posted by the agent
before DRY_RUN mode was in place.

Run once locally:
  python cleanup_comments.py
"""

import json
import base64
import os
import sys
from urllib import request, error
from urllib.parse import urlencode
from pathlib import Path

# ─── Credentials ──────────────────────────────────────────────────────────────

env = {}
env_file = Path(r"C:\Users\HusseinChaib\Documents\Cowork\.service-central-copilot\tools\jira_fetcher\.env")
for line in env_file.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

EMAIL    = env["JIRA_EMAIL"]
TOKEN    = env["JIRA_API_TOKEN"]
BASE_URL = env.get("JIRA_BASE_URL", "https://servicecentral.atlassian.net")

creds   = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
headers = {
    "Authorization": f"Basic {creds}",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def jira_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urlencode(params)
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def delete_comment(key, comment_id):
    url = BASE_URL + f"/rest/api/3/issue/{key}/comment/{comment_id}"
    req = request.Request(url, headers=headers, method="DELETE")
    try:
        with request.urlopen(req, timeout=15):
            pass
        return True
    except error.HTTPError as e:
        print(f"  ERROR deleting {key} comment {comment_id}: {e.code} {e.reason}")
        return False


def extract_text(body):
    """Recursively extract plain text from ADF body."""
    if not body:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        if body.get("type") == "text":
            return body.get("text", "")
        return "".join(extract_text(c) for c in body.get("content", []))
    if isinstance(body, list):
        return "".join(extract_text(c) for c in body)
    return ""

# ─── Main ─────────────────────────────────────────────────────────────────────

def fetch_all_open_tickets():
    tickets = []
    params = {
        "jql":        "project = SCD AND status not in (Closed, Resolved) ORDER BY created ASC",
        "maxResults": 100,
        "fields":     "summary",
    }
    while True:
        data   = jira_get("/rest/api/3/search/jql", params)
        issues = data.get("issues", [])
        tickets.extend(issues)
        print(f"  Fetched {len(issues)} tickets (total: {len(tickets)})")
        if data.get("isLast") or not data.get("nextPageToken"):
            break
        params["nextPageToken"] = data["nextPageToken"]
    return tickets


def main():
    print("Fetching all open SCD tickets...")
    print("WARNING: This script will DELETE comments from Jira tickets.")
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    tickets = fetch_all_open_tickets()
    print(f"Total: {len(tickets)} tickets\n")

    total_deleted = 0
    total_checked = 0

    for issue in tickets:
        key = issue["key"]
        try:
            data     = jira_get(f"/rest/api/3/issue/{key}/comment", {"maxResults": 100})
            comments = data.get("comments", [])
        except Exception as e:
            print(f"  SKIP {key}: could not fetch comments — {e}")
            continue

        for comment in comments:
            total_checked += 1
            body_text = extract_text(comment.get("body", ""))
            if "[SCD Core]" in body_text:
                comment_id = comment["id"]
                print(f"  Deleting {key} comment {comment_id}: {body_text[:80]!r}")
                if delete_comment(key, comment_id):
                    print("    Deleted OK")
                    total_deleted += 1

    print(f"\nDone. Checked {total_checked} comments across {len(tickets)} tickets.")
    print(f"Deleted {total_deleted} [SCD Core] comments.")


if __name__ == "__main__":
    main()
