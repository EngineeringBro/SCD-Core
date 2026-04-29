"""
localbrain.py — Local Module Runner

Watches the GitHub repo for issues labeled 'local-brain-caller'.
When one appears, extracts the ticket snapshot and runs the appropriate
module locally (with Playwright access to your live Chrome session).
Posts the completed action plan back to the same GitHub issue, then
labels it 'local-brain-complete'.

Usage:
    python localbrain.py --watch          # watch for new issues (blocking)
    python localbrain.py --ticket SCD-123 # run module for a specific ticket directly

Requirements:
    - COPILOT_TOKEN env var (Sonnet API access)
    - JIRA_API_TOKEN, JIRA_EMAIL, JIRA_BASE_URL (to fetch ticket snapshot)
    - GH_TOKEN env var (GitHub API — read + comment + label issues)
    - GITHUB_REPO env var (e.g. EngineeringBro/SCD-Core)
    - Playwright + Chrome user profile path (CHROME_USER_DATA_DIR)
    - playwright Python package installed
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Constants ────────────────────────────────────────────────────────────────

MODULE_NEEDED_LABEL = "local-brain-caller"
MODULE_COMPLETE_LABEL = "local-brain-complete"
GITHUB_API = "https://api.github.com"
POLL_INTERVAL_DEFAULT = 60   # seconds — overridden by X-Poll-Interval header


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _gh_headers() -> dict:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("GH_TOKEN env var not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        raise RuntimeError("GITHUB_REPO env var not set")
    return repo


def _list_open_issues_with_label(label: str) -> list[dict]:
    """Return all open issues with the given label."""
    repo = _repo()
    url = f"{GITHUB_API}/repos/{repo}/issues?labels={urllib.parse.quote(label)}&state=open&per_page=50"
    req = urllib.request.Request(url, headers=_gh_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _post_comment(issue_number: int, body: str) -> None:
    repo = _repo()
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, headers=_gh_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def _add_label(issue_number: int, label: str) -> None:
    repo = _repo()
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels"
    data = json.dumps({"labels": [label]}).encode()
    req = urllib.request.Request(url, data=data, headers=_gh_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def _remove_label(issue_number: int, label: str) -> None:
    repo = _repo()
    encoded = urllib.parse.quote(label)
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels/{encoded}"
    req = urllib.request.Request(url, headers=_gh_headers(), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError:
        pass  # label may already be gone


def _extract_snapshot_from_issue(issue: dict) -> dict | None:
    """Parse the ticket snapshot JSON from the issue body."""
    import re
    body = issue.get("body", "")
    matches = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    if not matches:
        return None
    try:
        return json.loads(matches[0])
    except (json.JSONDecodeError, ValueError):
        return None


# ── Module runner ─────────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load .env file if present (for local dev convenience)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def _run_module_for_issue(issue: dict) -> None:
    """Extract snapshot from issue, run module, post result back."""
    issue_number = issue["number"]
    issue_title = issue.get("title", "")
    print(f"[localbrain] Processing issue #{issue_number}: {issue_title}")

    snapshot = _extract_snapshot_from_issue(issue)
    if snapshot is None:
        print(f"[localbrain] #{issue_number}: no snapshot JSON found in issue body — skipping")
        return

    ticket_data = snapshot.get("ticket", {})
    module_name = snapshot.get("module", "")
    ticket_id = ticket_data.get("key", "")

    if not ticket_id or not module_name:
        print(f"[localbrain] #{issue_number}: missing ticket_id or module_name in snapshot — skipping")
        return

    print(f"[localbrain] #{issue_number}: ticket={ticket_id} module={module_name}")

    # Import and run the module
    try:
        if module_name == "orphaned_transaction":
            # Use the self-contained localbrain/ copy — full Brain1+Playwright agentic loop.
            # Step 9 (Jira edits) is intentionally excluded here; the executor handles it
            # after human approval of the generated SQL + action plan.
            _lb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "localbrain")
            if _lb_path not in sys.path:
                sys.path.insert(0, _lb_path)
            from module import OrphanedTransactionModule  # localbrain/module.py
            module = OrphanedTransactionModule()
            jira = None  # Brain1 uses Playwright directly — Jira read client not needed
        else:
            from core.registry import discover_modules
            from core.jira_fetcher import JiraReadClient
            module_map = discover_modules()
            module = module_map.get(module_name)
            if module is None:
                raise ValueError(f"Module '{module_name}' not found in loaded modules: {list(module_map.keys())}")
            jira = JiraReadClient()

        suggestion = module.run(ticket_data, jira)

    except (ImportError, ValueError, RuntimeError) as exc:
        error_body = (
            f"## ❌ localbrain Error\n\n"
            f"Failed to run module `{module_name}` for `{ticket_id}`.\n\n"
            f"```\n{exc}\n```\n\n"
            f"Manual intervention required."
        )
        _post_comment(issue_number, error_body)
        print(f"[localbrain] #{issue_number}: module run failed — {exc}")
        return

    # Post suggestion JSON back to the caller issue so orchestrator can pick it up
    from dataclasses import asdict as _asdict
    suggestion_json = json.dumps(_asdict(suggestion), indent=2, ensure_ascii=False)
    result_comment = (
        f"## ✅ Brain 1 Complete\n\n"
        f"Module `{module_name}` finished extraction for `{ticket_id}`.\n"
        f"Orchestrator will continue to gatekeeper and post the proposal.\n\n"
        f"<details>\n"
        f"<summary>ResolutionSuggestion (for orchestrator)</summary>\n\n"
        f"```json\n{suggestion_json}\n```\n"
        f"</details>\n"
    )
    _post_comment(issue_number, result_comment)
    _remove_label(issue_number, MODULE_NEEDED_LABEL)
    _add_label(issue_number, MODULE_COMPLETE_LABEL)
    print(f"[localbrain] #{issue_number}: posted suggestion JSON, labeled local-brain-complete — orchestrator will continue")


def _run_module_for_ticket(ticket_id: str) -> None:
    """Direct mode: fetch ticket from Jira, find pending issue, run module."""
    print(f"[localbrain] Direct mode for ticket {ticket_id}")

    # Find the open local-brain-caller issue for this ticket
    issues = _list_open_issues_with_label(MODULE_NEEDED_LABEL)
    matching = [i for i in issues if ticket_id in i.get("title", "")]
    if not matching:
        print(f"[localbrain] No open local-brain-caller issue found for {ticket_id}")
        print("Run GitHub Actions scan first to create the trigger issue.")
        sys.exit(1)

    _run_module_for_issue(matching[0])


# ── Watch loop ────────────────────────────────────────────────────────────────

def _watch() -> None:
    """
    Watch for new scd-module-needed issues using GitHub events API with ETag.
    Fires immediately when a new issue appears. Blocking — runs until Ctrl+C.
    """
    repo = _repo()
    url = f"{GITHUB_API}/repos/{repo}/events"
    etag = ""
    poll_interval = POLL_INTERVAL_DEFAULT
    processed_issues: set[int] = set()

    # On startup, process any already-open issues we haven't handled yet
    print("[localbrain] Checking for existing open local-brain-caller issues...")
    existing = _list_open_issues_with_label(MODULE_NEEDED_LABEL)
    for issue in existing:
        processed_issues.add(issue["number"])
        _run_module_for_issue(issue)

    print(f"[localbrain] Watching {repo} for new local-brain-caller issues... (Ctrl+C to stop)")

    while True:
        try:
            headers = dict(_gh_headers())
            if etag:
                headers["If-None-Match"] = etag

            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=poll_interval + 10) as r:
                etag = r.headers.get("ETag", etag)
                poll_interval = int(r.headers.get("X-Poll-Interval", POLL_INTERVAL_DEFAULT))
                events = json.loads(r.read())

            # Check for IssuesEvent with action=opened and our label
            new_issue_found = False
            for event in events:
                if event.get("type") != "IssuesEvent":
                    continue
                payload = event.get("payload", {})
                if payload.get("action") != "opened":
                    continue
                issue = payload.get("issue", {})
                labels = [lb.get("name", "") for lb in issue.get("labels", [])]
                if MODULE_NEEDED_LABEL in labels and issue["number"] not in processed_issues:
                    new_issue_found = True
                    processed_issues.add(issue["number"])
                    _run_module_for_issue(issue)

            if not new_issue_found:
                time.sleep(poll_interval)

        except urllib.error.HTTPError as e:
            if e.code == 304:
                # Not Modified — no new events, wait and retry
                time.sleep(poll_interval)
            else:
                print(f"[localbrain] GitHub API error: {e.code} — retrying in 60s")
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n[localbrain] Stopped.")
            sys.exit(0)
        except OSError as e:
            print(f"[localbrain] Network error: {e} — retrying in 60s")
            time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="SCD Core Local Brain — runs modules requiring Playwright")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", action="store_true", help="Watch GitHub for local-brain-caller issues")
    group.add_argument("--ticket", metavar="SCD-XXXXX", help="Run module for a specific ticket ID directly")
    args = parser.parse_args()

    if args.watch:
        _watch()
    else:
        _run_module_for_ticket(args.ticket)


if __name__ == "__main__":
    main()
