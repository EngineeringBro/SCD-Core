import gzip, json

comments_on_revv = []
for year in range(2019, 2026):
    try:
        with gzip.open(f"knowledge/tickets_cache_{year}.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                t = json.loads(line)
                topic = (t.get("topic") or "").strip()
                if topic != "Revv Error Report":
                    continue
                for c in (t.get("comments") or []):
                    body = (c.get("body") or "").strip()
                    internal = c.get("internal", False)
                    if body:
                        comments_on_revv.append({"internal": internal, "body": body})
    except FileNotFoundError:
        pass

print(f"Total comments found on Revv tickets: {len(comments_on_revv)}")
internal = [c for c in comments_on_revv if c["internal"]]
public = [c for c in comments_on_revv if not c["internal"]]
print(f"  Internal: {len(internal)}")
print(f"  Public:   {len(public)}")

print("\n--- Sample internal comments (first 10) ---")
for c in internal[:10]:
    print(repr(c["body"][:300]))
    print()

print("--- Sample public comments (first 5) ---")
for c in public[:5]:
    print(repr(c["body"][:300]))
    print()
