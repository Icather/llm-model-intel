# -*- coding: utf-8 -*-
"""Compare today's parsed llm-stats rows against current DB llm-stats rows."""
import json
import sqlite3

rows = json.load(open("_ls_parsed_20261028.json", encoding="utf-8"))
by_name = {}
for r in rows:
    by_name.setdefault(r["name"].strip().lower(), r)

con = sqlite3.connect("llm_intel.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

models = cur.execute("SELECT id, name, vendor_id, open_weights, license FROM models ORDER BY id").fetchall()
print("models in DB:", len(models))

MET = {"llm-stats-Overall": "index_general", "llm-stats-Reasoning": "index_reasoning",
       "llm-stats-Coding": "index_code", "llm-stats-Agent": "index_agents"}

print("\n%-26s | %-28s | %s" % ("db model_id", "llm-stats name", "diffs"))
print("-" * 100)
matched = 0
for m in models:
    nm = (m["name"] or "").strip()
    key = nm.lower()
    r = by_name.get(key)
    if r is None:
        # try fuzzy
        cands = [k for k in by_name if k.startswith(key[:12])]
        print("%-26s | %-28s | NOT FOUND  cands=%s" % (m["id"], nm, cands[:3]))
        continue
    matched += 1
    parts = []
    rank = rows.index(r) + 1
    for lb, fld in MET.items():
        cur_v = cur.execute("SELECT value, board_date FROM benchmarks WHERE model_id=? AND leaderboard=?",
                            (m["id"], lb)).fetchone()
        if cur_v is None:
            parts.append("%s: DB=none new=%s" % (lb.split('-')[-1], r.get(fld)))
        else:
            nv = r.get(fld)
            d = round(nv - cur_v["value"], 2) if nv is not None else None
            if d != 0:
                parts.append("%s: %.2f->%.2f (%+.2f)@%s" % (lb.split('-')[-1], cur_v["value"], nv, d, cur_v["board_date"]))
    print("%-26s | %-28s | #%-3d %s" % (m["id"], nm, rank, "; ".join(parts) if parts else "no change"))
print("\nmatched:", matched)
