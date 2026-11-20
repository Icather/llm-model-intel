# -*- coding: utf-8 -*-
"""Dump llm-stats leaderboard rows to JSON + print top N."""
import json
import re
import sys

BS = chr(92)

path = sys.argv[1]
topn = int(sys.argv[2]) if len(sys.argv) > 2 else 40
out = sys.argv[3] if len(sys.argv) > 3 else "_ls_parsed.json"

html = open(path, encoding="utf-8", errors="replace").read()
txt = html.replace(BS + '"', '"')

pat = re.compile(r'\{"model_id":"[^"]{1,80}","name":"[^"]{1,120}","organization":"')
rows = []
seen = set()
for m in pat.finditer(txt):
    start = m.start()
    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(txt):
        c = txt[i]
        if in_str:
            if esc:
                esc = False
            elif c == BS:
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    break
        i += 1
    try:
        o = json.loads(txt[start:i + 1])
    except Exception:
        continue
    if o.get("model_id") in seen:
        continue
    seen.add(o.get("model_id"))
    rows.append(o)

with open(out, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)

print("rows:", len(rows))
print("%-4s %-32s %6s %6s %6s %6s %-11s %8s %8s" % ("#", "model", "OVR", "REA", "COD", "AGT", "license", "in$", "out$"))
for i, r in enumerate(rows[:topn], 1):
    print("%-4d %-32s %6s %6s %6s %6s %-11s %8s %8s" % (
        i, r["name"][:32],
        r.get("index_general"), r.get("index_reasoning"),
        r.get("index_code"), r.get("index_agents"),
        (r.get("license") or "-")[:11],
        r.get("input_price"), r.get("output_price")))
