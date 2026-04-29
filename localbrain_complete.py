"""
Build and post the localbrain ResolutionSuggestion from prompt extraction output.

Used by the VS Code scd-localbrain prompt after browser extraction completes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.resolver import complete_module_needed
from modules.orphaned_transaction_module_v1_1.suggestion_builder import build_suggestion_from_extraction


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    repo = os.environ.get("GITHUB_REPO", "")
    if repo and not os.environ.get("GITHUB_REPOSITORY"):
        os.environ["GITHUB_REPOSITORY"] = repo


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete a localbrain caller issue from prompt extraction output")
    parser.add_argument("--context", required=True, help="Path to the localbrain prompt context JSON")
    parser.add_argument("--extraction", required=True, help="Path to the extraction result JSON")
    args = parser.parse_args()

    _load_env()

    with open(args.context, encoding="utf-8") as handle:
        context = json.load(handle)
    with open(args.extraction, encoding="utf-8") as handle:
        extraction = json.load(handle)

    snapshot = context.get("snapshot") or {}
    ticket = snapshot.get("ticket") or {}
    module_name = snapshot.get("module")
    issue_number = int(context["issue_number"])

    if module_name != "orphaned_transaction":
        raise RuntimeError(f"Unsupported localbrain module: {module_name!r}")

    suggestion = build_suggestion_from_extraction(ticket, extraction)
    complete_module_needed(issue_number, suggestion)
    print(f"[localbrain_complete] Posted ResolutionSuggestion for issue #{issue_number}")


if __name__ == "__main__":
    main()