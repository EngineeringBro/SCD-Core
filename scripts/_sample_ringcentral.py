"""Sample RingCentral tickets — show summary + first comment to find signal."""
import gzip, glob, json, random

samples = []
for path in sorted(glob.glob("knowledge/tickets_cache_*.jsonl.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if (rec.get("topic") or "") == "Ring Central Alert":
                samples.append(rec)

random.seed(42)
random.shuffle(samples)

print(f"Total RingCentral tickets: {len(samples)}\n")
for rec in samples[:30]:
    res = rec.get("resolution", "")
    summary = rec.get("summary", "")[:120]
    desc = (rec.get("description") or "")[:300]
    first_comment = ""
    comments = rec.get("comments") or []
    if comments:
        c = comments[0]
        body = c.get("body", c) if isinstance(c, dict) else c
        first_comment = str(body)[:200]
    print(f"[{res}] {summary}")
    print(f"  desc:    {desc[:200]}")
    if first_comment:
        print(f"  comment: {first_comment}")
    print()
