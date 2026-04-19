# SCD Core

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
