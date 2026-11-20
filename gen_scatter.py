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
- Generation toggle (-1..-4), lane filter, vendor focus chips
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
TPL = os.path.join(HERE, "scatter_tpl.html")
OUT = os.path.join(HERE, "scatter_20261121.html")
DATE = "2026-11-21"

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
    "anthropic": ["claude-opus-5-5", "claude-fable-5-1"],
    "openai": ["gpt-6-astra", "gpt-6-sol", "gpt-6.1-terra", "gpt-6-luna"],
    "google": ["gemini-3-pro", "gemini-3-8-flash"],
    "xai": ["grok-4.7"],
    "meta": ["muse-spark-1-3"],
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
        "mm": bool(r["multimodal_in"]),
        "vendor": VENDOR_CN.get(r["vid"], r["vid"]),
    })

with open(os.path.join(HERE, "history.json"), encoding="utf-8") as f:
    hist_doc = json.load(f)

# 2026-11-21: the chart stays on API list price (single, comparable basis across
# all vendors). The subscription-amortised price for overseas models lives in the
# master table's dedicated column -- say so here instead of silently re-basing x.
hist_doc["meta"]["price_note"] = (
    "价格口径=API 挂牌输出价(¥/百万 token，按量标准价)。境内取厂商官方价目；境外取美元挂牌价按 7.1 折算。"
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

top = current[0]
insight = (
    "默认视图＝每个厂商的<b>代表性模型</b>（每条产品线取最新一代，OpenAI 的 Astra / Sol / Terra / "
    "Luna 四条线全保留）：<b>%d</b> 个。点「代表性模型」按钮可展开全部 <b>%d</b> 个在库模型。"
    "最强：<b>%s</b>（能力 %.1f 分，¥%.1f/M）。历史代（空心点）默认隐藏，可在下面按代叠加——"
    "历史代能力分为<b>估算</b>值。"
) % (sum(1 for p in current if p["id"] in REP_IDS), len(current),
     top["name"], top["score"], top["price"])

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
  curPts.push({ x:PX(p.price), y:p.score, _price:p.price, _free:false, _name:p.name,
    _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel), _gen:0, _src:"cur",
    _mm:!!p.mm, _lo:p.lo, _hi:p.hi, _n:p.n, _id:p.id, _rep:!!p.rep, _vis:true });
});
HIST.forEach(p=>{
  histPts.push({ x:PX(p.price), y:p.ability, _price:p.price, _free:(p.price===0),
    _name:p.name, _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel),
    _gen:p.gen, _src:"hist", _mm:!!p.mm, _conf:p.conf, _note:p.note, _vis:true });
});

// Elo-tab points: same x (price), y = Elo
const curEloPts = [], histEloPts = [];
CURELO.forEach(p=>{
  curEloPts.push({ x:PX(p.price), y:p.elo, _price:p.price, _free:false, _name:p.name,
    _vendor:p.vendor, _lane:laneOf(p.tier), _rel:relKey(p.rel), _gen:0, _src:"cur",
    _mm:!!p.mm, _id:p.id, _rep:!!p.rep, _vis:true });
});
HISTELO.forEach(p=>{
  histEloPts.push({ x:PX(p.price), y:p.elo, _price:p.price, _free:(p.price===0),
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
let showLabels = true, repOnly = true, laneFilter = 'all', focusVendor = null;
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
        title:{ display:true, text:'输出价 ¥/百万 token（对数）', color:'#b8b8c8' },
        ticks:{ color:'#9a9ab0' }, grid:{ color:'#2c2c46' } },
      y: { /* bounds are fitted to visible data at runtime (fitView) */
        title:{ display:true, text:'能力分 (v2 统合分；历史代为校准估算)', color:'#b8b8c8' },
        ticks:{ color:'#9a9ab0' }, grid:{ color:'#2c2c46' } }
    },
    plugins: {
      legend:{ display:false },
      tooltip:{ callbacks:{
        label: c=>{ const d=c.raw;
          if(d._src==="cur") return d._name+"（"+d._lane+"） 分 "+d.y.toFixed(1)+
            " ["+d._lo.toFixed(1)+"–"+d._hi.toFixed(1)+"] ¥"+d._price+"/M n="+d._n;
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

      if(!showLabels) return;

      // obstacles = every marker box; labels are added to the same list as they land
      const boxes = vis.map(v=>({x0:v.px-v.r-1, y0:v.py-v.r-1, x1:v.px+v.r+1, y1:v.py+v.r+1}));
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
        const w = c.measureText(v.d._name).width, h = FS+2;
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
            c.fillText(v.d._name, bx, by);
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
function vendorOk(v){ return !focusVendor || v===focusVendor; }

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
  chart.update('none');
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
  const pts = pool.filter(p=>p._vis);
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

document.getElementById('zin').onclick  = ()=> zoomCenter(0.85);
document.getElementById('zout').onclick = ()=> zoomCenter(1/0.85);
document.getElementById('zxin').onclick  = ()=> zoomAxis('x', 0.85);
document.getElementById('zxout').onclick = ()=> zoomAxis('x', 1/0.85);
document.getElementById('zyin').onclick  = ()=> zoomAxis('y', 0.85);
document.getElementById('zyout').onclick = ()=> zoomAxis('y', 1/0.85);
document.getElementById('fit').onclick  = ()=> resetView();

// ---- toggles ----
document.querySelectorAll('.tg').forEach(b=>{
  b.onclick=()=>{ const g=+b.dataset.gen; const vis=!chart.isDatasetVisible(g);
    chart.setDatasetVisibility(g,vis); b.classList.toggle('on',vis); applyFilters(); };
});
document.getElementById('all').onclick=()=>{
  [1,2,3,4].forEach(g=>{ chart.setDatasetVisibility(g,true);
    document.querySelector('.tg[data-gen="'+g+'"]').classList.add('on'); }); applyFilters(); };
document.getElementById('reset').onclick=()=>{
  [1,2,3,4].forEach(g=>{ chart.setDatasetVisibility(g,false);
    document.querySelector('.tg[data-gen="'+g+'"]').classList.remove('on'); }); applyFilters(); };
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
  const c=document.createElement('span'); c.className='chip';
  c.innerHTML='<i style="background:'+vcolor(v)+'"></i>'+v;
  c.onclick=()=>{
    if(focusVendor===v){ focusVendor=null; c.classList.remove('on'); }
    else { focusVendor=v; document.querySelectorAll('#chips .chip').forEach(x=>x.classList.remove('on')); c.classList.add('on'); }
    applyFilters();
  };
  chipBox.appendChild(c);
});

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
            .replace("__JS__", JS))

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

_nrep = sum(1 for p in current if p["id"] in REP_IDS)
_missing = REP_IDS - {p["id"] for p in current}
if _missing:
    print("[WARN] representative ids without a composite score (not plotted): " + str(sorted(_missing)))
print("[written] " + OUT + "  (current " + str(len(current)) + " + history " + str(len(hist)) +
      " ; default reps " + str(_nrep) + ")")
