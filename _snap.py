# -*- coding: utf-8 -*-
"""Dump the day's composite scores + output prices for next-day diffing.

MUST RUN LAST -- after `llm_intel.py recompute`. On 2026-09-14 this was run at
09:04 while recompute landed at 09:10, so the file recorded the PRE-recompute
state (identical to the 9/13 snapshot) while the DB and master table already had
the new numbers. That silently corrupted the day-over-day baseline.

Guards added 2026-09-15:
  * composite_scores must be non-empty (i.e. recompute has actually run);
  * the output file mtime is asserted to be newer than llm_intel.db's;
  * if the new score snapshot is byte-identical to the most recent one, print a
    loud WARNING -- that is both a legitimate "quiet day" AND the signature of
    the ordering mistake, so it must be judged, not ignored.
"""
import datetime
import io
import json
import os
import sqlite3
import sys

DB = "llm_intel.db"
date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

ncomp = db.execute("SELECT COUNT(*) FROM composite_scores").fetchone()[0]
assert ncomp > 0, "composite_scores is EMPTY -- run `llm_intel.py recompute` first"

scores = {r["name"]: r["score"] for r in db.execute(
    "SELECT m.name, c.score FROM composite_scores c JOIN models m ON m.id=c.model_id "
    "ORDER BY c.score DESC")}

prices = {}
for r in db.execute("SELECT model_id, scenario, currency, output_per_m FROM prices "
                    "ORDER BY model_id, scenario, currency"):
    prices.setdefault(r["model_id"], []).append(
        {"s": r["scenario"], "c": r["currency"], "out": r["output_per_m"]})

sp, pp = "_snap_scores_%s.json" % date, "_snap_prices_%s.json" % date
json.dump(scores, io.open(sp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
json.dump(prices, io.open(pp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

# --- guards -----------------------------------------------------------------
db_mtime = os.path.getmtime(DB)
out_mtime = os.path.getmtime(sp)
assert out_mtime >= db_mtime, (
    "snapshot is OLDER than %s -- you almost certainly ran this before recompute"
    % DB)

prev = sorted(f for f in os.listdir(".")
              if f.startswith("_snap_scores_") and f.endswith(".json") and f != sp)
if prev:
    last = prev[-1]
    if io.open(last, encoding="utf-8").read() == io.open(sp, encoding="utf-8").read():
        print("WARNING: %s is byte-identical to %s." % (sp, last))
        print("         Either a genuinely quiet day, OR the DB was snapshotted before")
        print("         recompute (the 9/14 failure mode). Verify against llm_intel.db.")

print("models", len(scores), "price-rows", sum(len(v) for v in prices.values()))
print("db_mtime", datetime.datetime.fromtimestamp(db_mtime).strftime("%H:%M:%S"),
      "| snap_mtime", datetime.datetime.fromtimestamp(out_mtime).strftime("%H:%M:%S"))
print("wrote", sp, "and", pp)
