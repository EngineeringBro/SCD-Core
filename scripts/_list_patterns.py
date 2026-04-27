import json

data = json.load(open("knowledge/mined_patterns.json", encoding="utf-8"))

print("=== ALL TOPIC PATTERNS (by volume) ===")
topics = sorted(data["by_topic"].items(), key=lambda x: x[1]["total"], reverse=True)
for t, p in topics:
    top = p["top_resolution"]
    conf = p["confidence"]
    total = p["total"]
    print(f"  {total:>6,}  conf={conf:.2f}  top={top:<28}  {t}")

print()
print("=== EMAIL PATTERNS (>= 20 tickets, top 30 by volume) ===")
emails = sorted(data["by_reporter_email"].items(), key=lambda x: x[1]["total"], reverse=True)
for e, p in emails[:30]:
    bot = "[BOT]" if p["is_bot"] else "     "
    top = p["top_resolution"]
    conf = p["confidence"]
    total = p["total"]
    print(f"  {bot}  {total:>6,}  conf={conf:.2f}  top={top:<28}  {e}")
