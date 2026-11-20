# -*- coding: utf-8 -*-
"""Mechanical stale-number sweep for the master table's live prose.

WHY THIS EXISTS
---------------
On 2026-09-23 the same failure mode bit twice in one day:
  * the 9/16 llm-stats split `Coding 44.2 / Agent 41.1` appeared in TWO bullets
    worded differently; a whole-phrase patch only caught one, so the delivered
    table contradicted itself;
  * a pool rebuild shifted every score, and two hand-written numbers in
    选型结论 (`DeepSeek V4 Pro 48.6`, `Qwen3.8-27B 41.0`) silently went stale.

Reading the prose does not reliably catch this. So: check it mechanically.

WHAT IT DOES
------------
Extracts every `（NN.N` / `NN.N 分` style number from the LIVE region of the
master table (everything before `## 方法论与局限` -- the changelog there is
allowed to quote historical values) and reports any number that is not a
current composite score or a genuine foreign quantity (llm-stats raw score,
a computed delta, an Elo, etc.).

Exit code is always 0 -- this is a *report*, not an assertion, because some
hits are legitimate. Read the output and judge.

USAGE
    python _stale_sweep.py [master_table_YYYYMMDD.md] [db]
"""
import io
import os
import re
import sqlite3
import sys

md_path = sys.argv[1] if len(sys.argv) > 1 else None
db_path = sys.argv[2] if len(sys.argv) > 2 else "llm_intel.db"

if md_path is None:
    cands = sorted(f for f in os.listdir(".") if re.match(r"master_table_\d+\.md$", f))
    assert cands, "no master_table_*.md found"
    md_path = cands[-1]

md = io.open(md_path, encoding="utf-8").read()
cut = md.find("## 方法论与局限")
live = md if cut < 0 else md[:cut]
hist = "" if cut < 0 else md[cut:]

db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row
cur = {r["name"]: round(r["score"], 1) for r in db.execute(
    "SELECT m.name, c.score FROM composite_scores c JOIN models m ON m.id=c.model_id")}
valid = set(cur.values())

print("master : %s  (live %d chars / changelog %d chars)" % (md_path, len(live), len(hist)))
print("scores in db: %d models, %d distinct values" % (len(cur), len(valid)))

# every number attached to a model name, in either （NN.N or NN.N 分 form
hits = []
for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9.\-]{2,24})（([0-9]{2}\.[0-9])", live):
    hits.append((m.group(1).strip(), float(m.group(2)), m.start()))
for m in re.finditer(r"([0-9]{2}\.[0-9])\s*分", live):
    hits.append(("· 分-form", float(m.group(1)), m.start()))

suspect = []
for label, val, pos in hits:
    if val in valid:
        continue
    ctx = live[max(0, pos - 55):pos + 25].replace("\n", " ")
    suspect.append((label, val, ctx))

print("\n--- numbers NOT matching any current composite score (%d) ---" % len(suspect))
for label, val, ctx in suspect:
    print("  %-14s %5.1f  |  …%s…" % (label, val, ctx))

# --- blind-spot detector (added 2026-10-04) ---------------------------------
# The two patterns above only catch `模型名（NN.N` and `NN.N 分`.  The prose
# also compares models as `（GPT-5.6 Sol 57.9 / Grok 4.6 47.5）` -- the number
# sits AFTER the name, inside a parenthetical list -- and that shape was
# invisible to them.  On 2026-10-04 four such numbers had been stale since the
# 9/30 board refresh and only a hand-written scan found them.
#
# Here we take every `（...）` block, look for a tracked model name followed by
# a NN.N, and flag any value that is neither that model's current composite
# score nor one of ITS OWN stored benchmark values (llm-stats raw dims,
# SWE-bench, AA, ...).
#
# Per-model (not global) whitelisting matters: on the 2026-10-04 control run a
# global whitelist silently swallowed `Grok 4.6 47.5` because 47.5 happens to
# be Grok 4.7's raw llm-stats Overall.  Scoping the whitelist to the named
# model keeps the detector at 4/4 on that control.
own = {}
for r in db.execute("SELECT m.name nm, c.score sc FROM composite_scores c "
                    "JOIN models m ON m.id=c.model_id"):
    own.setdefault(r["nm"], set()).add(round(r["sc"], 1))
for r in db.execute("SELECT m.name nm, b.value v FROM benchmarks b "
                    "JOIN models m ON m.id=b.model_id WHERE b.value IS NOT NULL"):
    own.setdefault(r["nm"], set()).add(round(r["v"], 1))
names = sorted(cur, key=len, reverse=True)

blind = []
for pm in re.finditer(r"（([^（）]{4,200})）", live):
    inner = pm.group(1)
    for nm in names:
        for m in re.finditer(re.escape(nm) + r"[^\d]{0,4}?([0-9]{2}\.[0-9])", inner):
            v = round(float(m.group(1)), 1)
            if v in own.get(nm, set()):
                continue
            blind.append((nm, v, live[max(0, pm.start() - 35):pm.end()]))
print("\n--- name-adjacent numbers inside （…） matching nothing in the DB (%d) ---"
      % len(blind))
for nm, v, ctx in blind:
    print("  %-20s %5.1f  (composite %.1f)  |  …%s…"
          % (nm, v, cur[nm], ctx.replace("\n", " ")))

print("\n--- how to read this ---")
print("  legit hits: raw llm-stats values (e.g. 59.7), computed deltas (11.7),")
print("              Elo numbers, prices, percentages.")
print("  stale hits: a model's composite score that should have been refreshed.")
print("  cross-check a stale hit against the table row for that model, then patch")
print("  generate_master.py and regenerate -- never hand-edit the .md.")
