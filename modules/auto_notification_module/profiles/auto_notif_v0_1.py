"""
Automatic Notifications — profile v1.0

Signal:     topic field = 10404 (Automatic Notifications)
Confidence: 0.76  (76% Dismissed in 182 historical tickets)
Source:     50,630 historical SCD tickets; 83% Level 1, 100% Sev 3,
            70% John Ryon, 75% root cause Unknown.
            Type of Work intentionally omitted — 82% of tickets had no value set.

Action sequence (no branching):
  1  Set Topic         → 10404 (Automatic Notifications)
  2  Set Severity      → 10043 (Sev 3)
  3  Set Support Level → 10471 (Level 1)
  4  Set Root Cause    → 10501 (Unknown)
  5  Assign            → john.ryon@servicecentral.com
  6  Log time          → 1m (authenticated user)
  7  Close             → Dismissed
"""
from core.resolution_suggestion import Action

VERSION    = "0.1"  # conf=0.76 < 90% -> v0.x; .1 = first edition
CONFIDENCE = 0.76
DIAGNOSIS  = "Automated system notification. Dismissed per historical pattern (76%)."

ACTIONS = (
    Action(step=1, type="jira_field_update", payload={"field": "customfield_10170", "value_id": "10404"}),  # Topic: Automatic Notifications
    Action(step=2, type="jira_field_update", payload={"field": "customfield_10036", "value_id": "10043"}),  # Sev 3
    Action(step=3, type="jira_field_update", payload={"field": "customfield_10186", "value_id": "10471"}),  # Level 1
    Action(step=4, type="jira_field_update", payload={"field": "customfield_10201", "value_id": "10501"}),  # Unknown
    Action(step=5, type="jira_assign",       payload={"email": "john.ryon@servicecentral.com"}),
    Action(step=6, type="jira_log_time",     payload={"time_spent": "1m"}),
    Action(step=7, type="jira_transition",   payload={"to": "Closed", "resolution": "Dismissed"}),
)
