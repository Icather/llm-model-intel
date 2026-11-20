#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily refresh 2026-09-01: sync baseline.json with fresh web-researched data.

Changes applied (idempotent on re-seed: benchmark identity = model+board+metric+board_date):
1. NEW MODEL qwen3.8-27b  (Qwen3.8-27B, released 2026-08-05, Apache-2.0 dense 27.8B,
   native vision-language). llm-stats Overall 45.5 / Reasoning 45.1 / Coding 32.4;
   vals.ai SWE-bench Verified 86.0 (8/30 board). Price: Aliyun doc $0.424/$1.696
   (intl standard) + estimated CNY 3.0/12.0 for chart axis.
2. NEW MODEL hy4-lite     (Hy4 preview 轻量版, released 2026-09-01, Tencent):
   1.5TB -> 214GB via Sherry sparse ternary quantization (avg 1.25-bit) +
   MIX-STQ1_0 mixed precision; heterogeneous joint inference 1.02 tok/s
   (4090 laptop + 4x A4000, 80GB VRAM + 64GB RAM), ~6x faster than laptop
   offload; GGUF + code open-sourced. No API price / no leaderboard coverage.
3. COVERAGE GAP FILL (llm-stats board_date 2026-09-01):
   doubao-2.1-pro  (Seed 2.1 Pro)  Overall 46.8 / Reasoning 47.6 / Coding 34.5
   deepseek-v4-flash (V4-Flash-0731) Overall 45.8 / Reasoning 43.2 / Coding 34.4
   deepseek-v4-flash-vision-exp      Overall 47.7 / Reasoning 44.3 / Coding 36.6
   (these three had NO llm-stats rows -> composite score now has real anchor)
4. SWE-bench Verified (vals.ai, board 2026-08-30):
   glm-5.3-flash 92.0 (new — beats Qwen3.8 Max 85.6 & V4 Flash 88.8 on same harness)
   qwen3.8-27b   86.0
5. Sources: Tencent Hy4-lite / Hy3 free-extend to 9/30 / Aliyun Qwen3-VL-Rerank
   price cut (0.7->0.5, 1.8->0.5) / 智谱中报+GLM-6.0 route / DeepSeek V4 Pro 0813
   launch & price up / Kimi legacy models offline 8/31 / Qwen3.8-27B release.
6. Community signals: 9 fresh Bilibili items (Hy4 tests, GLM-Flash vision repair,
   V4-Flash local DSH, Qwen3.8-27B local runs, GLM-Flash vs V4-Flash game-build
   time test).

NOTE: llm-stats values for previously tracked models are unchanged vs the
8/31 snapshot (verified by live fetch today) -> no value updates for those.
Qwen3.8 Flash (production) listed separately on llm-stats at 49.6 Overall is the
production superset of Flash-Next (per 8/31 verification) -> NOT added as a
duplicate model; noted in report instead.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.path.join(HERE, "baseline.json")
d = json.load(open(B, encoding="utf-8"))

DATE = "2026-09-01"
SWE_BOARD_DATE = "2026-08-30"
LS = "https://llm-stats.com/leaderboards/llm-leaderboard"
VALS = "https://www.vals.ai/benchmarks/swebench"


def add_model(m):
    ids = {x["id"] for x in d["models"]}
    if m["id"] not in ids:
        d["models"].append(m)
        print("+ model", m["id"])
    else:
        print("= model exists", m["id"])


def add_bench(mid, lb, metric, val, is_ov, bdate, src, conf="verified"):
    for b in d["benchmarks"]:
        if (b["model_id"] == mid and b["leaderboard"] == lb and b["metric"] == metric
                and b.get("board_date") == bdate):
            if b.get("value") != val:
                b["value"] = val
                print("~ bench updated", mid, lb, metric)
            return
    d["benchmarks"].append({
        "model_id": mid, "leaderboard": lb, "metric": metric, "value": val,
        "is_overall": is_ov, "board_date": bdate, "confidence": conf,
        "source_url": src,
    })
    print("+ bench", mid, lb, metric, val)


def add_price(mid, scenario, cur, inp, out, src, cached=None, note=None):
    for p in d["prices"]:
        if (p.get("model_id") == mid and p.get("scenario") == scenario
                and p.get("currency") == cur):
            p["input_per_m"] = inp
            p["output_per_m"] = out
            p["source_url"] = src
            if cached is not None:
                p["cached_per_m"] = cached
            if note:
                p["note"] = note
            p["captured_at"] = DATE
            print("~ price updated", mid, scenario, cur)
            return
    row = {"model_id": mid, "region": "CN" if cur == "CNY" else "INTL",
           "currency": cur, "scenario": scenario, "input_per_m": inp,
           "output_per_m": out, "promo_until": None, "source_url": src,
           "captured_at": DATE}
    if cached is not None:
        row["cached_per_m"] = cached
    if note:
        row["note"] = note
    d["prices"].append(row)
    print("+ price", mid, scenario, cur, inp, out)


def add_source(platform, url, title, author=None, excerpt=None, tags=None):
    for s in d["sources"]:
        if s.get("url") == url:
            print("= source exists", url[:60])
            return
    d["sources"].append({
        "platform": platform, "url": url, "title": title, "author": author,
        "published_at": DATE, "fetched_at": DATE, "excerpt": excerpt,
        "tags": tags,
    })
    print("+ source", title[:50])


def add_signal(mid, bvid, author, sentiment, eng, quote):
    for s in d.get("community_signals", []):
        if s.get("ref_id") == bvid and s.get("author") == author:
            print("= signal exists", bvid)
            return
    d.setdefault("community_signals", []).append({
        "model_id": mid, "platform": "bilibili", "ref_id": bvid,
        "author": author, "url": f"https://www.bilibili.com/video/{bvid}",
        "sentiment": sentiment, "engagement": eng, "quote": quote,
        "captured_at": DATE,
    })
    print("+ signal", bvid)


# ---------------- 1. new models ----------------
add_model({
    "id": "qwen3.8-27b",
    "vendor_id": "alibaba",
    "name": "Qwen3.8-27B",
    "generation": "3.8",
    "tier": "lite",
    "release_date": "2026-08-05",
    "release_precision": "day",
    "total_params_b": 27.8,
    "active_params_b": 27.8,
    "arch": "Dense, native vision-language",
    "context_in": 262144,
    "context_out": 131072,
    "multimodal_in": 1,
    "open_weights": 1,
    "license": "Apache-2.0",
    "is_latest": 1,
    "notes": ("2026-08-05 开源（Apache-2.0，dense 27.8B，原生视觉语言，image-text-to-text）；"
              "FP8 版 8/13 跟进。8/22 HF 下载 2,090,699（12,021 likes）——Qwen3.8 家族下载王，"
              "约 Max 权重(17,386)的 120 倍。4-bit 可跑 24GB 卡（RTX 3090/4090）；AMD AI Max 级 "
              "24.5-51.8 tok/s。Aliyun Model Studio API：国际 $0.424/$1.696（另档 $0.5/$3.0），"
              "1M 上下文（Alibaba 文档称 max input 991,808），原生文+图+视频输入。与 Flash-Next 对比："
              "27B 本地单卡可跑、许可干净；Flash-Next 每 token 更便宜但需大内存装 176B 权重。"
              "vals.ai SWE-bench Verified 86.0%（Mini-SWE-agent，8/30）。"),
})

add_model({
    "id": "hy4-lite",
    "vendor_id": "tencent",
    "name": "Hy4 preview 轻量版",
    "generation": "4",
    "tier": "lite",
    "release_date": "2026-09-01",
    "release_precision": "day",
    "total_params_b": None,
    "active_params_b": None,
    "arch": "Hy4 MoE 压缩（Sherry 稀疏三值量化 + MIX-STQ1_0 混合精度）",
    "context_in": 1048576,
    "context_out": 131072,
    "multimodal_in": 0,
    "open_weights": 1,
    "license": "Apache-2.0",
    "is_latest": 1,
    "notes": ("2026-09-01 腾讯混元发布 Hy4 preview 轻量版：权重由接近 1.5TB 压至约 214GB。"
              "两项技术：①自研 Sherry 稀疏三值量化，最激进档权重压至平均 1.25 bit；"
              "②MIX-STQ1_0 混合精度策略，按敏感度逐层决定比特数。主流任务能力保持稳定："
              "长文理解、多轮长上下文检索与原始版基本持平，数学小幅回落；日常编码辅助/工具调用/"
              "长文档/问答可覆盖。异构设备联合推理已验证：4090 笔记本 + 四卡 A4000 服务器"
              "（显存合计 80GB + 内存 64GB）协同 1.02 tok/s，较笔记本单机 offload 快约 6 倍。"
              "量化 GGUF 库及代码已同步开源。无独立 API 定价（非 API 产品线）。"),
})

# ---------------- 2. prices ----------------
add_price("qwen3.8-27b", "standard", "USD", 0.424, 1.696,
          "https://www.alibabacloud.com/help/tc/model-studio/qwen3-8-27b",
          cached=0.085,
          note="阿里云 Model Studio 国际价（另有 $0.5/$3.0 档）；缓存命中 $0.085。")
add_price("qwen3.8-27b", "estimated", "CNY", 3.0, 12.0,
          "https://www.alibabacloud.com/help/tc/model-studio/qwen3-8-27b",
          note="按 7.1 汇率由 $0.424/$1.696 折算的估计人民币价（无官方 CNY 公布）。")

# ---------------- 3. benchmark coverage fill ----------------
for mid, ov, rs, cd in [
    ("doubao-2.1-pro", 46.8, 47.6, 34.5),
    ("deepseek-v4-flash", 45.8, 43.2, 34.4),
    ("deepseek-v4-flash-vision-exp", 47.7, 44.3, 36.6),
    ("qwen3.8-27b", 45.5, 45.1, 32.4),
]:
    add_bench(mid, "llm-stats-Overall", "ts_overall", ov, 1, DATE, LS)
    add_bench(mid, "llm-stats-Reasoning", "ts_reasoning", rs, 0, DATE, LS)
    add_bench(mid, "llm-stats-Coding", "ts_coding", cd, 0, DATE, LS)

# ---------------- 4. SWE-bench Verified ----------------
add_bench("glm-5.3-flash", "SWE-bench-Verified", "swe_verified_pct",
          92.0, 0, SWE_BOARD_DATE, VALS)
add_bench("qwen3.8-27b", "SWE-bench-Verified", "swe_verified_pct",
          86.0, 0, SWE_BOARD_DATE, VALS)

# ---------------- 5. sources ----------------
add_source("web", "https://new.qq.com/rain/a/20260901A069H400",
           "腾讯混元 Hy4 preview 轻量版发布：1.5TB 压至 214GB，异构设备可联合推理",
           "凤凰网科技", "Sherry 稀疏三值量化(平均1.25bit)+MIX-STQ1_0；4090本+4xA4000 1.02 tok/s；GGUF 开源",
           "tencent,hy4-lite,quantization")
add_source("web", "https://cloud.tencent.com/developer/news/4533104",
           "Hy4 preview 调用激增，WorkBuddy 内 Hy3 限免延期至 9/30 23:59",
           "腾讯云开发者社区", "Hy4 首发接入 WorkBuddy 首日排队，紧急扩容；Hy3 限免从 8/31 延至 9/30",
           "tencent,hy3,hy4,workbuddy")
add_source("web", "https://news.qq.com/rain/a/20260901A06FKM00",
           "阿里云 Qwen3-VL-Rerank 降价：文本 0.7->0.5，多模态 1.8->0.5（-72.2%）",
           "腾讯新闻", "8/31 10:13 起北京地域；此前 8/27 已下调 Qwen3.8-Flash 计费；海外 OpenAI/Google/Anthropic 同期降价潮",
           "alibaba,pricing,rerank")
add_source("web", "https://theplanettools.ai/blog/alibaba-qwen3-8-2-4t-a95b-open-weights-ties-max-license-2026",
           "Qwen3.8-27B（Apache-2.0）发布：同一家族两种许可按尺寸分界",
           "ThePlanetTools", "27B 8/5 开源 Apache-2.0 原生视觉，FP8 8/13；下载 209 万 vs Max 权重 1.7 万；QwenCloud 托管 $0.5/$3",
           "alibaba,qwen3.8-27b,license")
add_source("web", "https://new.qq.com/rain/a/20260901A05F4D00",
           "智谱 2026 中报：营收 9.54 亿 +399.7%，API 收入 8.25 亿 +2735.7%，10 万卡国产芯片推理",
           "腾讯新闻", "8/31 发布港股上市后首份中报；MaaS/API 占 86.5%，ARR 16 亿美元；单位 token 推理成本较年初降 80%",
           "zhipu,earnings")
add_source("web", "https://www.163.com/dy/article/L5N0LBMJ0519D45U.html",
           "唐杰定义 GLM-6.0：Full Self-Training（全自训练）技术路线",
           "观点网", "8/31 晚业绩会：GLM-6.0 覆盖预训练-中期-后训练全流程自净化；核心难题是自我判断与纠错",
           "zhipu,glm-6.0")
add_source("web", "https://aiwiremedia.com/news/platforms-infrastructure/deepseek-v4-pro-agent-upgrades-and-higher-api-prices",
           "DeepSeek V4 Pro 0813 正式版：Agent 升级 + API 涨价 + 峰谷计费",
           "AI Wire Media", "8/13 正式发布；8/16 起 V4 Pro/Flash 新价，谷时半价；V4 Pro 未命中输入 $0.66-1.32 / 输出 $1.98-3.96",
           "deepseek,v4-pro,pricing")
add_source("web", "https://www.toutiao.com/article/7680187300852220459/",
           "月之暗面 7 次核心发布迭代；8/31 老 Kimi 模型集体下线",
           "今日头条", "moonshot-v1 全系列与 K2.5 8/31 停服；现行 K2.6/K2.7 Code/K3",
           "moonshot,kimi,lifecycle")

# ---------------- 6. community signals ----------------
signals = [
    ("hy4-preview", "BV1BZtu6aEe8", "AI大航海时代", "mixed", 14,
     "3D 游戏实测直逼 Fable5（浏览器OS/GTA/C++滑板/地铁FPS/Blender城市/复古摔跤全过），但 429 限流、卡住、修好又出问题贯穿全场：潜力很大，远没到放心用"),
    ("hy4-preview", "BV1Ggtg6pEyT", "进化中的阿陈", "positive", 23,
     "地狱测试：一句话秒写浏览器操作系统/纯C++ 3D动作游戏/地铁射击游戏，代码与全栈架构战力直逼世界第一梯队"),
    ("glm-5.3-flash", "BV1MHtW6WEfa", "Biolover", "positive", 3,
     "开源 AI 凭一张截图 80 秒修复代码 Bug——GLM-5.3 视觉能力实测（Claude Code 同任务对照）"),
    ("glm-5.3-flash", "BV1QH4k6GEf5", "量子菠萝_", "positive", 103,
     "纯国产芯片推理：320B/18B MoE，百万 token 输入仅 $0.075，成本约为 Claude 的 1/75；提醒 9/9 优惠截止"),
    ("glm-5.3-flash", "BV142tw6oE8R", "MRTao涛涛", "negative", 884,
     "一句话做游戏实测翻车：GLM-5.3-Flash 用 44 分钟，DeepSeek-V4-Flash 只用 6 分钟（同一提示词）"),
    ("deepseek-v4-flash", "BV1xjtb6AEmr", "Yin_Code", "positive", 6086,
     "DeepSeek V4 多模态实测：本地 DSH 部署 + 5 个必装插件全流程"),
    ("qwen3.8-27b", "BV1sbtg6GEVp", "蓝发少年Linky", "mixed", 79,
     "RTX 5090 Laptop 24GB 跑 Q4：约 40 tok/s 文字流畅；Agent 基础工具/飞书 Webhook 一次成功，联网搜索偏慢，代码能力有波动"),
    ("qwen3.8-27b", "BV1Ugtb6KEsf", "karminski-牙医", "neutral", 18569,
     "51 小模型全面评测：Qwen3.8-27B 等 8 款 × 4 量化版本 × 3 思考强度，前端/python/Agent 三维横评"),
    ("qwen3.8-27b", "BV1kstb6dEaF", "一猫之下科技", "positive", 7476,
     "Qwen3.8-27B NVFP4 在 V100 上输出 280 tok/s（1Cat-vLLM SM70 fork）"),
]
for s in signals:
    add_signal(*s)

# ---------------- meta note ----------------
meta = d.setdefault("_meta", {})
meta["captured_at"] = DATE
note = ("2026-09-01 增量：新增 qwen3.8-27b（8/5 发布 Apache-2.0 dense 27.8B 原生视觉，"
        "llm-stats 45.5 + vals SWE 86.0，此前遗漏）；新增 hy4-lite（腾讯 9/1 Hy4 preview 轻量版，"
        "1.5TB→214GB Sherry 三值量化+混合精度，无 API 价）；补 llm-stats 覆盖缺口 doubao-2.1-pro/"
        "deepseek-v4-flash/deepseek-v4-flash-vision-exp（此前无 llm-stats 锚）；"
        "glm-5.3-flash 补 vals.ai SWE-bench Verified 92.0（同口径超 Qwen3.8 Max 85.6 / V4 Flash 88.8）；"
        "Qwen3.8-Flash 生产版 llm-stats 49.6 与 Flash-Next 同架构不重复建模型；"
        "阿里 Qwen3-VL-Rerank 降价、智谱中报+GLM-6.0 路线、DeepSeek V4 Pro 0813 涨价、Kimi 老模型 8/31 下线入源。")
meta["note"] = meta.get("note", "") + "\n" + note

json.dump(d, open(B, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("baseline.json updated for", DATE)
