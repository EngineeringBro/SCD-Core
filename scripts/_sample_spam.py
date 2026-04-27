"""
Sample no-topic tickets to identify spam/noise patterns for content-based detection.
Shows summary + reporter email + first 300 chars of description for analysis.
"""
import gzip
import glob
import json
import random

cache_files = sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz"))

# Collect all no-topic tickets
no_topic = []
tagged_spam = []

for path in cache_files:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            topic = (rec.get("topic") or "").strip()
            if not topic:
                no_topic.append(rec)
            elif topic == "Spam":
                tagged_spam.append(rec)

print(f"No-topic tickets: {len(no_topic):,}")
print(f"Tagged spam tickets: {len(tagged_spam):,}")

# Show 30 random tagged-spam tickets to understand actual patterns
print("\n" + "="*80)
print("TAGGED SPAM SAMPLES (topic=Spam, resolved=Dismissed)")
print("="*80)
sample_spam = [t for t in tagged_spam if (t.get("resolution") or "") == "Dismissed"]
random.seed(42)
for rec in random.sample(sample_spam, min(30, len(sample_spam))):
    desc = (rec.get("description") or "")[:200].replace("\n", " ")
    comments = rec.get("comments", [])
    first_comment = ""
    if comments:
        c = comments[0]
        body = c.get("body", c) if isinstance(c, dict) else c
        first_comment = str(body)[:150].replace("\n", " ")
    print(f"\n[{rec['key']}] FROM: {rec.get('reporter_email','?')}")
    print(f"  SUMMARY: {rec.get('summary','')[:120]}")
    print(f"  DESC:    {desc}")
    if first_comment:
        print(f"  COMMENT: {first_comment}")

# Show 30 random no-topic tickets that were resolved as Dismissed
print("\n" + "="*80)
print("NO-TOPIC DISMISSED TICKETS (likely untagged spam/noise)")
print("="*80)
no_topic_dismissed = [t for t in no_topic if (t.get("resolution") or "") == "Dismissed"]
print(f"No-topic + Dismissed: {len(no_topic_dismissed):,}")
for rec in random.sample(no_topic_dismissed, min(30, len(no_topic_dismissed))):
    desc = (rec.get("description") or "")[:200].replace("\n", " ")
    print(f"\n[{rec['key']}] FROM: {rec.get('reporter_email','?')}")
    print(f"  SUMMARY: {rec.get('summary','')[:120]}")
    print(f"  DESC:    {desc}")

# Reporter email breakdown for no-topic dismissed
print("\n" + "="*80)
print("REPORTER EMAIL BREAKDOWN — no-topic + Dismissed")
print("="*80)
from collections import Counter
email_counter = Counter(t.get("reporter_email", "(unknown)") or "(unknown)" for t in no_topic_dismissed)
for email, count in email_counter.most_common(30):
    print(f"  {count:>5,}  {email}")
