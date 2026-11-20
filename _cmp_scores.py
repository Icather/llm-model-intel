# -*- coding: utf-8 -*-
import sqlite3, sys
a = sqlite3.connect(sys.argv[1]); b = sqlite3.connect(sys.argv[2])
a.row_factory = b.row_factory = sqlite3.Row
A = {r["model_id"]: dict(r) for r in a.execute("SELECT * FROM composite_scores")}
B = {r["model_id"]: dict(r) for r in b.execute("SELECT * FROM composite_scores")}
print("old n=%d  new n=%d" % (len(A), len(B)))
rows = []
for k in set(A) | set(B):
    if k not in A: rows.append((k, None, B[k]["score"], "NEW")); continue
    if k not in B: rows.append((k, A[k]["score"], None, "GONE")); continue
    d = B[k]["score"] - A[k]["score"]
    rows.append((k, A[k]["score"], B[k]["score"], d))
rows.sort(key=lambda r: -abs(r[3]) if isinstance(r[3], float) else 0)
print("\n%-26s %7s %7s %7s %s" % ("model", "old", "new", "delta", "n_sources/conf"))
for k, o, n, d in rows:
    nb = B.get(k, {})
    print("%-26s %7s %7s %7s  n=%s->%s ci=%s..%s" % (
        k, "%.1f" % o if o is not None else "-", "%.1f" % n if n is not None else "-",
        ("%+.1f" % d) if isinstance(d, float) else str(d),
        A.get(k, {}).get("n_sources", "-"), nb.get("n_sources", "-"),
        ("%.1f" % nb["ci_low"]) if nb.get("ci_low") is not None else "-",
        ("%.1f" % nb["ci_high"]) if nb.get("ci_high") is not None else "-"))
print("\nmax |delta| = %.1f" % max(abs(r[3]) for r in rows if isinstance(r[3], float)))
