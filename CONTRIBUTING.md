# 参与贡献 / Contributing

## 数据订正优先

这个仓库最欢迎的 PR 是**数据订正**，尤其是：

- 厂商官方调价（请附官方价目页 URL 与日期，聚合站/代理商的报价不算）；
- 新模型发布（附官方公告 + 第三方独立评测链接）；
- 榜单口径变化（例如同一榜单新增 effort 档位、出现 AutoEval 标记）。

请在 PR 描述里写清 **改了什么、依据是什么、核实时间**。只有厂商自报分、没有第三方复现的，一律只进 `models.notes`，不并入评分。

## 不要直接手改产物

`master_table_*.md` 与 `scatter_*.html` 都是生成物。改数据请改 `baseline.json`（或写幂等的 `update_YYYYMMDD.py`），然后跑：

```bash
python llm_intel.py seed && python llm_intel.py recompute
python llm_intel.py audit          # 必须 0 ERROR
python generate_master.py && python gen_scatter.py
```

只改生成物而不改生成器的 PR 会被要求重做。

## 口径纪律

- **统一排名，不分地域**：标题、副标题、结论、定位列都不按国产/境外分区；可用切面是「开源权重 vs 闭源」。
- **订阅折算价与 API 挂牌价不可横比**，两个价格列各自独立。
- **社区信号不入分**：B 站/论坛的评价只用于补漏与置信区间，不能当跑分。

## 提交流程

1. fork → 分支 → 提交，一个 PR 只做一件事；
2. PR 描述写清依据（URL + 日期）；
3. 涉及分数变动的，附 `audit` 输出与新旧分对照。
