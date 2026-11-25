# -*- coding: utf-8 -*-
import json
a = json.load(open("_ls_parsed_20261122.json", encoding="utf-8"))
b = json.load(open("_ls_parsed_20261125.json", encoding="utf-8"))
da = {r["name"]: r for r in a}
db = {r["name"]: r for r in b}
print("prev rows %d -> now %d" % (len(a), len(b)))
print("\n=== top 12 prev ===")
for i, r in enumerate(a[:12], 1):
    print("%2d %-30s %6s %6s %6s %6s" % (i, r["name"][:30], r.get("index_general"), r.get("index_reasoning"), r.get("index_code"), r.get("index_agents")))
print("\n=== changed scores (same name) ===")
n = 0
for name, r in db.items():
    o = da.get(name)
    if not o:
        continue
    d = []
    for f in ("index_general", "index_reasoning", "index_code", "index_agents"):
        if r.get(f) != o.get(f):
            d.append("%s %s->%s" % (f.replace("index_", ""), o.get(f), r.get(f)))
    if d:
        n += 1
        print("%-30s %s" % (name, "; ".join(d)))
print("changed:", n)
print("\n=== in now, not prev ===")
for name in db:
    if name not in da:
        r = db[name]
        print("%-30s %-12s %6s rel=%s" % (name, (r.get("organization") or "")[:12], r.get("index_general"), r.get("release_date")))
print("\n=== in prev, not now ===")
for name in da:
    if name not in db:
        print(name)
