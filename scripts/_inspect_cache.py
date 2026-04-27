"""Quick inspection of the cached tickets — run once to validate schema."""
import gzip
import json

PATH = "knowledge/tickets_cache.jsonl.gz"
tickets = []
with gzip.open(PATH, "rt", encoding="utf-8") as f:
    for line in f:
        tickets.append(json.loads(line))

print(f"Total tickets: {len(tickets)}")
print("=" * 70)

# --- schema check: which keys exist across all tickets ---
all_keys = set()
for t in tickets:
    all_keys.update(t.keys())
print(f"\nAll fields found across cache:\n  {sorted(all_keys)}")

# --- field population stats ---
print("\nField population (% non-empty):")
for key in sorted(all_keys):
    filled = sum(
        1 for t in tickets
        if t.get(key) not in (None, "", [], {})
    )
    print(f"  {key:<20} {filled}/{len(tickets)} ({100*filled//len(tickets)}%)")

# --- sample: first ticket with comments ---
print("\n" + "=" * 70)
print("SAMPLE — first ticket with >=2 comments:\n")
for t in tickets:
    if len(t.get("comments", [])) >= 2:
        for k, v in t.items():
            if k == "description":
                print(f"  description     : {str(v)[:300]}...")
            elif k == "comments":
                print(f"  comments ({len(v)} total):")
                for c in v[:4]:
                    if isinstance(c, dict):
                        print(f"    [{c.get('author','?')}]  {c.get('created','')[:19]}")
                        print(f"    {c.get('body','')[:200]}")
                        print()
                    else:
                        print(f"    (plain string): {str(c)[:200]}")
            else:
                print(f"  {k:<16}: {v}")
        break
