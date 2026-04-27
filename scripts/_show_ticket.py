import gzip, json

with gzip.open("knowledge/tickets_cache.jsonl.gz", "rt", encoding="utf-8") as f:
    tickets = [json.loads(l) for l in f]

t = tickets[239]  # 0-indexed → ticket #240

for k, v in t.items():
    if k == "comments":
        print(f"comments ({len(v)} total):")
        for i, c in enumerate(v, 1):
            author  = c.get("author", "?")
            created = c.get("created", "")
            body    = c.get("body", "")
            print(f"  [{i}] author : {author}")
            print(f"       created: {created}")
            print(f"       body   : {body}")
            print()
    else:
        print(f"{k}: {v}")
