"""
JiraReadClient — exposes ONLY read operations.
JiraWriteClient — exposes read + write operations.

Investigation jobs import ONLY JiraReadClient.
The executor imports JiraWriteClient (available only inside scd-execute Environment).

Both use the same underlying JIRA_API_TOKEN until service accounts are provisioned.
"""
from __future__ import annotations
import json
import base64
import urllib.request
import urllib.parse
import os
from typing import Any


class _JiraBase:
    def __init__(self):
        email = os.environ["JIRA_EMAIL"]
        token = os.environ["JIRA_API_TOKEN"]
        self.base = os.environ["JIRA_BASE_URL"].rstrip("/")
        creds = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> dict:
        url = self.base + path
        print(f"[jira] GET {url[:120]}")  # truncate to avoid leaking full token in logs
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Jira API {e.code} on {url[:100]}: {body}") from e

    def _search_jql(self, payload: dict) -> dict:
        """GET /rest/api/3/search/jql — current Jira Cloud search endpoint (cursor pagination)."""
        params: dict = {
            "jql":        payload["jql"],
            "maxResults": payload.get("maxResults", 50),
            "fields":     ",".join(payload["fields"]) if isinstance(payload.get("fields"), list) else (payload.get("fields") or "*all"),
        }
        if payload.get("nextPageToken"):
            params["nextPageToken"] = payload["nextPageToken"]
        return self._get(f"/rest/api/3/search/jql?{urllib.parse.urlencode(params)}")

    def get_issue(self, ticket_id: str, fields: list[str] | None = None) -> dict:
        if fields:
            params = urllib.parse.urlencode({"fields": ",".join(fields)})
            return self._get(f"/rest/api/3/issue/{ticket_id}?{params}")
        return self._get(f"/rest/api/3/issue/{ticket_id}")

    def get_comments(self, ticket_id: str) -> list:
        data = self._get(f"/rest/api/3/issue/{ticket_id}/comment")
        return data.get("comments", [])

    def search(self, jql: str, fields: list[str] | None = None, max_results: int = 50) -> list:
        data = self._search_jql({"jql": jql, "maxResults": max_results, "fields": fields or ["*all"]})
        return data.get("issues", [])


class JiraReadClient(_JiraBase):
    """Read-only Jira client. Safe to use in any job."""

    def _post(self, *args, **kwargs):
        raise PermissionError("JiraReadClient cannot make POST requests. Use JiraWriteClient only inside executor.py.")

    def _put(self, *args, **kwargs):
        raise PermissionError("JiraReadClient cannot make PUT requests. Use JiraWriteClient only inside executor.py.")


class JiraWriteClient(_JiraBase):
    """
    Write-capable Jira client.
    ONLY import this inside core/executor.py.
    NEVER import in module code, gatekeeper, or validator.
    """

    def _post(self, path: str, payload: dict) -> dict:
        url = self.base + path
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()) if r.length else {}

    def _put(self, path: str, payload: dict) -> dict:
        url = self.base + path
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers, method="PUT")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()) if r.length else {}

    def add_comment(self, ticket_id: str, body_adf: dict, internal: bool = False) -> dict:
        payload = {"body": body_adf}
        if internal:
            payload["visibility"] = {"type": "role", "value": "Service Desk Team"}
        return self._post(f"/rest/api/3/issue/{ticket_id}/comment", payload)

    def transition(self, ticket_id: str, transition_id: str, resolution_id: str | None = None) -> dict:
        payload: dict = {"transition": {"id": transition_id}}
        if resolution_id:
            payload["fields"] = {"resolution": {"id": resolution_id}}
        return self._post(f"/rest/api/3/issue/{ticket_id}/transitions", payload)

    def update_field(self, ticket_id: str, field_id: str, value: Any) -> dict:
        return self._put(
            f"/rest/api/3/issue/{ticket_id}",
            {"fields": {field_id: value}},
        )

    def add_worklog(self, ticket_id: str, time_spent: str) -> dict:
        return self._post(
            f"/rest/api/3/issue/{ticket_id}/worklog",
            {"timeSpent": time_spent},
        )

    def find_user_account_id(self, email: str) -> str:
        """Look up a Jira user's accountId by email address."""
        params = urllib.parse.urlencode({"query": email, "maxResults": 5})
        data = self._get(f"/rest/api/3/user/search?{params}")
        if not data:
            raise ValueError(f"No Jira user found for email: {email}")
        # Prefer exact email match
        for user in data:
            if (user.get("emailAddress") or "").lower() == email.lower():
                return user["accountId"]
        # Fall back to first result
        return data[0]["accountId"]

    def assign_user(self, ticket_id: str, account_id: str) -> dict:
        """Assign a ticket to a user by accountId."""
        return self._put(
            f"/rest/api/3/issue/{ticket_id}/assignee",
            {"accountId": account_id},
        )
