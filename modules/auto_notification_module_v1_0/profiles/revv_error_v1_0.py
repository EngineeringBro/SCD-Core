"""
Revv Error Report — profile v1.0

Signal:     topic field = 10494 (Revv Error Report)
Confidence: 0.90  (hard guidance — user confirmed 2026-04-27)
Source:     50,630 historical SCD tickets; 99% closed Done, 100% Sev 3,
            98% Level 1, 99% Support, assigned Tim Parrish

Action sequence (no branching):
  1  Set Topic         → 10494 (Revv Error Report)
  2  Set Severity      → 10043 (Sev 3)
  3  Set Support Level → 10471 (Level 1)
  4  Set Type of Work  → 10313 (Support)
  5  Set Root Cause    → 10501 (Unknown)
  6  Assign            → tim.parrish@servicecentral.com
  7  Log time          → 1m (authenticated user)
  8  Close             → Done
"""
from core.resolution_suggestion import Action

VERSION    = "1.0"  # conf=0.90 >= 90% -> v1.x; .0 = first edition
CONFIDENCE = 0.90
DIAGNOSIS  = "Automated Revv sync error notification. No customer impact."

ACTIONS = (
    Action(step=1, type="jira_field_update", payload={"field": "customfield_10170", "value_id": "10494"}),  # Topic: Revv Error Report
    Action(step=2, type="jira_field_update", payload={"field": "customfield_10036", "value_id": "10043"}),  # Sev 3
    Action(step=3, type="jira_field_update", payload={"field": "customfield_10186", "value_id": "10471"}),  # Level 1
    Action(step=4, type="jira_field_update", payload={"field": "customfield_10143", "value_id": "10313"}),  # Support
    Action(step=5, type="jira_field_update", payload={"field": "customfield_10201", "value_id": "10501"}),  # Unknown
    Action(step=6, type="jira_assign",       payload={"email": "tim.parrish@servicecentral.com"}),
    Action(step=7, type="jira_log_time",     payload={"time_spent": "1m"}),
    Action(step=8, type="jira_transition",   payload={"to": "Closed", "resolution": "Done"}),
)
