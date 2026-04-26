# SCD Core — Repository Guide

## What This Repo Does

SCD Core is an autonomous support-ticket resolution agent for the ServiceCentral Jira queue (project: SCD).

It scans open tickets, routes them to specialized modules, generates resolution proposals, and — after a human approves — executes the actions.

**No action is ever taken automatically.** Every execution requires a manual workflow trigger and passes through a GitHub Environment with a required human reviewer.

## Architecture

```
Scan + Propose (scd-core-run.yml)
  Brain 1: Module (Claude Sonnet) — analyses ticket, produces ResolutionSuggestion
  Brain 2: Gatekeeper (pure Python) — hard safety rules, no LLM
  Brain 3: Validator (GPT) — cross-vendor review
  → GitHub Issue posted for human approval

Execute (scd-core-execute.yml, Environment: scd-execute)
  Re-validation — diffs ticket state against Brain 1 snapshot
  Brain 4: Executor (pure Python) — executes actions via JiraWriteClient
  → Notification logs committed, state committed, Issue closed
```

## Workflows

| Workflow | Trigger | Does |
|----------|---------|------|
| `scd-core-run.yml` | Manual only | Scans queue, posts proposals as Issues |
| `scd-core-execute.yml` | Manual only | Executes one approved proposal |

**Both workflows are `workflow_dispatch` only — no cron, no automation.**

## Secrets Required

Set these in repo **Settings → Secrets and variables → Actions**:

| Secret | Used by |
|--------|---------|
| `JIRA_API_TOKEN` | All workflows |
| `JIRA_EMAIL` | All workflows |
| `JIRA_BASE_URL` | All workflows (e.g. `https://servicecentral.atlassian.net`) |
| `ANTHROPIC_API_KEY` | Run workflow (Brain 1) |
| `OPENAI_API_KEY` | Run workflow (Brain 3) |
| `PROPOSAL_HMAC_KEY` | Both workflows (proposal integrity) |
| `GH_TOKEN` | Both workflows (post/close Issues) |

## GitHub Environment: `scd-execute`

Create this in **Settings → Environments → New environment**.

- Name: `scd-execute`
- Required reviewers: Hussein Shaib (or designated approver)
- This is the structural execution wall — write credentials are only injected after approval

## Modules

| Module | Handles | Version |
|--------|---------|---------|
| `spam` | Spam/junk tickets | 1.0.0 |
| `revv_errors` | Automated Revv sync errors | 1.0.0 |
| `auto_notifications` | RingCentral + system alerts | 1.0.0 |
| `orphaned_transactions` | Orphaned payment transactions | 1.1.0 |
| `general` | Fallback — all unmatched tickets | 1.0.0 |

## Adding a New Module

1. Create `modules/<name>/`
2. Create `module.py` with a class inheriting `Module` (see `modules/spam/module.py`)
3. Create `__init__.py` exporting the class
4. Add a routing rule to `configs/module_registry.yaml`
5. Add a closure profile to `configs/closure_profiles.yaml` if notification-based

## Knowledge Ingestion

To add learned patterns to a module:

1. Drop a `.md` or `.yaml` file into `modules/<name>/ingestion/pending/`
2. Run `python -m core.ingestor` (future — not yet implemented)
3. Extracted patterns are merged into `modules/<name>/learned/`
4. Version is bumped in `modules/<name>/version.yaml`

## Running Locally (read-only, no writes)

```bash
pip install -r requirements.txt

export JIRA_API_TOKEN=...
export JIRA_EMAIL=...
export JIRA_BASE_URL=https://servicecentral.atlassian.net
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GITHUB_REPOSITORY=EngineeringBro/SCD-Core
export GH_TOKEN=...

python -m core.orchestrator
```

## Safety Constraints

- No ticket is modified without explicit per-action human approval
- Terminal statuses are blocked by the Gatekeeper (fulfilled, declined, rejected, etc.)
- SQL actions are human-executed only — the agent generates and proposes, never runs
- Re-validation runs at execution time — if the ticket changed since the proposal, execution halts
