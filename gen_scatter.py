#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the ability-vs-price scatter (template + generator split).

Reads scatter_tpl.html, fills placeholders, and writes scatter_20260901.html.

Data:
- Current generation: composite_scores (v2 composite, real) JOIN models JOIN
  vendors JOIN prices (latest CNY standard output price).
- Historical generations: history.json v2 (release date + price from web
  research; ability is an ESTIMATE mapped onto the same 33-65 axis).

Interactivity (fully self-implemented; log-axis correct):
- Drag = pan (content follows the cursor)
- Wheel = zoom anchored at pointer (toggleable to pan, for trackpad users)
- ＋/− zoom both axes; X＋/X−/Y＋/Y− zoom a single axis (aspect control)
- Default view on load & 复位 / double-click = fit to currently visible data
- Generation toggle (-1..-4), lane filter, vendor VISIBILITY chips (multi-select)
- Evolution lines (same vendor + same lane, arrow -> newer), show/hide
- Name labels, pareto (value) frontier toggles

Zoom/pan math: the price axis is LOGARITHMIC, so all x arithmetic runs in log
space (geometric shifts) while y runs in value space. Bounds are written to
chart.options.scales.* — Chart.js recomputes the resolved bounds from the
options on every update(), so editing chart.scales.* directly is silently
reverted. Drag deltas are accumulated and flushed once per rAF, because calling
chart.update() from inside the mousemove event corrupts the scale bounds.
"""
import sqlite3
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "llm_intel.db")
EFFORT_FILE = os.path.join(HERE, "effort_tiers.json")
EFFORT = {}
if os.path.exists(EFFORT_FILE):
    _e = json.load(open(EFFORT_FILE, encoding="utf-8"))
    for _mid, _d in _e.get("models", {}).items():
        # v2 (2026-11-21): anchor = vendor DEFAULT effort, so the model point is the
        # config you actually buy; lower rungs sit down-left, higher rungs up-right.
        # xmul = cost-per-task ratio vs the anchor (thinking burns tokens).
        EFFORT[_mid] = {"anchor": _d.get("anchor"),
                        "tiers": [{"effort": t["effort"], "aa": t["aa"], "dy": t["dy"],
                                   "xmul": t.get("xmul", 1.0),
                                   "anchor": bool(t.get("anchor"))}
                                  for t in _d["tiers"]]}
# Cost of ONE Artificial Analysis index task (2026-11-21: x axis switched from
# "list price per Mtok" to this). task_cost.json carries the measured AA value for
# the models AA benchmarks and a flagged placeholder for the rest.
TASK_FILE = os.path.join(HERE, "task_cost.json")
TASK, TASK_META = {}, {}
if os.path.exists(TASK_FILE):
    _tc = json.load(open(TASK_FILE, encoding="utf-8"))
    TASK, TASK_META = _tc.get("models", {}), _tc.get("_meta", {})
_TIN = TASK_META.get("tin", 300000)
_TOUT = TASK_META.get("tout", 90000)
_K = TASK_META.get("scale_k", 1.0)

def hist_task_cost(price_cny):
    """History rows only carry an output price (CNY). Same placeholder profile as
    task_cost.json, with input price assumed at the pool median p_in/p_out = 0.25."""
    if not price_cny:
        return None
    return (_TIN * (price_cny * 0.25) + _TOUT * price_cny) / 1e6 * _K

TPL = os.path.join(HERE, "scatter_tpl.html")
OUT = os.path.join(HERE, "scatter_20261122.html")
DATE = "2026-11-22"

# vendor_id -> canonical Chinese name used in history.json + color map
VENDOR_CN = {
    "zhipu": "智谱", "alibaba": "阿里", "deepseek": "DeepSeek", "moonshot": "Kimi",
    "tencent": "腾讯", "baidu": "百度", "xiaomi": "小米", "minimax": "MiniMax",
    "bytedance": "字节", "openai": "OpenAI", "anthropic": "Anthropic",
    "google": "Google", "xai": "xAI", "meta": "Meta",
}

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute("""
SELECT m.name, m.tier, m.release_date, v.id AS vid, c.model_id AS mid,
       c.score, c.ci_low, c.ci_high, c.n_sources,
       p.output_per_m, m.multimodal_in
FROM (
    -- one row per model: prefer the weighted-z composite over any legacy
    -- hand-inserted 'bridged' estimate that may linger from older runs
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY model_id
        ORDER BY CASE WHEN method LIKE 'bridged%' THEN 1 ELSE 0 END, id DESC
    ) AS rn
    FROM composite_scores
) c
JOIN models m ON m.id = c.model_id
JOIN vendors v ON v.id = m.vendor_id
LEFT JOIN prices p ON p.id = (
    SELECT id FROM prices WHERE model_id = c.model_id
    ORDER BY CASE currency WHEN 'CNY' THEN 0 ELSE 1 END,
             CASE scenario WHEN 'standard' THEN 0 WHEN 'estimated' THEN 1
                           WHEN 'promo' THEN 2 ELSE 3 END,
             captured_at DESC, id DESC LIMIT 1)
WHERE c.rn = 1 AND p.output_per_m IS NOT NULL
ORDER BY c.score DESC
""").fetchall()

# ---- "unrated" lane: models that are in the DB (and priced) but have NO
# composite score yet, so they cannot be plotted honestly. Typically
# freshly-launched models before any third-party leaderboard covers them.
# We surface them as a footnote instead of silently dropping them.
unrated = con.execute("""
SELECT m.name, m.release_date, m.is_latest, v.name_zh AS vendor,
       p.output_per_m, p.currency, p.scenario
FROM models m
JOIN vendors v ON v.id = m.vendor_id
LEFT JOIN prices p ON p.id = (
    SELECT id FROM prices WHERE model_id = m.id
    ORDER BY CASE currency WHEN 'CNY' THEN 0 ELSE 1 END,
             CASE scenario WHEN 'standard' THEN 0 WHEN 'estimated' THEN 1
                           WHEN 'promo' THEN 2 ELSE 3 END,
             captured_at DESC, id DESC LIMIT 1)
WHERE m.id NOT IN (SELECT model_id FROM composite_scores)
ORDER BY m.release_date DESC, m.name
""").fetchall()
con.close()

# ---- default view: one representative model per product line ----
# Keyed by (vendor, product line); every id listed here must exist in composite_scores.
# OpenAI is the documented exception: all four of its lines (Astra / Sol / Terra /
# Luna) stay visible by default, per user instruction.
REPRESENTATIVE = {
    "anthropic": ["claude-opus-5-5", "claude-fable-5-2", "claude-mythos-5-2",
                  "claude-sonnet-5-5", "claude-haiku-5-5"],
    "openai": ["gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-terra", "gpt-6-luna"],
    "google": ["gemini-4-neon", "gemini-4-carbon", "gemini-4-hydrogen"],
    "xai": ["grok-4.7"],
    "meta": ["muse-wonder", "muse-spark-1-3"],
    "zhipu": ["glm-5.3", "glm-5.3-flash"],
    "alibaba": ["qwen4-max", "qwen3.8-max", "qwen3.8-flash-next", "qwen3.8-27b"],
    "tencent": ["hy4-flash", "hy3"],
    "deepseek": ["deepseek-v4.1-flash", "deepseek-v4-pro"],
    "moonshot": ["kimi-k3.1-pro"],
    "bytedance": ["doubao-2.1-pro"],
    "baidu": ["ernie-5.5-flash", "ernie-5.1"],
    "xiaomi": ["mimo-v2.6-pro"],
    "minimax": ["minimax-m3", "minimax-m3.1-flash"],
}
REP_IDS = {i for ids in REPRESENTATIVE.values() for i in ids}

current = []
for r in rows:
    current.append({
        "id": r["mid"],
        "rep": r["mid"] in REP_IDS,
        "name": r["name"],
        "tier": r["tier"],
        "rel": (r["release_date"] or "")[:7],
        "score": r["score"],
        "lo": r["ci_low"],
        "hi": r["ci_high"],
        "n": r["n_sources"],
        "price": r["output_per_m"],
        "cost": (TASK.get(r["mid"]) or {}).get("cny"),
        "est": bool((TASK.get(r["mid"]) or {}).get("est", True)),
        "mm": bool(r["multimodal_in"]),
        "vendor": VENDOR_CN.get(r["vid"], r["vid"]),
        "tiers": ([{"effort": t["effort"], "aa": t["aa"], "dy": t["dy"],
                    "xmul": t["xmul"], "anchor": t["anchor"]}
                   for t in EFFORT[r["mid"]]["tiers"]] if r["mid"] in EFFORT else None),
    })

with open(os.path.join(HERE, "history.json"), encoding="utf-8") as f:
    hist_doc = json.load(f)

# 2026-11-21: the chart stays on API list price (single, comparable basis across
# all vendors). The subscription-amortised price for overseas models lives in the
# master table's dedicated column -- say so here instead of silently re-basing x.
hist_doc["meta"]["price_note"] = (
    "横轴=单任务成本(¥/次)：完成一次 Artificial Analysis Intelligence Index 任务的实际花费，"
    "取 AA intelligenceIndexCostPerTask（USD）×7.1。23 个模型为 AA 实测，14 个为推算（名字前带 ≈）。"
    "历史代无当年实测，按同一 token 画像从其输出价推算。"
    "挂牌价（¥/百万 token）仍在 tooltip 里给出，口径=API 按量标准价，境外美元按 7.1 折算。"
    "促销价/优惠价不入图。"
    "⚠️ 2026-11-21 起，境外模型在总表中另有一列「订阅折算价」= 挂牌价 × k（"
    "Anthropic Max 20x k≈2.5%、OpenAI Pro 200 k≈6.3%、Google Ultra 20x k≈5.8%，即满额度下的边际成本），"
    "该口径**未**用于本图横轴——因为它只对「额度用满」成立，与境内按量价混排会误导。两者不可直接比较。"
)

hist = []
for m in hist_doc["models"]:
    hist.append({
        "name": m["name"], "vendor": m["vendor"], "rel": m["rel"], "gen": m["gen"],
        "tier": m["tier"], "price": m["price"], "mm": bool(m["mm"]),
        "ability": m["ability"], "conf": m["conf"], "note": m.get("note", ""),
        "cost": hist_task_cost(m["price"]),
        "elo": m.get("elo"),
    })

# ---- Elo tab data: user-preference axis (Chatbot Arena, openlm snapshot) ----
# Current-gen models with a known Elo join the Elo tab too, so generations can
# be compared on ONE fixed ladder. Models without Elo are simply absent there.
CUR_ELO = hist_doc["meta"].get("cur_elo", {})
cur_elo_pts = []
for p in current:
    if p["name"] in CUR_ELO:
        cur_elo_pts.append({**p, "elo": CUR_ELO[p["name"]]})
hist_elo_pts = [p for p in hist if p.get("elo")]

# Vendors shown by default (user instruction 2026-11-21): the chart opens on this
# subset of the representative view; the rest are one click away in 显示厂商.
DEFAULT_VENDORS = ["OpenAI", "Kimi", "DeepSeek", "\u963f\u91cc", "\u667a\u8c31"]
_def_cur = [p for p in current if p["id"] in REP_IDS and p["vendor"] in DEFAULT_VENDORS]
top = max(_def_cur, key=lambda p: p["score"]) if _def_cur else current[0]
insight = (
    "默认视图＝<b>%s</b> 的代表性模型（每条产品线取最新一代，OpenAI 的 Astra / Sol / Terra / "
    "Luna 四条线全保留）：<b>%d</b> 个。点「代表性模型」按钮可展开全部 <b>%d</b> 个在库模型，"
    "其余 9 家厂商在「显示厂商」里一键打开。其中最强：<b>%s</b>（能力 %.1f 分，单任务 ¥%.2f）。"
    "历史代（空心点）默认隐藏，可在下面按代叠加——历史代能力分与单任务成本均为<b>估算</b>值。"
) % (" / ".join(DEFAULT_VENDORS), len(_def_cur), len(current),
     top["name"], top["score"], top["cost"])

CURRENT_JS = json.dumps(current, ensure_ascii=False)
HIST_JS = json.dumps(hist, ensure_ascii=False)
META_JS = json.dumps(hist_doc["meta"], ensure_ascii=False)
INSIGHT_JS = json.dumps(insight, ensure_ascii=False)
CUR_ELO_JS = json.dumps(cur_elo_pts, ensure_ascii=False)
HIST_ELO_JS = json.dumps(hist_elo_pts, ensure_ascii=False)

# ---------------------------------------------------------------------------
# The interactive JS. Hand-rolled zoom/pan + evolution lines + filters.
# ---------------------------------------------------------------------------
JS = r'''if (typeof Chart === 'undefined') {
  document.getElementById('insight').textContent =
    '⚠️ Chart.js 未加载：同目录下的 chart.umd.min.js 缺失或被移动，请确认它和本 HTML 在同一文件夹。';
  throw new Error('Chart is undefined');
}
// Vendor colours = brand colours (icon / VI), lifted only as far as a dark canvas
// needs. Blue is the dominant brand hue in this field, so the blue family keeps its
// brand hue (+-10 deg) and is separated by lightness/saturation instead -- every pair
// is >= 20 CIELAB dE (see _palette_check.py). Comments carry the brand source hex.
const VC = {
  "OpenAI":"#10A37F",   // ChatGPT green   (#10A37F, exact)
  "Anthropic":"#D97757",// Claude clay     (#D97757, exact)
  "Google":"#7193EE",   // Google blue     (brand #4285F4, lifted)
  "Meta":"#086EFF",     // Meta blue       (#0866FF)
  "xAI":"#8D929A",      // xAI is monochrome -> mid grey
  "智谱":"#0F0FFF",      // Zhipu blue      (#3859FF)
  "阿里":"#886EEF",      // Qwen violet     (#615CED)
  "DeepSeek":"#434FF4", // DeepSeek blue   (#4D6BFE)
  "Kimi":"#0DA3F2",     // Kimi blue       (#007CFF, official brand book)
  "腾讯":"#0C72E2",      // Tencent blue    (#0052D9)
  "百度":"#273DBA",      // Baidu blue      (#2932E1)
  "小米":"#FF6900",      // Xiaomi orange   (#FF6900, exact)
  "MiniMax":"#F03E3E",  // MiniMax red     (#F03E3E, exact)
  "字节":"#6A37FF"       // Doubao blue-violet (#5B4BFF)
};
const FALLBACK = ["#9aa0d0","#c0c4e8","#a0e0c8","#e0c89a"];
function hash(s){ let h=0; for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))|0; return Math.abs(h); }
function vcolor(v){ return VC[v] || FALLBACK[hash(v)%FALLBACK.length]; }
function hexA(hex,a){ const h=hex.replace('#',''); const r=parseInt(h.substr(0,2),16),
  g=parseInt(h.substr(2,2),16), b=parseInt(h.substr(4,2),16); return "rgba("+r+","+g+","+b+","+a+")"; }
const GEN_ALPHA = {1:0.95, 2:0.78, 3:0.6, 4:0.45};
const GEN_LABEL = {1:"-1代 2026上",2:"-2代 2025下",3:"-3代 2025上",4:"-4代+ 2024/23"};
const PX = p => p>0 ? p : 0.08;
const laneOf = t => (t==="flagship") ? "flagship" : "light";
const relKey = r => (r||"").slice(0,7);

// unified point objects
// Two axes (tabs) share one chart instance; switching tabs swaps datasets:
//   BENCH tab: y = v2 composite (cur real, hist estimated/anchored)
//   ELO tab  : y = Arena Elo raw (openlm 2026-09-01), same human-preference
//              ladder for ALL generations -> clean cross-gen comparison.
const curPts = [], histPts = [];
CURRENT.forEach(p=>{
  curPts.push({ x:PX(p.cost), y:p.score, _price:p.price, _cost:p.cost, _est:p.est,
    _free:false, _name:p.name,
    _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel), _gen:0, _src:"cur",
    _mm:!!p.mm, _lo:p.lo, _hi:p.hi, _n:p.n, _id:p.id, _rep:!!p.rep,
    _tiers:p.tiers, _vis:true });
});
HIST.forEach(p=>{
  histPts.push({ x:PX(p.cost), y:p.ability, _price:p.price, _free:(p.price===0),
    _name:p.name, _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel),
    _gen:p.gen, _src:"hist", _mm:!!p.mm, _conf:p.conf, _note:p.note, _vis:true });
});

// Elo-tab points: same x (per-task cost), y = Elo
const curEloPts = [], histEloPts = [];
CURELO.forEach(p=>{
  curEloPts.push({ x:PX(p.cost), y:p.elo, _price:p.price, _cost:p.cost, _est:p.est,
    _free:false, _name:p.name,
    _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel), _gen:0, _src:"cur",
    _mm:!!p.mm, _id:p.id, _rep:!!p.rep, _vis:true });
});
HISTELO.forEach(p=>{
  histEloPts.push({ x:PX(p.cost), y:p.elo, _price:p.price, _cost:p.cost, _free:(p.price===0),
    _name:p.name, _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel),
    _gen:p.gen, _src:"hist", _mm:!!p.mm, _conf:p.conf, _note:p.note, _vis:true });
});

// dataset factory: builds [currentDs, genDs...] for a point set
function makeDatasets(cPts, hPts){
  const cDs = {
    label:"当前代", data: cPts.slice(),
    pointRadius: cPts.map(p=> p._mm?9:7),
    pointStyle: cPts.map(p=> p._lane==="light"?"triangle":"circle"),
    backgroundColor: cPts.map(p=> vcolor(p._vendor)),
    borderColor: cPts.map(p=> "#ffffff"), borderWidth: cPts.map(p=>1),
  };
  const gDs = [1,2,3,4].map(g=>{
    const pts = hPts.filter(p=>p._gen===g);
    return {
      label: GEN_LABEL[g], data: pts.slice(),
      pointRadius: pts.map(p=> p._mm?8:6),
      pointStyle: pts.map(p=> p._lane==="light"?"triangle":"circle"),
      backgroundColor: "rgba(0,0,0,0)",
      borderColor: pts.map(p=> hexA(vcolor(p._vendor), GEN_ALPHA[g])),
      borderWidth: 2, _gen: g,
    };
  });
  return [cDs, ...gDs];
}

let datasets = makeDatasets(curPts, histPts);
let eloDatasets = null;   // built lazily on first Elo-tab switch

// ---- state (declared BEFORE new Chart: afterDatasetsDraw reads showLabels at first render) ----
let showLabels = true, repOnly = true, laneFilter = 'all';
// vendor chips are VISIBILITY toggles (all on by default), not a single-vendor focus
const hiddenVendors = new Set();
const DEFAULT_VENDORS = __DEFAULT_VENDORS__;
let hoverId = null;   // model name whose effort ladder is being shown
let wheelMode = 'zoom';                 // 'zoom' (default) | 'pan' (trackpad friendly)

const box = document.getElementById('box'), canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const chart = new Chart(ctx, {
  type: 'scatter',
  data: { datasets },
  options: {
    responsive: true, maintainAspectRatio: false,
    devicePixelRatio: window.devicePixelRatio || 1,
    scales: {
      x: { type:'logarithmic',
        title:{ display:true, text:'单任务成本 ¥/任务（对数；AA 指数一次任务）', color:'#b8b8c8' },
        // the cost axis spans ~3 decades; Chart.js otherwise emits a tick per minor
        // step and the labels collide into "7050 8060". Keep a clean 1/2/5 ladder.
        afterBuildTicks: (ax)=>{
          const lo = Math.log10(ax.min), hi = Math.log10(ax.max), keep = [];
          for(let e = Math.floor(lo); e <= Math.ceil(hi); e++){
            [1,2,5].forEach(m=>{
              const v = m * Math.pow(10, e);
              if(v >= ax.min*0.999 && v <= ax.max*1.001) keep.push({ value:v });
            });
          }
          ax.ticks = keep;
        },
        ticks:{ color:'#9a9ab0', callback:(v)=> (v>=1 ? String(Math.round(v)) : String(+v.toFixed(2))) },
        grid:{ color:'#2c2c46' } },
      y: { /* bounds are fitted to visible data at runtime (fitView) */
        title:{ display:true, text:'能力分 (v2 统合分；历史代为校准估算)', color:'#b8b8c8' },
        ticks:{ color:'#9a9ab0' }, grid:{ color:'#2c2c46' } }
    },
    plugins: {
      legend:{ display:false },
      tooltip:{ callbacks:{
        label: c=>{ const d=c.raw;
          if(d._src==="cur") return d._name+"（"+d._lane+"） 分 "+d.y.toFixed(1)+
            " ["+d._lo.toFixed(1)+"–"+d._hi.toFixed(1)+"]  单任务 "+
            (d._est?"≈":"")+"¥"+d._cost.toFixed(2)+
            "   ¥"+d._price+"/M n="+d._n;
          return d._name+" "+GEN_LABEL[d._gen]+" "+d._rel+" 分(估) "+
            d.y.toFixed(1)+" ¥"+(d._free?"免":d._price)+"/M ["+d._conf+"]"; },
        afterLabel: c=> c.raw._note?c.raw._note:"" }
      }
    },
    animation:false
  },
  plugins:[{
    id:'decorate',
    afterDatasetsDraw(chart){
      const x=chart.scales.x, y=chart.scales.y, c=chart.ctx;
      // ---- markers first (multimodal ring), then collision-free labels ----
      const vis = [];
      chart.data.datasets.forEach((ds,i)=>{
        if(!chart.isDatasetVisible(i)) return;
        ds.data.forEach(d=>{
          const px=x.getPixelForValue(d.x), py=y.getPixelForValue(d.y);
          if(px<2||px>chart.width-2||py<2||py>chart.height-2) return;
          vis.push({d, px, py, r: d._mm?11:(d._lane==="light"?5:6)});
        });
      });
      vis.forEach(v=>{
        if(!v.d._mm) return;
        c.save(); c.beginPath(); c.arc(v.px,v.py,11,0,Math.PI*2);
        c.strokeStyle="rgba(255,212,94,.9)"; c.lineWidth=1.5; c.stroke(); c.restore();
      });

      // ---- reasoning-effort ladder for the hovered model ----
      // LOCAL INSET, deliberately NOT plotted in data coordinates:
      //   * effort deltas reach +-13 composite points (Opus low<->max) while the whole
      //     y axis spans ~30 points -> a true-scale ladder leaves the plot;
      //   * cost multipliers reach 5.7x = 0.75 decades of the log x axis -> same problem.
      // So the ladder is scaled into a fixed pixel budget around the point (shrunk to
      // the room actually available), and each rung is LABELLED with the real numbers:
      // AA index + cost multiplier vs the vendor-default tier.
      const rungBoxes = [];
      if(hoverId){
        const hd = chart.data.datasets[0].data.find(p=> p._name === hoverId);
        if(hd && hd._tiers && hd._tiers.length > 1){
          const rows = hd._tiers.filter(t=> !t.anchor);
          const A0 = chart.chartArea || {left:0, top:0, right:chart.width, bottom:chart.height};
          if(rows.length){
            const px0 = x.getPixelForValue(hd.x), py0 = y.getPixelForValue(hd.y);
            const up = Math.max(0, ...rows.map(t=> t.dy));
            const dn = Math.max(0, ...rows.map(t=> -t.dy));

            // pixel budget per direction, clamped to the room actually available so a
            // rung can never be pushed outside the plot (the top point of the cloud has
            // little space above it -> that half compresses instead of flipping over)
            const clamp = (v,lo,hi)=> Math.max(lo, Math.min(hi, v));
            const UP = clamp(py0 - A0.top - 8, 24, 62);
            const DN = clamp(A0.bottom - py0 - 8, 24, 44);
            // screen y grows DOWNWARD: a positive dy (stronger) must subtract pixels
            const dyPx = t => t.dy === 0 ? 0
                           : t.dy > 0 ? -(up ? (t.dy/up)*UP : 0) : -(dn ? (t.dy/dn)*DN : 0);
            // x is now a REAL cost axis, so a rung's horizontal position is its own
            // measured cost (anchor x times the tier's cost ratio) -- no compression.
            const dxPx = t => x.getPixelForValue(hd.x * (t.xmul || 1)) - px0;
            const ax = px0, ay = py0;
            const rp = rows.map(t=> ({t, px: px0 + dxPx(t), py: ay + dyPx(t)}));
            c.save();
            c.setLineDash([3,3]); c.lineWidth = 1;
            c.strokeStyle = hexA(vcolor(hd._vendor), 0.55);
            c.beginPath(); c.moveTo(ax, ay);
            rp.slice().sort((a,b)=> a.t.aa - b.t.aa).forEach(o=> c.lineTo(o.px, o.py));
            c.stroke();
            c.setLineDash([]);
            // hollow ring marks the anchored (vendor-default) tier
            c.beginPath(); c.arc(ax, ay, 3.2, 0, Math.PI*2);
            c.fillStyle = "#0e0e18"; c.fill();
            c.strokeStyle = hexA(vcolor(hd._vendor), 0.95); c.lineWidth = 1.4; c.stroke();
            c.font = "10px -apple-system,Segoe UI,sans-serif"; c.textBaseline = "middle";
            // rung labels must dodge the markers too (a rung often lands right next to
            // another vendor's dot), so try 6 anchors and keep the first free one
            const mk = vis.map(v=>({x0:v.px-v.r-1, y0:v.py-v.r-1, x1:v.px+v.r+1, y1:v.py+v.r+1}));
            const hitB = b => mk.some(q=> !(b.x1<q.x0 || b.x0>q.x1 || b.y1<q.y0 || b.y0>q.y1))
                            || rungBoxes.some(q=> !(b.x1<q.x0 || b.x0>q.x1 || b.y1<q.y0 || b.y0>q.y1));
            rp.forEach(o=>{
              c.beginPath(); c.arc(o.px, o.py, 3.6, 0, Math.PI*2);
              c.fillStyle = hexA(vcolor(hd._vendor), 0.9); c.fill();
              c.strokeStyle = "#0e0e18"; c.lineWidth = 1; c.stroke();
              const txt = o.t.effort + " " + o.t.aa.toFixed(0) + " · ×" + o.t.xmul.toFixed(1);
              const w = c.measureText(txt).width, hh = 11;
              let box = null, tx = 0, ty = o.py, al = "left";
              for(const cnd of [[1,0],[-1,0],[1,-1],[1,1],[-1,-1],[-1,1]]){
                const sx = cnd[0], sy2 = cnd[1];
                const bx0 = sx > 0 ? o.px + 6 : o.px - 6 - w;
                const by = o.py + sy2 * hh;
                if(bx0 < 2 || bx0 + w > chart.width - 2) continue;
                const b = {x0: bx0, y0: by - hh/2 - 1, x1: bx0 + w, y1: by + hh/2 + 1};
                if(hitB(b)) continue;
                box = b; tx = sx > 0 ? o.px + 7 : o.px - 7; ty = by; al = sx > 0 ? "left" : "right"; break;
              }
              if(!box){
                const sx = (o.px + 6 + w < chart.width - 4) ? 1 : -1;
                const bx0 = sx > 0 ? o.px + 6 : o.px - 6 - w;
                box = {x0: bx0, y0: o.py - hh/2 - 1, x1: bx0 + w, y1: o.py + hh/2 + 1};
                tx = sx > 0 ? o.px + 7 : o.px - 7; al = sx > 0 ? "left" : "right";
              }
              c.textAlign = al; c.fillStyle = "#c9cfe4"; c.fillText(txt, tx, ty);
              rungBoxes.push(box);
            });
            c.restore();
          }
        }
      }

      if(!showLabels) return;

      // obstacles = every marker box; labels are added to the same list as they land
      const boxes = vis.map(v=>({x0:v.px-v.r-1, y0:v.py-v.r-1, x1:v.px+v.r+1, y1:v.py+v.r+1}))
                     .concat(rungBoxes);
      const area = chart.chartArea || {left:0, top:0, right:chart.width, bottom:chart.height};
      const PAD = 2, GAP = 8, GAP2 = 15, FS = 11;
      c.save(); c.font = FS+"px -apple-system,Segoe UI,sans-serif";
      const hits = b => boxes.some(o => !(b.x1 < o.x0 || b.x0 > o.x1 || b.y1 < o.y0 || b.y0 > o.y1));

      // current-gen first, then higher score; history labels only fill leftover room
      const order = vis.slice().sort((a,b)=>{
        const ca = a.d._src==="cur"?0:1, cb = b.d._src==="cur"?0:1;
        if(ca!==cb) return ca-cb;
        return b.d.y - a.d.y;
      });

      order.forEach(v=>{
        const lab = ((v.d._src==="cur" && v.d._est) ? "\u2248 " : "") + v.d._name;
        const w = c.measureText(lab).width, h = FS+2;
        let placed = false;
        for(const gap of [GAP, GAP2]){
          const CAND = [
            [ 1, 0],[-1, 0],[ 1,-1],[ 1, 1],[-1,-1],[-1, 1],[0,-1],[0, 1]
          ];
          for(const [sx,sy] of CAND){
            const bx = sx>0 ? v.px + v.r + gap
                     : sx<0 ? v.px - v.r - gap - w
                     : v.px - w/2;
            const by = sy>0 ? v.py + v.r + gap
                     : sy<0 ? v.py - v.r - gap - h
                     : v.py - h/2;
            const box = {x0:bx-PAD, y0:by-PAD, x1:bx+w+PAD, y1:by+h+PAD};
            if(box.x0 < area.left || box.x1 > area.right ||
               box.y0 < area.top  || box.y1 > area.bottom) continue;
            if(hits(box)) continue;
            c.textAlign = "left"; c.textBaseline = "top"; c.fillStyle = "#dde0f2";
            c.fillText(lab, bx, by);
            boxes.push(box);
            placed = true;
            break;
          }
          if(placed) break;
        }
        // no room anywhere -> no label (tooltip on hover still shows the name)
      });
      c.restore();
    }
  }]
});

// start with only current generation visible
[1,2,3,4].forEach(g=> chart.setDatasetVisibility(g,false));

function laneOk(l){ return laneFilter==='all' || laneFilter===l; }
function vendorOk(v){ return !hiddenVendors.has(v); }

let TAB = 'bench';   // 'bench' | 'elo'

function applyFilters(){
  // Write into the ACTIVE tab's dataset array. `datasets` is always the bench
  // array; on the elo tab chart.data.datasets is eloDatasets, so filling
  // datasets[0..4] there would update hidden arrays and the rendered chart
  // would keep showing stale, unfiltered points.
  const cDs = (TAB === 'elo') ? curEloPts : curPts;
  const hDs = (TAB === 'elo') ? histEloPts : histPts;
  const arr = (TAB === 'elo') ? eloDatasets : datasets;
  if(!arr){ return; }   // eloDatasets not built yet (first entry handled by switchTab)
  // rep-only is a BENCH-tab concept: the Elo tab exists for cross-generation
  // comparison and only 4 current models have an Elo, none of them "representative".
  // Gating it there left the tab empty (caught by the headless interaction test).
  const repGate = (TAB === 'elo') || !repOnly;
  cDs.forEach(p=> p._vis = laneOk(p._lane) && vendorOk(p._vendor) && (repGate || p._rep));
  hDs.forEach(p=> p._vis = laneOk(p._lane) && vendorOk(p._vendor) && chart.isDatasetVisible(p._gen));
  // 🔴 Per-point style arrays (color / radius / shape) are INDEX-ALIGNED with data.
  // Replacing `data` alone leaves them at the unfiltered length, so every point after
  // the first hidden one renders in the WRONG vendor colour. Rebuild them together.
  function setPts(ds, pts, isCur){
    ds.data = pts;
    ds.pointRadius = pts.map(p=> isCur ? (p._mm?9:7) : (p._mm?8:6));
    ds.pointStyle  = pts.map(p=> p._lane==="light"?"triangle":"circle");
    if(isCur){
      ds.backgroundColor = pts.map(p=> vcolor(p._vendor));
      ds.borderColor = pts.map(()=> "#ffffff");
      ds.borderWidth = pts.map(()=> 1);
    } else {
      ds.backgroundColor = pts.map(()=> "rgba(0,0,0,0)");
      ds.borderColor = pts.map(p=> hexA(vcolor(p._vendor), GEN_ALPHA[p._gen]));
      ds.borderWidth = 2;
    }
  }
  setPts(arr[0], cDs.filter(p=>p._vis), true);
  [1,2,3,4].forEach((_,i)=> setPts(arr[i+1], hDs.filter(p=> p._gen===(i+1) && p._vis), false));
  updateInsight();
  chart.update('none');
}

// The default view is a 5-vendor subset now, so a hard-coded "strongest model" line
// would name a model that is not on screen. Recompute it from what is actually drawn.
function updateInsight(){
  const el = document.getElementById('insight');
  if(!el) return;
  const pool = (TAB === 'elo') ? curEloPts : curPts;
  const vis = pool.filter(p=>p._vis);
  const vend = new Set(vis.map(p=>p._vendor));
  if(!vis.length){
    el.innerHTML = '\u26a0\ufe0f 当前没有显示任何模型 —— 检查「显示厂商」「代表性模型」「泳道」三个筛选，或点「显示厂商」里的「全部」。';
    return;
  }
  const best = vis.reduce((a,b)=> b.y > a.y ? b : a);
  const metric = (TAB === 'elo') ? 'Elo ' : '能力 ';
  // Elo only exists for 4 current models; with the 5-vendor default two of them are
  // hidden, so say which ones instead of leaving a 2-point chart looking broken.
  const eloEl = document.getElementById('elo-hidden');
  if(eloEl){
    const hid = curEloPts.filter(q=> !q._vis).map(q=> q._name);
    eloEl.innerHTML = hid.length
      ? ' 另有 ' + hid.length + ' 个有 Elo 的模型（' + hid.join('、') + '）被「显示厂商」关掉了。'
      : '';
  }
  el.innerHTML = '当前显示 <b>' + vis.length + '</b> / ' + pool.length +
    ' 个当前代模型，覆盖 <b>' + vend.size + '</b> 家厂商；其中最强：<b>' + best._name + '</b>（' +
    metric + best.y.toFixed(1) + '，单任务 ' + (best._est ? '\u2248' : '') +
    '\u00a5' + best._cost.toFixed(2) + '）。点「显示厂商」可增删厂商。';
}

// ---- tab switching ----
function switchTab(tab){
  if(tab === TAB) return;
  TAB = tab;
  document.getElementById('tab-bench').classList.toggle('on', tab==='bench');
  document.getElementById('tab-elo').classList.toggle('on', tab==='elo');
  if(tab === 'elo'){
    if(!eloDatasets){
      // The Elo tab exists FOR cross-generation comparison, so unlike the bench
      // tab (which starts current-only) all four generations start VISIBLE.
      // The bench tab's hidden state must NOT leak into the Elo tab (it would
      // show only the 4 current-Elo points and fitView would collapse the
      // y-window onto them -> "empty looking" chart).
      eloDatasets = makeDatasets(curEloPts, histEloPts);
      chart.data.datasets = eloDatasets;
      chart.data.datasets.forEach((ds,i)=> chart.setDatasetVisibility(i, true));
      document.querySelectorAll('.tg').forEach(b=> b.classList.add('on'));
    } else {
      chart.data.datasets = eloDatasets;
    }
    chart.options.scales.y.title.text = 'Arena Elo（用户盲测偏好；openlm 2026-09-01 快照）';
    chart.options.scales.y.min = null; chart.options.scales.y.max = null;
    document.getElementById('elo-note').style.display = '';
  } else {
    chart.data.datasets = datasets;
    chart.options.scales.y.title.text = '能力分 (v2 统合分；历史代为校准估算)';
    document.getElementById('elo-note').style.display = 'none';
  }
  applyFilters();
  fitView();   // re-fit: the two tabs have different y ranges
}
document.getElementById('tab-bench').onclick = ()=> switchTab('bench');
document.getElementById('tab-elo').onclick   = ()=> switchTab('elo');

// ---- zoom / pan ----
// chartjs-plugin-zoom is kept ONLY for its working pan()/resetZoom() methods;
// its built-in mouse/wheel handlers fail to attach in this Chart.js build and its
// zoom() is a no-op, so we drive pan + zoom with our own pointer handlers.
// Drag = pan (content follows cursor); Wheel = zoom anchored at the pointer.
// Both are log-axis correct: pan shifts geometrically, zoom scales in log space.
// All bounds are written to chart.options.scales.* because Chart.js recomputes the
// resolved scale bounds from the options on every update() (editing chart.scales.*
// directly is silently overwritten).
function curBounds(){
  const x = chart.scales.x, y = chart.scales.y;
  return {
    x0: chart.options.scales.x.min ?? x.min, x1: chart.options.scales.x.max ?? x.max,
    y0: chart.options.scales.y.min ?? y.min, y1: chart.options.scales.y.max ?? y.max
  };
}
// px/py are CANVAS-relative pixels; convert to chart-area-relative for getValueForPixel,
// because the scale's pixel origin is the plot area, not the canvas top-left.
function zoomAt(px, py, factor){
  const x = chart.scales.x, y = chart.scales.y, area = chart.chartArea;
  const ax = px - area.left, ay = py - area.top;
  const fx = x.getValueForPixel(ax), fy = y.getValueForPixel(ay);
  const b = curBounds();
  const ox = chart.options.scales.x, oy = chart.options.scales.y;
  if(x.type === 'logarithmic'){
    const lm = Math.log10(b.x0), lM = Math.log10(b.x1), fl = Math.log10(fx);
    ox.min = Math.pow(10, fl - (fl - lm)*factor);
    ox.max = Math.pow(10, fl + (lM - fl)*factor);
  } else { ox.min = fx - (fx - b.x0)*factor; ox.max = fx + (b.x1 - fx)*factor; }
  if(y.type === 'logarithmic'){
    const lm = Math.log10(b.y0), lM = Math.log10(b.y1), fl = Math.log10(fy);
    oy.min = Math.pow(10, fl - (fl - lm)*factor);
    oy.max = Math.pow(10, fl + (lM - fl)*factor);
  } else { oy.min = fy - (fy - b.y0)*factor; oy.max = fy + (b.y1 - fy)*factor; }
  if(ox.min < 0.02) ox.min = 0.02;
  if(ox.max <= ox.min) ox.max = ox.min * 2;
  chart.update('none');
}
// pan by a pixel delta (drag / trackpad). Content follows the cursor: dragging right
// shifts the window left, so min/max DECREASE. The shift is a fraction of the axis
// span, applied in value space (linear) or log space (price axis) — exact, with no
// getValueForPixel extrapolation (which is unreliable near the axis edges).
function panBy(dxPx, dyPx){
  const area = chart.chartArea;
  const b = curBounds();
  const ox = chart.options.scales.x, oy = chart.options.scales.y;
  // x: screen and data both grow rightward, so negate the fraction — the window
  // moves opposite the drag, i.e. content follows the cursor. y: screen y grows
  // DOWNWARD while data y grows UPWARD, so there the sign must NOT be flipped.
  const fx = -dxPx / area.width, fy = dyPx / area.height;
  if(chart.scales.x.type === 'logarithmic'){
    const span = Math.log10(b.x1) - Math.log10(b.x0);
    ox.min = Math.pow(10, Math.log10(b.x0) + fx*span);
    ox.max = Math.pow(10, Math.log10(b.x1) + fx*span);
  } else { ox.min = b.x0 + fx*(b.x1-b.x0); ox.max = b.x1 + fx*(b.x1-b.x0); }
  if(chart.scales.y.type === 'logarithmic'){
    const span = Math.log10(b.y1) - Math.log10(b.y0);
    oy.min = Math.pow(10, Math.log10(b.y0) + fy*span);
    oy.max = Math.pow(10, Math.log10(b.y1) + fy*span);
  } else { oy.min = b.y0 + fy*(b.y1-b.y0); oy.max = b.y1 + fy*(b.y1-b.y0); }
  if(ox.min < 0.02) ox.min = 0.02;
  if(ox.max <= ox.min) ox.max = ox.min * 2;
  chart.update('none');
}
// fit = make the currently visible points fill the plot with a small margin on
// BOTH axes. This is the default view on load and the target of 复位/double-click.
// x is padded in log space (7%), y in value space (8%).
function fitView(){
  // collect points from the ACTIVE tab only — the two axes have wildly
  // different y ranges (composite ~26-59 vs Elo ~1377-1486); fitting the
  // wrong set puts every Elo point outside the viewport (invisible chart).
  const pool = (TAB === 'elo') ? [...curEloPts, ...histEloPts] : [...curPts, ...histPts];
  // history rows keep _vis=true until applyFilters runs, so trusting _vis alone made
  // the axis reserve room for hidden generations; ask the chart what is actually shown.
  const pts = pool.filter(p=> p._vis && (p._src === "cur" || chart.isDatasetVisible(p._gen)));
  const ox = chart.options.scales.x, oy = chart.options.scales.y;
  if(!pts.length){ ox.min=null; ox.max=null; oy.min=null; oy.max=null; chart.update('none'); return; }
  let x0=Infinity, x1=-Infinity, y0=Infinity, y1=-Infinity;
  pts.forEach(p=>{ if(p.x<x0)x0=p.x; if(p.x>x1)x1=p.x; if(p.y<y0)y0=p.y; if(p.y>y1)y1=p.y; });
  let lx0=Math.log10(x0), lx1=Math.log10(x1);
  if(lx1-lx0 < 1e-9){ lx0-=0.3; lx1+=0.3; }        // a single x value: give it room
  const xp=(lx1-lx0)*0.07;
  ox.min=Math.max(0.02, Math.pow(10, lx0-xp));
  ox.max=Math.pow(10, lx1+xp);
  if(y1-y0 < 1e-9){ y0-=2; y1+=2; }                 // a single y value: give it room
  const yp=(y1-y0)*0.08;
  oy.min=y0-yp; oy.max=y1+yp;
  // The reasoning-effort ladder is drawn ABOVE the point for higher efforts. If the
  // top model of the cloud sits flush against the frame there is no room for that
  // half of the ladder, so reserve a slice of headroom for the tallest ladder among
  // the visible points (BENCH tab only; the Elo tab has no ladders).
  if(TAB !== 'elo'){
    let upMax = 0;
    pts.forEach(p=>{ if(p._tiers) p._tiers.forEach(t=>{ if(t.dy > upMax) upMax = t.dy; }); });
    if(upMax > 0) oy.max = Math.max(oy.max, y1 + upMax*0.55);
  }
  chart.update('none');
}
function resetView(){ fitView(); }
// per-axis zoom for aspect control: factor<1 zooms the axis IN, >1 zooms OUT,
// anchored at the axis center (log-space math on the price axis).
function zoomAxis(axis, factor){
  const s=chart.scales[axis], o=chart.options.scales[axis], b=curBounds();
  const v0=axis==='x'?b.x0:b.y0, v1=axis==='x'?b.x1:b.y1;
  let n0, n1;
  if(s.type==='logarithmic'){
    const lm=Math.log10(v0), lM=Math.log10(v1);
    n0=Math.pow(10, lm+(1-factor)*(lM-lm)/2);
    n1=Math.pow(10, lM-(1-factor)*(lM-lm)/2);
  } else {
    n0=v0+(1-factor)*(v1-v0)/2;
    n1=v1-(1-factor)*(v1-v0)/2;
  }
  o.min=n0; o.max=n1;
  if(axis==='x'){ if(o.min<0.02)o.min=0.02; if(o.max<=o.min)o.max=o.min*2; }
  chart.update('none');
}
function zoomCenter(factor){
  const r = canvas.getBoundingClientRect();
  zoomAt(r.width/2, r.height/2, factor);
}
function setWheelMode(mode){
  wheelMode = mode;
  const b = document.getElementById('wheelmode');
  b.textContent = '滚轮：' + (mode === 'zoom' ? '缩放' : '平移');
  b.classList.toggle('on', mode === 'pan');
}
document.getElementById('wheelmode').onclick =
  ()=> setWheelMode(wheelMode === 'zoom' ? 'pan' : 'zoom');

// wheel: zoom anchored at pointer (default), or pan when toggled (trackpad friendly)
canvas.addEventListener('wheel', e=>{
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  if(wheelMode === 'pan'){
    panBy(e.deltaX, e.deltaY);
  } else {
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 0.85 : 1/0.85);
  }
}, {passive:false});
canvas.addEventListener('dblclick', ()=> resetView());

// drag = pan (content follows the cursor).
// Deltas are accumulated and flushed once per animation frame: calling chart.update()
// from *inside* the mousemove event interferes with Chart.js's own event dispatch (and
// any registered plugin hooks), which corrupts the scale bounds. Deferring the update
// to rAF — outside the event — keeps it clean. One update per frame is also smoother.
let dragging = false, lastX = 0, lastY = 0, pendX = 0, pendY = 0, rafPending = false;
function flushPan(){
  rafPending = false;
  if(pendX !== 0 || pendY !== 0){ panBy(pendX, pendY); pendX = 0; pendY = 0; }
}
canvas.addEventListener('mousedown', e=>{
  dragging = true; lastX = e.clientX; lastY = e.clientY; box.classList.add('dragging');
});
window.addEventListener('mouseup', ()=>{ if(dragging){ dragging = false; box.classList.remove('dragging'); } });
window.addEventListener('mousemove', e=>{
  if(!dragging) return;
  pendX += e.clientX - lastX; pendY += e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY;
  if(!rafPending){ rafPending = true; requestAnimationFrame(flushPan); }
});

// ---- hover: reveal the reasoning-effort ladder of the nearest point ----
canvas.addEventListener('mousemove', ev=>{
  if(dragging) return;
  const rect = canvas.getBoundingClientRect();
  const mx = (ev.clientX - rect.left) * (chart.width / rect.width);
  const my = (ev.clientY - rect.top)  * (chart.height / rect.height);
  const xs = chart.scales.x, ys = chart.scales.y;
  let best = null, bd = 1e9;
  chart.data.datasets[0].data.forEach(d=>{
    const px = xs.getPixelForValue(d.x), py = ys.getPixelForValue(d.y);
    const dd = (px-mx)*(px-mx) + (py-my)*(py-my);
    if(dd < bd){ bd = dd; best = d; }
  });
  const nid = (best && bd <= 400 && best._tiers) ? best._name : null;
  if(nid !== hoverId){ hoverId = nid; chart.update('none'); }
});
canvas.addEventListener('mouseleave', ()=>{
  if(hoverId){ hoverId = null; chart.update('none'); }
});
window.__hoverModel = n=>{ hoverId = n; chart.update('none'); };

document.getElementById('zin').onclick  = ()=> zoomCenter(0.85);
document.getElementById('zout').onclick = ()=> zoomCenter(1/0.85);
document.getElementById('zxin').onclick  = ()=> zoomAxis('x', 0.85);
document.getElementById('zxout').onclick = ()=> zoomAxis('x', 1/0.85);
document.getElementById('zyin').onclick  = ()=> zoomAxis('y', 0.85);
document.getElementById('zyout').onclick = ()=> zoomAxis('y', 1/0.85);
document.getElementById('fit').onclick  = ()=> resetView();

// ---- toggles ----
// toggling a generation changes the x range (history task costs are wider than the
// current-gen spread), so these three also re-fit -- vendor/lane/rep filters do not.
document.querySelectorAll('.tg').forEach(b=>{
  b.onclick=()=>{ const g=+b.dataset.gen; const vis=!chart.isDatasetVisible(g);
    chart.setDatasetVisibility(g,vis); b.classList.toggle('on',vis); applyFilters(); fitView(); };
});
document.getElementById('all').onclick=()=>{
  [1,2,3,4].forEach(g=>{ chart.setDatasetVisibility(g,true);
    document.querySelector('.tg[data-gen="'+g+'"]').classList.add('on'); }); applyFilters(); fitView(); };
document.getElementById('reset').onclick=()=>{
  [1,2,3,4].forEach(g=>{ chart.setDatasetVisibility(g,false);
    document.querySelector('.tg[data-gen="'+g+'"]').classList.remove('on'); }); applyFilters(); fitView(); };
document.querySelectorAll('.lane').forEach(b=>{
  b.onclick=()=>{ laneFilter=b.dataset.lane;
    document.querySelectorAll('.lane').forEach(x=>x.classList.toggle('on', x===b)); applyFilters(); };
});
document.getElementById('btn-rep').onclick=()=>{
  repOnly=!repOnly; document.getElementById('btn-rep').classList.toggle('on',repOnly);
  document.getElementById('btn-rep').textContent = repOnly ? '代表性模型' : '全部当前代';
  applyFilters(); };
document.getElementById('btn-label').onclick=()=>{
  showLabels=!showLabels; document.getElementById('btn-label').classList.toggle('on',showLabels); chart.update('none'); };

// ---- vendor focus chips ----
const vendors=[...new Set([...curPts,...histPts].map(p=>p._vendor))];
const chipBox=document.getElementById('chips');
vendors.forEach(v=>{
  const on = DEFAULT_VENDORS.indexOf(v) >= 0;
  if(!on) hiddenVendors.add(v);
  const c=document.createElement('span'); c.className = 'chip' + (on ? ' on' : '');
  c.title = '点击隐藏 / 显示该厂商';
  c.innerHTML='<i style="background:'+vcolor(v)+'"></i>'+v;
  c.onclick=()=>{
    if(hiddenVendors.has(v)){ hiddenVendors.delete(v); c.classList.add('on'); }
    else { hiddenVendors.add(v); c.classList.remove('on'); }
    syncAllChip();
    applyFilters();
  };
  chipBox.appendChild(c);
});
// "全部" resets the visibility set -- with 14 vendors, undoing a few clicks by hand
// is the annoying case.
const allChip = document.createElement('span');
allChip.className = 'chip all'; allChip.title = '显示全部厂商';
allChip.textContent = '全部';
allChip.onclick = ()=>{ hiddenVendors.clear();
  document.querySelectorAll('#chips .chip:not(.all)').forEach(x=> x.classList.add('on'));
  syncAllChip(); applyFilters(); };
chipBox.appendChild(allChip);
function syncAllChip(){ allChip.classList.toggle('on', hiddenVendors.size === 0); }
syncAllChip();

// ---- meta + insight ----
document.getElementById('m1').textContent = META.methodology;
document.getElementById('m2').textContent = META.price_note;
document.getElementById('m3').textContent = META.conf_legend + ' ｜ ' + META.gen_legend;
document.getElementById('m4').textContent = META.lane_note + ' ｜ 注意：' + META.caveat;
document.getElementById('insight').innerHTML = __INSIGHT__;

applyFilters();
fitView();   // default view: visible data fills the plot on both axes
chart.update();
window.__chart = chart;  // expose for console debugging
window.__panBy = panBy; window.__zoomAt = zoomAt; window.__resetView = resetView;
'''

JS = JS.replace("__INSIGHT__", INSIGHT_JS)
JS = JS.replace("__DEFAULT_VENDORS__", json.dumps(DEFAULT_VENDORS, ensure_ascii=False))

# ---- render the unrated lane as a static HTML block ----
if unrated:
    _items = []
    for u in unrated:
        if u["output_per_m"] is None:
            _price = "无 API 定价"
        else:
            _cur = "¥" if u["currency"] == "CNY" else "$"
            _scen = {"standard": "标准", "offpeak": "闲时", "estimated": "折算"}.get(u["scenario"], u["scenario"])
            _price = f"{_cur}{u['output_per_m']:g}/百万 token（{_scen}）"
        _ret = "" if u["is_latest"] else " · 已退役"
        _items.append(
            f"<li><b>{u['name']}</b>（{u['vendor']}，{u['release_date'] or '日期未定'}）— "
            f"输出价 {_price}{_ret}</li>")
    UNRATED_HTML = (
        '<div class="card" style="margin-top:14px">'
        '<div class="sub" style="margin:0 0 6px"><b>待评级</b>'
        '｜以下模型已入库并已定价，但<b>尚无任何第三方独立评测收录</b>，'
        '没有可信的能力分，因此<b>不出现在上方散点图中</b>（放进图里会等于编造纵轴）。'
        '待 llm-stats / vals.ai / AA 覆盖后自动补入。</div>'
        '<ul style="margin:0;padding-left:20px;line-height:1.9">'
        + "".join(_items) + '</ul></div>')
else:
    UNRATED_HTML = ""

with open(TPL, encoding="utf-8") as f:
    html = f.read()

# Inline Chart.js so the page is fully self-contained (no CDN / relative-path
# dependency). The zoom/pan interaction is fully self-implemented (see JS below);
# chartjs-plugin-zoom was removed: its pan handler collided with ours (double pan).
def _inline_js(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return "<script>\n" + f.read().strip() + "\n</script>"

html = (html.replace("__CHARTJS__", _inline_js("chart.umd.min.js"))
            .replace("__DATE__", DATE)
            .replace("__NCUR__", str(len(current)))
            .replace("__NHIS__", str(len(hist)))
            .replace("__CURRENT__", CURRENT_JS)
            .replace("__HIST__", HIST_JS)
            .replace("__META__", META_JS)
            .replace("__UNRATED_HTML__", UNRATED_HTML)
            .replace("__CURELO__", CUR_ELO_JS)
            .replace("__HISTELO__", HIST_ELO_JS)
            .replace("__NMEAS__", str(TASK_META.get("n_measured", "-")))
            .replace("__NEST__", str(TASK_META.get("n_estimated", "-")))
            .replace("__JS__", JS))

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

_nrep = sum(1 for p in current if p["id"] in REP_IDS)
_missing = REP_IDS - {p["id"] for p in current}
if _missing:
    print("[WARN] representative ids without a composite score (not plotted): " + str(sorted(_missing)))
print("[written] " + OUT + "  (current " + str(len(current)) + " + history " + str(len(hist)) +
      " ; default reps " + str(_nrep) + ")")
