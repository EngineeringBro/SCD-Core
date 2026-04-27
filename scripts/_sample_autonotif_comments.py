import gzip, json

comments = []
for year in range(2019, 2026):
    try:
        with gzip.open(f"knowledge/tickets_cache_{year}.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                t = json.loads(line)
                topic = (t.get("topic") or "").strip()
                if topic != "Automatic Notifications":
                    continue
                for c in (t.get("comments") or []):
                    body = (c.get("body") or "").strip()
                    internal = c.get("internal", False)
                    if body:
                        comments.append({"internal": internal, "body": body})
    except FileNotFoundError:
        pass

total = len(comments)
internal = [c for c in comments if c["internal"]]
public = [c for c in comments if not c["internal"]]
print(f"Automatic Notifications — comments across all tickets: {total}")
print(f"  Internal: {len(internal)}")
print(f"  Public:   {len(public)}")

print("\n--- Internal comments (first 10) ---")
for c in internal[:10]:
    print(repr(c["body"][:200]))
    print()

print("--- Public comments (first 10) ---")
for c in public[:10]:
    print(repr(c["body"][:200]))
    print()
