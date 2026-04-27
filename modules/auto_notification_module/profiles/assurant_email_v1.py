"""
Assurant Email Sender — profile v1.0

Signal:     reporter_email ends with @assurant.com
Confidence: 0.96  (96% Fixed/Completed in 607 historical tickets)
Source:     50,630 historical SCD tickets; 97% Level 2, 100% Sev 3,
            94% Support, 68% Integration or Sync Error, 88% Ian Turner.
            Time logged on 606/607 tickets, average 70 min.

Action sequence (no branching):
  1  Set Severity      → 10043 (Sev 3)
  2  Set Support Level → 10472 (Level 2)
  3  Set Type of Work  → 10313 (Support)
  4  Set Root Cause    → 10499 (Integration or Sync Error)
  5  Assign            → ian.turner@servicecentral.com
  6  Log time          → 1m (authenticated user)
  7  Close             → Fixed / Completed

Note: Topic field intentionally omitted — Assurant tickets span multiple
      topics; overwriting would lose signal. Classification is by email only.
"""
from core.resolution_suggestion import Action

VERSION    = "1.0"
CONFIDENCE = 0.96
DIAGNOSIS  = "Automated Assurant claims notification. Resolved per historical pattern (96%)."

ACTIONS = (
    Action(step=1, type="jira_field_update", payload={"field": "customfield_10036", "value_id": "10043"}),  # Sev 3
    Action(step=2, type="jira_field_update", payload={"field": "customfield_10186", "value_id": "10472"}),  # Level 2
    Action(step=3, type="jira_field_update", payload={"field": "customfield_10143", "value_id": "10313"}),  # Support
    Action(step=4, type="jira_field_update", payload={"field": "customfield_10201", "value_id": "10499"}),  # Integration or Sync Error
    Action(step=5, type="jira_assign",       payload={"email": "ian.turner@servicecentral.com"}),
    Action(step=6, type="jira_log_time",     payload={"time_spent": "1m"}),
    Action(step=7, type="jira_transition",   payload={"to": "Closed", "resolution": "Fixed / Completed"}),
)
