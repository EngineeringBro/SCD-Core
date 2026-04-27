import json
data = json.load(open("knowledge/mined_patterns.json"))

key_topics = ["Spam", "Ring Central Alert", "Revv Error Report",
              "Automatic Notifications", "Azure Notification", "(no topic)"]
print("=== TOPIC PATTERNS ===")
for t in key_topics:
    p = data["by_topic"].get(t)
    if p:
        conf = p["confidence"]
        top  = p["top_resolution"]
        pct  = p["top_resolution_pct"] * 100
        tot  = p["total"]
        print(f"{t}: {tot:,} tickets  top={top} ({pct:.0f}%)  conf={conf}")
        print(f"  resolutions: {p['resolutions']}")

key_emails = [
    "noreply@repairq.io", "mail@repairq.io",
    "notify@ringcentral.com", "azure-noreply@microsoft.com",
    "help@mandrill.com", "service@paypal.com",
]
print()
print("=== EMAIL PATTERNS ===")
for e in key_emails:
    p = data["by_reporter_email"].get(e)
    if p:
        top  = p["top_resolution"]
        pct  = p["top_resolution_pct"] * 100
        tot  = p["total"]
        bot  = p["is_bot"]
        print(f"{e}: {tot:,} tickets  top={top} ({pct:.0f}%)  is_bot={bot}")
        print(f"  resolutions: {p['resolutions']}")
    else:
        print(f"{e}: not in patterns")
