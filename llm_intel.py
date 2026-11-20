#!/usr/bin/env python3
"""llm_intel -- local knowledge base for LLM vendor/model intelligence.

WHY THIS EXISTS
    Re-searching the same model facts every session burns API quota and loses
    provenance. Everything we scrape/verify lands in llm_intel.db (SQLite) with
    the source URL and a capture timestamp, so it can be re-queried instead of
    re-searched, and audited instead of trusted.

LAYOUT
    model_intel/
        llm_intel.py    -- this CLI + DB layer
        baseline.json   -- curated dataset (edit this, then run `seed`)
        llm_intel.db    -- generated

WORKFLOW
    python llm_intel.py init          create schema (idempotent)
    python llm_intel.py seed          upsert everything in baseline.json
    python llm_intel.py stats         coverage summary (what's missing)
    python llm_intel.py models        table of models + latest CN price
    python llm_intel.py recompute     recalculate composite capability scores
    python llm_intel.py chart         dump capability-vs-price points as JSON
    python llm_intel.py q "SELECT..." arbitrary SQL, pretty table
    python llm_intel.py ls benchmarks list any table
    python llm_intel.py add-source --platform bilibili --url ... --title ...
    python llm_intel.py add-signal --model glm-5.3 --platform bilibili ...

COMPOSITE SCORE METHOD (see `recompute`)
    Leaderboards use incompatible units (Elo ~1500, SuperCLUE ~65, AA ~60).
    Raw averaging is meaningless. Instead: within each leaderboard, take only
    the rows flagged is_overall=1, rank the Chinese models, convert to a
    percentile p=(rank-1)/(n-1) where 0 is best, average each model's p across
    the boards that cover it, then score = 100*(1-p). Result: a relative
    standing, not an absolute ability measure.

CONVENTIONS
    - Prices are CNY per 1M tokens unless currency says otherwise.
    - captured_at is always ISO-8601 local (UTC+8) date, i.e. "2026-08-28".
    - confidence: verified (third party) | vendor_claimed | estimated.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "llm_intel.db")
BASELINE = os.path.join(HERE, "baseline.json")
CN_TZ = timezone(timedelta(hours=8))

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS vendors (
    id          TEXT PRIMARY KEY,
    name_zh     TEXT,
    name_en     TEXT,
    country     TEXT DEFAULT 'CN'
);

CREATE TABLE IF NOT EXISTS models (
    id                TEXT PRIMARY KEY,
    vendor_id         TEXT REFERENCES vendors(id),
    name              TEXT,
    generation        TEXT,
    tier              TEXT,            -- flagship | flash | lite
    release_date      TEXT,            -- YYYY-MM-DD or YYYY-MM-DD HH:MM
    release_precision TEXT,            -- hour | day | estimated
    total_params_b    REAL,
    active_params_b   REAL,
    arch              TEXT,
    context_in        INTEGER,
    context_out       INTEGER,
    multimodal_in     INTEGER DEFAULT 0,
    open_weights      INTEGER DEFAULT 0,
    license           TEXT,
    is_latest         INTEGER DEFAULT 1,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id      TEXT REFERENCES models(id),
    region        TEXT DEFAULT 'CN',
    currency      TEXT DEFAULT 'CNY',
    scenario      TEXT DEFAULT 'standard',  -- standard|offpeak|promo|plan_equiv
    input_per_m   REAL,
    cached_per_m  REAL,
    output_per_m  REAL,
    promo_until   TEXT,
    source_url    TEXT,
    captured_at   TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id                TEXT PRIMARY KEY,
    vendor_id         TEXT REFERENCES vendors(id),
    name              TEXT,
    price_cny_month   REAL,
    quota_desc        TEXT,
    models_allowed    TEXT,   -- JSON array
    client_whitelist  TEXT,   -- JSON array
    tos_note          TEXT,
    source_url        TEXT,
    captured_at       TEXT
);

CREATE TABLE IF NOT EXISTS benchmarks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT REFERENCES models(id),
    leaderboard  TEXT,     -- AA|SuperCLUE|OpenCompass|LMArena|ArenaCode|DataLearner
    metric       TEXT,     -- intelligence_index|elo|swe_bench|...
    value        REAL,
    is_overall   INTEGER DEFAULT 0,   -- 1 = feeds the composite score
    rank_in_board INTEGER,
    n_in_board   INTEGER,
    board_date   TEXT,
    confidence   TEXT DEFAULT 'verified',
    source_url   TEXT,
    captured_at  TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT,        -- bilibili|web|github_api|official_doc
    url          TEXT UNIQUE,
    title        TEXT,
    author       TEXT,
    published_at TEXT,
    fetched_at   TEXT,
    excerpt      TEXT,
    tags         TEXT
);

CREATE TABLE IF NOT EXISTS community_signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   TEXT REFERENCES models(id),
    platform   TEXT,
    ref_id     TEXT,      -- bilibili bvid etc
    author     TEXT,
    url        TEXT,
    sentiment  TEXT,      -- positive|negative|mixed
    engagement INTEGER,   -- likes
    quote      TEXT,
    captured_at TEXT
);

CREATE TABLE IF NOT EXISTS composite_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id    TEXT REFERENCES models(id),
    score       REAL,
    ci_low      REAL,
    ci_high     REAL,
    n_sources   INTEGER,
    method      TEXT,
    computed_at TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_bench_model ON benchmarks(model_id);
CREATE INDEX IF NOT EXISTS idx_price_model ON prices(model_id);
CREATE INDEX IF NOT EXISTS idx_signal_model ON community_signals(model_id);
"""


# ---------------------------------------------------------------- plumbing

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def now_cn():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def print_table(rows, maxw=44):
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = []
    for c in cols:
        w = max(len(str(c)), *(len(str(r[c])) for r in rows)) if rows else len(c)
        widths.append(min(w, maxw))
    def fmt(vals):
        out = []
        for i, v in enumerate(vals):
            s = "" if v is None else str(v)
            s = s.replace("\n", " ")
            if len(s) > maxw:
                s = s[: maxw - 1] + "\u2026"
            out.append(s.ljust(widths[i]))
        return " | ".join(out)
    print(fmt(cols))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt([r[c] for c in cols]))


# ---------------------------------------------------------------- commands

def cmd_init(args):
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    print(f"schema ready -> {DB_PATH}")


def _upsert(con, table, row, key="id"):
    cols = [c for c in row if row[c] is not None]
    if key not in cols:
        cols.append(key)
    placeholders = ", ".join("?" * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT({key}) DO UPDATE SET {updates}")
    con.execute(sql, [row[c] for c in cols])


def cmd_seed(args):
    """Upsert baseline.json. benchmarks/prices/sources are append-if-new to keep history."""
    with open(BASELINE, encoding="utf-8") as f:
        data = json.load(f)
    con = connect()
    con.executescript(SCHEMA)
    n = {}
    for t in ("vendors", "models"):
        for row in data.get(t, []):
            _upsert(con, t, row)
        n[t] = len(data.get(t, []))

    # Identity = (model, scenario, currency, promo, the actual numbers).
    # captured_at must NOT be part of it: baseline rows usually omit it and
    # fall back to today's date, so including it duplicated every price row
    # on every re-seed (17 -> 32 after one extra run).
    for row in data.get("prices", []):
        row.setdefault("captured_at", now_cn())
        args = (row["model_id"], row.get("scenario", "standard"),
                row.get("currency", "CNY"), row.get("promo_until"),
                row.get("input_per_m"), row.get("output_per_m"))
        hit = con.execute(
            "SELECT id FROM prices WHERE model_id=? AND scenario=? AND "
            "IFNULL(currency,'')=IFNULL(?,'') AND IFNULL(promo_until,'')=IFNULL(?,'') AND "
            "IFNULL(input_per_m,-1)=IFNULL(?,-1) AND IFNULL(output_per_m,-1)=IFNULL(?,-1) "
            "ORDER BY id LIMIT 1", args).fetchone()
        if hit:
            con.execute("UPDATE prices SET captured_at=?, source_url=?, region=?, "
                        "cached_per_m=? WHERE id=?",
                        (row["captured_at"], row.get("source_url"),
                         row.get("region", "CN"), row.get("cached_per_m"), hit["id"]))
        else:
            con.execute(
                "INSERT INTO prices(model_id,region,currency,scenario,input_per_m,"
                "cached_per_m,output_per_m,promo_until,source_url,captured_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row["model_id"], row.get("region", "CN"), row.get("currency", "CNY"),
                 row.get("scenario", "standard"), row.get("input_per_m"),
                 row.get("cached_per_m"), row.get("output_per_m"),
                 row.get("promo_until"), row.get("source_url"), row["captured_at"]))
    n["prices"] = len(data.get("prices", []))

    for row in data.get("plans", []):
        row.setdefault("captured_at", now_cn())
        _upsert(con, "plans", row)
    n["plans"] = len(data.get("plans", []))

    for row in data.get("benchmarks", []):
        row.setdefault("captured_at", now_cn())
        row.setdefault("confidence", "verified")
        exists = con.execute(
            "SELECT 1 FROM benchmarks WHERE model_id=? AND leaderboard=? AND metric=? "
            "AND IFNULL(board_date,'')=IFNULL(?,'')",
            (row["model_id"], row["leaderboard"], row["metric"], row.get("board_date"))
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO benchmarks(model_id,leaderboard,metric,value,is_overall,"
                "rank_in_board,n_in_board,board_date,confidence,source_url,captured_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (row["model_id"], row["leaderboard"], row["metric"], row["value"],
                 row.get("is_overall", 0), row.get("rank_in_board"),
                 row.get("n_in_board"), row.get("board_date"), row["confidence"],
                 row.get("source_url"), row["captured_at"]))
    n["benchmarks"] = len(data.get("benchmarks", []))

    for row in data.get("sources", []):
        row.setdefault("fetched_at", now_cn())
        con.execute(
            "INSERT INTO sources(platform,url,title,author,published_at,fetched_at,"
            "excerpt,tags) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET title=excluded.title, "
            "excerpt=excluded.excerpt, fetched_at=excluded.fetched_at, "
            "tags=excluded.tags",
            (row["platform"], row["url"], row.get("title"), row.get("author"),
             row.get("published_at"), row["fetched_at"], row.get("excerpt"),
             row.get("tags")))
    n["sources"] = len(data.get("sources", []))

    for row in data.get("community_signals", []):
        row.setdefault("captured_at", now_cn())
        exists = con.execute(
            "SELECT 1 FROM community_signals WHERE ref_id=? AND IFNULL(author,'')=IFNULL(?,'') "
            "AND IFNULL(quote,'')=IFNULL(?,'')",
            (row.get("ref_id"), row.get("author"), row.get("quote"))).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO community_signals(model_id,platform,ref_id,author,url,"
                "sentiment,engagement,quote,captured_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["model_id"], row["platform"], row.get("ref_id"), row.get("author"),
                 row.get("url"), row.get("sentiment"), row.get("engagement"),
                 row.get("quote"), row["captured_at"]))
    n["community_signals"] = len(data.get("community_signals", []))

    for row in data.get("composite_scores", []):
        row.setdefault("computed_at", now_cn())
        exists = con.execute("SELECT id FROM composite_scores WHERE model_id=? AND method=?",
                             (row["model_id"], row.get("method", "manual"))).fetchone()
        if exists:
            con.execute("UPDATE composite_scores SET score=?, ci_low=?, ci_high=?, "
                        "n_sources=?, note=?, computed_at=? WHERE id=?",
                        (row["score"], row.get("ci_low"), row.get("ci_high"),
                         row.get("n_sources"), row.get("note"), row["computed_at"],
                         exists[0]))
        else:
            con.execute("INSERT INTO composite_scores(model_id,score,ci_low,ci_high,"
                        "n_sources,method,note,computed_at) VALUES(?,?,?,?,?,?,?,?)",
                        (row["model_id"], row["score"], row.get("ci_low"),
                         row.get("ci_high"), row.get("n_sources"),
                         row.get("method", "manual"), row.get("note"), row["computed_at"]))
    n["composite_scores"] = len(data.get("composite_scores", []))

    con.commit()
    con.close()
    print("seeded:", ", ".join(f"{k}={v}" for k, v in n.items()))


def cmd_stats(args):
    con = connect()
    print("vendors          :", con.execute("SELECT COUNT(*) FROM vendors").fetchone()[0])
    print("models           :", con.execute("SELECT COUNT(*) FROM models").fetchone()[0])
    print("  latest gen only:", con.execute("SELECT COUNT(*) FROM models WHERE is_latest=1").fetchone()[0])
    print("prices           :", con.execute("SELECT COUNT(*) FROM prices").fetchone()[0])
    print("plans            :", con.execute("SELECT COUNT(*) FROM plans").fetchone()[0])
    print("benchmarks       :", con.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0])
    print("sources          :", con.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
    print("community signals:", con.execute("SELECT COUNT(*) FROM community_signals").fetchone()[0])
    print("\n-- coverage gaps (latest models missing a CN standard price) --")
    rows = con.execute("""
        SELECT m.id FROM models m
        WHERE m.is_latest=1 AND NOT EXISTS (
            SELECT 1 FROM prices p WHERE p.model_id=m.id AND p.scenario='standard')
    """).fetchall()
    print("  " + (", ".join(r[0] for r in rows) if rows else "none"))
    print("\n-- models with only 1 leaderboard source (low confidence) --")
    rows = con.execute("""
        SELECT model_id, COUNT(DISTINCT leaderboard) c
        FROM benchmarks WHERE is_overall=1 GROUP BY model_id HAVING c<=1
    """).fetchall()
    print("  " + (", ".join(f"{r[0]}({r[1]})" for r in rows) if rows else "none"))
    con.close()


def cmd_models(args):
    con = connect()
    rows = con.execute("""
        SELECT m.id, v.name_zh vendor, m.tier, m.release_date rel,
               m.context_in/1000 || 'K' ctx,
               p.input_per_m in_pm, p.output_per_m out_pm,
               m.multimodal_in mm, m.open_weights ow, m.license
        FROM models m
        LEFT JOIN vendors v ON v.id=m.vendor_id
        LEFT JOIN prices p ON p.id = (
            SELECT id FROM prices WHERE model_id=m.id AND scenario='standard'
            ORDER BY captured_at DESC LIMIT 1)
        WHERE m.is_latest=1
        ORDER BY p.output_per_m
    """).fetchall()
    print_table([dict(r) for r in rows])
    con.close()


SCALE = 12.0   # display scale: theta in ~[-2,2] -> score in [26,74]
K_PRIOR = 3.0   # empirical-Bayes prior strength, in "pseudo-weighted-z"


def cmd_recompute(args):
    """Weighted z-score composite with empirical-Bayes shrinkage (v2).

    Replaces the old percentile method. Each leaderboard's raw scores are
    converted to within-board z-scores, so scale differences disappear AND the
    *magnitude* of a model's lead is preserved (percentile kept only rank).

    Each z is weighted by:  w = conf_w * overall_w * src_w
        conf_w   : verified 1.0 | vendor_claimed 0.6 | estimated 0.4
        overall_w: is_overall=1 -> 1.0, else 0.7   (specialized boards count less)
        src_w    : leaderboard startswith 'llm-stats' -> 1.5  (external aggregate of
                   669 benchmarks = stronger signal than one hand-run board)

    Empirical-Bayes composite over z-space (prior mean mu0 = 0):
        theta = (sum w*z + K_PRIOR*0) / (sum w + K_PRIOR)
        se    = 1 / sqrt(sum w + K_PRIOR)
        ci    = theta +- 1.96*se
    Display score = 50 + SCALE*theta.

    All composite rows are cleared and recomputed. Low-coverage models are
    shrunk toward 50 with a wider CI — that is honest uncertainty, not a bug.
    Models absent from llm-stats (e.g. very new) will sit near 50 with a wide
    band; treat their score as "not yet established".
    """
    CONF_W = {"verified": 1.0, "vendor_claimed": 0.6, "estimated": 0.4}
    con = connect()
    rows = con.execute(
        "SELECT model_id, leaderboard, metric, value, is_overall, confidence "
        "FROM benchmarks").fetchall()
    boards = {}
    for r in rows:
        # Group by (leaderboard, metric), NOT leaderboard alone.
        # A single board can hold mutually incomparable scoring regimes under
        # different metric names -- e.g. ArenaCode carries elo_webdev (real
        # blind votes), elo_webdev_max (high-thinking variant, ~+60 Elo) and
        # elo_webdev_autoeval (AI temp score). Pooling them into one z
        # distribution silently converts a "thinking-budget bonus" into an
        # apparent capability gap. Grouping on the pair keeps each regime
        # self-contained.
        boards.setdefault((r["leaderboard"], r["metric"]), []).append(r)
    per_model = {}  # mid -> [(z, w), ...]
    skipped = []    # regimes with <2 models: cannot localise, contribute nothing
    for (board, metric), items in boards.items():
        vals = [float(x["value"]) for x in items]
        n = len(vals)
        if n < 2:
            # A lone observation has no position within a distribution; feeding
            # it z=0 would inflate sum(w) and fake precision. Drop it.
            skipped.append(f"{board}/{metric}")
            continue
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        sd = var ** 0.5
        if sd < 1e-9:
            sd = 1.0
        is_ext = board.startswith("llm-stats")
        for x in items:
            zz = (float(x["value"]) - mean) / sd
            conf = x["confidence"] or "verified"
            cw = CONF_W.get(conf, 1.0)
            ow = 1.0 if x["is_overall"] else 0.7
            sw = 1.5 if is_ext else 1.0
            per_model.setdefault(x["model_id"], []).append((zz, cw * ow * sw))
    con.execute("DELETE FROM composite_scores")  # full honest recompute
    method = ("weighted z-score + empirical-Bayes(K=3): within-board z, "
              "w=conf*overall*src; conf{v1,vendor0.6,est0.4}; overall{1,0.7}; "
              "src{llm-stats1.5}; theta=(Swz)/(Sw+K); score=50+12*theta")
    out = []
    for mid, zw in per_model.items():
        swz = sum(a * b for a, b in zw)
        sw = sum(b for _, b in zw)
        nb = len(zw)
        theta = swz / (sw + K_PRIOR)
        se = 1.0 / ((sw + K_PRIOR) ** 0.5)
        score = round(50 + SCALE * theta, 1)
        lo = round(max(0.0, 50 + SCALE * (theta - 1.96 * se)), 1)
        hi = round(min(100.0, 50 + SCALE * (theta + 1.96 * se)), 1)
        out.append((mid, score, lo, hi, nb, round(sw, 1)))
    for mid, s, lo, hi, nb, sw in sorted(out, key=lambda x: -x[1]):
        con.execute("INSERT INTO composite_scores(model_id,score,ci_low,ci_high,"
                    "n_sources,method,computed_at) VALUES(?,?,?,?,?,?,?)",
                    (mid, s, lo, hi, nb, method, now_cn()))
    con.commit()
    rows = con.execute(
        "SELECT model_id,score,ci_low,ci_high,n_sources FROM composite_scores "
        "ORDER BY score DESC").fetchall()
    print_table([dict(r) for r in rows])
    print(f"\nscoring regimes ingested: {len(boards) - len(skipped)}; "
          f"skipped (single model, no localisation): {len(skipped)}; "
          f"K={K_PRIOR}; SCALE={SCALE}")
    if skipped:
        print("  dropped: " + ", ".join(sorted(skipped)))
    print("note: grouped by (leaderboard, metric) so incomparable regimes "
          "(elo_webdev vs elo_webdev_max vs autoeval) never share a z-scale.")
    print("note: z preserves lead MAGNITUDE (not just rank); llm-stats x1.5 as "
          "external aggregate; low-coverage models shrunk to 50 with wider CI.")
    con.close()


def cmd_chart(args):
    con = connect()
    rows = con.execute("""
        SELECT c.model_id, m.name, v.name_zh vendor, c.score, c.ci_low, c.ci_high,
               c.n_sources, p.output_per_m, p.currency, m.multimodal_in,
               m.open_weights, m.tier
        FROM composite_scores c
        JOIN models m ON m.id=c.model_id
        LEFT JOIN vendors v ON v.id=m.vendor_id
        LEFT JOIN prices p ON p.id = (
            -- Currency first: the chart axis is CNY, so never let a USD row win.
            -- Then prefer an actual 'standard' price over an 'estimated' one.
            SELECT id FROM prices WHERE model_id=c.model_id
            ORDER BY CASE currency WHEN 'CNY' THEN 0 ELSE 1 END,
                     CASE scenario WHEN 'standard' THEN 0
                                   WHEN 'estimated' THEN 1 ELSE 2 END,
                     captured_at DESC
            LIMIT 1)
        ORDER BY c.score DESC
    """).fetchall()
    payload = [dict(r) for r in rows]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    con.close()


def cmd_q(args):
    con = connect()
    try:
        cur = con.execute(args.sql)
        rows = [dict(r) for r in cur.fetchall()]
        print_table(rows)
    except sqlite3.Error as e:
        print("SQL error:", e, file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


def cmd_ls(args):
    allowed = {"vendors", "models", "prices", "plans", "benchmarks", "sources",
               "community_signals", "composite_scores"}
    if args.table not in allowed:
        print("unknown table. options:", ", ".join(sorted(allowed)), file=sys.stderr)
        sys.exit(1)
    con = connect()
    rows = con.execute(f"SELECT * FROM {args.table}").fetchall()
    print_table([dict(r) for r in rows])
    con.close()


def cmd_add_source(args):
    con = connect()
    con.execute("INSERT INTO sources(platform,url,title,author,published_at,"
                "fetched_at,excerpt,tags) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title,"
                "excerpt=excluded.excerpt, fetched_at=excluded.fetched_at",
                (args.platform, args.url, args.title, args.author,
                 args.published_at, now_cn(), args.excerpt, args.tags))
    con.commit()
    con.close()
    print("source recorded:", args.url)


def cmd_add_signal(args):
    con = connect()
    con.execute("INSERT INTO community_signals(model_id,platform,ref_id,author,url,"
                "sentiment,engagement,quote,captured_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (args.model, args.platform, args.ref_id, args.author, args.url,
                 args.sentiment, args.engagement, args.quote, now_cn()))
    con.commit()
    con.close()
    print("signal recorded for", args.model)


def cmd_audit(args):
    """Read-only integrity audit: reconcile llm_intel.db against baseline.json.

    cmd_seed matches on (model_id, leaderboard, metric, board_date) and never
    deletes, so a row retired from baseline.json survives in the DB as a zombie
    and keeps voting in recompute(). That is exactly how the claude-fable-5
    AA/intelligence_index=62.0 row (retired 2026-09-03) survived until
    2026-09-05. This command surfaces that class of drift before it silently
    moves scores.

    Findings are split into ERROR (changes composite scores, or risks data
    loss) and WARN (known/expected, listed for visibility). Only ERROR affects
    the exit code, so a scheduled run does not cry wolf over the long tail of
    legacy rows that have no source_url.

    Exit code 0 = no ERROR findings, 1 = at least one ERROR finding.
    """
    import collections
    if not os.path.exists(BASELINE):
        print("baseline.json not found", file=sys.stderr)
        sys.exit(1)
    with open(BASELINE, encoding="utf-8") as f:
        data = json.load(f)
    con = connect()
    findings = collections.OrderedDict()
    # ERROR = silently changes composite scores, or risks data loss.
    # WARN  = known/expected condition, surfaced for visibility only.
    ERROR, WARN = "ERROR", "WARN"

    def report(name, rows, header, severity=WARN):
        if rows:
            findings[name] = (severity, header, rows)

    # --- 1/2/3. benchmark reconciliation on (model, leaderboard, metric) -----
    def bkey(m, l, me):
        return (m, l, me)

    base_b = collections.Counter(
        bkey(b["model_id"], b["leaderboard"], b["metric"]) for b in data.get("benchmarks", []))
    db_b = con.execute("SELECT id,model_id,leaderboard,metric,value,board_date,"
                       "confidence,source_url FROM benchmarks ORDER BY id").fetchall()
    db_count = collections.Counter(bkey(r["model_id"], r["leaderboard"], r["metric"]) for r in db_b)

    zom = [r for r in db_b if bkey(r["model_id"], r["leaderboard"], r["metric"]) not in base_b]
    report("zombie_rows", [dict(r) for r in zom],
           "in DB but absent from baseline (retired rows that still vote in recompute)",
           ERROR)

    dup = []
    for k, n_db in db_count.items():
        n_base = base_b.get(k, 0)
        # n_base == 0 means the whole key is unknown to baseline -- that is
        # already reported as a zombie row, so don't double-count it here.
        if n_base and n_db > n_base:
            dup.append({"model_id": k[0], "leaderboard": k[1], "metric": k[2],
                        "db_rows": n_db, "baseline_rows": n_base,
                        "db_values": [r["value"] for r in db_b
                                      if bkey(r["model_id"], r["leaderboard"], r["metric"]) == k]})
    report("duplicate_observations", dup,
           "same (model,board,metric) appears more often in DB than baseline", ERROR)

    missing = [{"model_id": k[0], "leaderboard": k[1], "metric": k[2]}
               for k in base_b if k not in db_count]
    report("missing_rows", missing, "in baseline but never seeded into DB", ERROR)

    # --- 4. structural hygiene ----------------------------------------------
    orphan_b = con.execute(
        "SELECT b.id,b.model_id,b.leaderboard,b.metric FROM benchmarks b "
        "WHERE NOT EXISTS(SELECT 1 FROM models m WHERE m.id=b.model_id)").fetchall()
    report("orphan_benchmarks", [dict(r) for r in orphan_b],
           "benchmark with no parent model", ERROR)

    orphan_p = con.execute(
        "SELECT p.id,p.model_id FROM prices p "
        "WHERE NOT EXISTS(SELECT 1 FROM models m WHERE m.id=p.model_id)").fetchall()
    report("orphan_prices", [dict(r) for r in orphan_p], "price with no parent model", ERROR)

    noprov = con.execute(
        "SELECT id,model_id,leaderboard,metric,value FROM benchmarks "
        "WHERE IFNULL(source_url,'')=''").fetchall()
    report("untraceable_benchmarks", [dict(r) for r in noprov],
           "benchmark with no source_url (cannot be verified)")

    nodate = con.execute(
        "SELECT id,model_id,leaderboard,metric,value FROM benchmarks "
        "WHERE IFNULL(board_date,'')=''").fetchall()
    report("undated_benchmarks", [dict(r) for r in nodate],
           "benchmark with no board_date (staleness unknown)")

    # --- 5. baseline completeness (it is the disaster-recovery copy) ---------
    have_sig = {(s.get("model_id"), s.get("ref_id"), s.get("author") or "", s.get("quote") or "")
                for s in data.get("community_signals", [])}
    extra_sig = [dict(r) for r in con.execute(
        "SELECT model_id,ref_id,author,quote FROM community_signals")
        if (r["model_id"], r["ref_id"], r["author"] or "", r["quote"] or "") not in have_sig]
    report("signals_missing_from_baseline", extra_sig,
           "community signal lives only in DB (lost if the DB is rebuilt)", ERROR)

    have_src = {s["url"] for s in data.get("sources", [])}
    extra_src = [dict(r) for r in con.execute("SELECT url,title FROM sources")
                 if r["url"] not in have_src]
    report("sources_missing_from_baseline", extra_src,
           "source lives only in DB (lost if the DB is rebuilt)", ERROR)

    # --- 6. boards that recompute() silently ignores -------------------------
    grp = collections.Counter((r["leaderboard"], r["metric"]) for r in db_b)
    singles = [{"leaderboard": k[0], "metric": k[1]} for k, n in grp.items() if n < 2]
    report("ignored_single_observation_boards", singles,
           "n=1 board: recompute drops it (no localisation) - expected, listed for visibility")

    con.close()

    print(f"integrity audit  |  db={DB_PATH}")
    print(f"                 |  baseline={BASELINE}")

    errs = {k: v for k, v in findings.items() if v[0] == ERROR}
    warns = {k: v for k, v in findings.items() if v[0] == WARN}

    verbose = getattr(args, "verbose", False)
    SHOW_LIMIT = 10 ** 9 if verbose else 12

    def dump(name, sev, header, rows):
        print(f"\n[{sev}] {name}  ({len(rows)})")
        print(f"    {header}")
        for r in rows[:SHOW_LIMIT]:
            print("    ", json.dumps(r, ensure_ascii=False))
        if len(rows) > SHOW_LIMIT:
            print(f"    ... and {len(rows) - SHOW_LIMIT} more "
                  f"(use --verbose to list all)")

    print()
    if not findings:
        print("clean: no drift found.")
        return

    # Summary first: what a scheduled run should look at.
    for name, (sev, header, rows) in findings.items():
        print(f"  {sev:<5} {len(rows):>3}  {name}")
    print()

    verbose = getattr(args, "verbose", False)
    for name, (sev, header, rows) in findings.items():
        if sev == ERROR or verbose:
            dump(name, sev, header, rows)

    if not verbose and warns:
        print("\n(WARN categories are known/expected conditions; "
              "run with --verbose to list them)")

    n_err = sum(len(v[2]) for v in errs.values())
    if n_err:
        print(f"\n{n_err} ERROR finding(s): composite scores or durable data are "
              f"affected. Fix before trusting a ranking.")
        sys.exit(1)
    print(f"\nno ERROR findings; {sum(len(v[2]) for v in warns.values())} WARN "
          f"finding(s) are informational.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("seed").set_defaults(func=cmd_seed)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("models").set_defaults(func=cmd_models)
    sub.add_parser("recompute").set_defaults(func=cmd_recompute)
    sub.add_parser("chart").set_defaults(func=cmd_chart)
    au = sub.add_parser("audit", help="read-only DB<->baseline integrity check")
    au.add_argument("--verbose", action="store_true",
                    help="also list WARN-level findings in full")
    au.set_defaults(func=cmd_audit)

    q = sub.add_parser("q"); q.add_argument("sql"); q.set_defaults(func=cmd_q)
    ls = sub.add_parser("ls"); ls.add_argument("table"); ls.set_defaults(func=cmd_ls)

    s = sub.add_parser("add-source")
    s.add_argument("--platform", required=True)
    s.add_argument("--url", required=True)
    s.add_argument("--title"); s.add_argument("--author")
    s.add_argument("--published-at"); s.add_argument("--excerpt"); s.add_argument("--tags")
    s.set_defaults(func=cmd_add_source)

    g = sub.add_parser("add-signal")
    g.add_argument("--model", required=True)
    g.add_argument("--platform", default="bilibili")
    g.add_argument("--ref-id"); g.add_argument("--author"); g.add_argument("--url")
    g.add_argument("--sentiment", choices=["positive", "negative", "mixed"])
    g.add_argument("--engagement", type=int); g.add_argument("--quote")
    g.set_defaults(func=cmd_add_signal)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
