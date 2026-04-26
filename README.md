# SCD Core

Autonomous support queue agent for ServiceCentral. Processes SCD tickets through a 4-brain pipeline with mandatory human approval before any write action.

## Architecture

```
Scan → Route → Module (Brain 1) → Gatekeeper (Brain 2, pure Python)
→ Validator (Brain 3, GPT) → GitHub Issue (proposal) → Human approves
→ Executor (Brain 4, pure Python, write creds injected only after approval)
```

## Key Design Principles

- **No autonomous execution.** Every write requires human approval via GitHub Environment gate.
- **Write credentials** exist only in the `scd-execute` GitHub Environment. They are never injected until you click Approve.
- **JiraReadClient / JiraWriteClient** are strictly separated in code. Investigation jobs only import the read client.
- **Modules are self-contained** problem-solvers. Adding a new ticket type = drop a new module folder + one routing rule.
- **Knowledge ingestion** is build-time only. Coworker agents go in `modules/<name>/ingestion/pending/`, are ingested into `modules/<name>/learned/`, then the module is self-contained at runtime.
- **Version snapshots** freeze `learned/` before every bump. Roll back any version with one command.

## Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `scd-core-run.yml` | Manual only | Scan → Route → Module → Gatekeeper → Validator → Post GitHub Issue proposals |
| `scd-core-execute.yml` | Manual only + Environment approval | Re-validate → Execute approved proposal → Commit logs |

## Modules

| Module | Category | Actions |
|--------|----------|---------|
| `spam` | Spam tickets | Dismiss + close |
| `revv_errors` | Revv sync errors | Log to notifications/revv-reports.md + close |
| `auto_notifications` | Automatic notifications | Log + close |
| `orphaned_transactions` | Transaction errors | SQL fix + rollback + Jira comment + resolve |
| `general` | Everything else | RAG pipeline: retriever → reranker → synthesizer |

## Secrets Required

**Repo-level** (available to all jobs):
- `JIRA_API_TOKEN` — full Jira API token (read + write; code restricts to read in investigation jobs)
- `JIRA_EMAIL`
- `JIRA_BASE_URL`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `PROPOSAL_HMAC_KEY` — for signing proposals between brains
- `GITHUB_TOKEN` — auto-provided by GitHub Actions

**`scd-execute` Environment only** (injected only after approval):
- Same `JIRA_API_TOKEN` — this is where the write client credential lives
- Required reviewer: Hussein Shaib

> Note: Until service accounts are provisioned, the same token is used for both read and write.
> The code-level client split (JiraReadClient/JiraWriteClient) enforces the boundary.
> Future: swap to separate read-only and write-only tokens when available.

## Adding a New Module

1. `mkdir modules/<name>/`
2. Create `module.py` implementing the `Module` base class
3. Create `profile.yaml` (for notification-style modules) or `learned/` folder
4. Add routing rule in `configs/module_registry.yaml`
5. Add tests in `modules/<name>/tests/`

See `docs/adding_a_module.md` for the full recipe.

## Rolling Back a Module Version

```bash
python -m core.ingestor --rollback --module orphaned_transactions --to-version 1.4.0
```

> Autonomous AI agent that monitors, classifies, and resolves a live Jira support queue in real-time — built with Python, GitHub Actions CI/CD, and the Jira REST API. Trained on 50,000+ historical tickets.

## What It Does

- Polls the live SCD Jira queue every 10 minutes via GitHub Actions
- Classifies each ticket using a rule-based engine trained on 50,000+ historical tickets
- Auto-dismisses noise (Revv errors, Azure alerts, Assurant auto-sync, spam)
- Flags real issues for human review with classification + confidence score
- Writes a full structured audit log of every decision made

## Architecture

```
GitHub Actions (cron: every 10 min)
    │
    └──► scd_agent.py
              │  authenticates via Jira REST API v3
              │  fetches open SCD tickets (cursor pagination)
              │  classifies each ticket (22 categories)
              │  executes action (dismiss / flag / close)
              │
              └──► logs/audit.jsonl  (structured audit trail)
```

## Classification Categories

| Category | Action | Volume (historical) |
|----------|--------|-------------------|
| revv_error | Auto-dismiss | ~3,868 |
| inventory_auto | Auto-dismiss | ~8,625 |
| azure_alert | Auto-dismiss | ~577 |
| spam / robocall | Close Won't Do | ~189 |
| orphaned_tx | Human review | ~784 |
| ticket_status | Human review | ~909 |
| google_claim | Human review | ~133 |
| ringcentral | Human review | ~2,260 |
| ...and 14 more | | |

## Stack

- **Language**: Python 3.12 — stdlib only (no external dependencies)
- **CI/CD**: GitHub Actions — scheduled cron pipeline
- **API**: Jira REST API v3 — cursor pagination, Basic auth
- **Secrets**: GitHub Encrypted Secrets
- **Logging**: Structured JSONL audit log + human-readable run log

## Setup

1. Fork this repo (keep it private)
2. Add GitHub Secrets:
   - `JIRA_EMAIL`
   - `JIRA_API_TOKEN`
   - `JIRA_BASE_URL`
3. Enable GitHub Actions in your repo settings
4. The agent will run automatically every 10 minutes

## Audit Log Format

Every action is recorded in `logs/audit.jsonl`:

```json
{
  "ts": "2026-04-19T14:32:00Z",
  "key": "SCD-141827",
  "summary": "Asurion: Error saving Asurion serviceJob SNWT3X7A33B5-1",
  "category": "revv_error",
  "confidence": 0.99,
  "action": "dismiss",
  "result": "closed"
}
```

## Knowledge Base

Trained on 50,636 SCD tickets (April 2026). Intelligence documented in `SCD-Core-v0.2.md`.
