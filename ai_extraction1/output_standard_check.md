# 标准输出表对照核查（output_standard_check）

- 核查对象：`ai_extraction/config.py` 的 `empty_result()` 骨架 + Prompt 输出结构
- 核查时间：2026-10-14
- 性质：**只读核查，未修改任何代码**
- 结论一句话：**老师要求的 7 列产品字段当前 100% 覆盖，无缺失**；用户提到的 3 个缺口（标段/合同包名称、HTML 源地址、万元单位）不属于这 7 列，而是**口径/载体层面的差异**，其中"标段"已实测污染了"品目"字段。

---

## 0. 证据来源（仓库内可验证）

| 证据 | 位置 | 内容 |
|---|---|---|
| 七字段官方定义 | `E:\项目数据与统计\赛道五基本项目流程和任务分工.txt` | 产品/服务名称、品目、品牌（产品或供应商）、规格型号、数量、单价、总价 |
| 标准表模板（AI/Gold 双列） | [goldset_task1_template.csv](file:///e:/ICT竞赛项目/ai_extraction/data/gold/goldset_task1_template.csv) | 表头 = `announcement_id, source_file` + 7 字段 × `_AI`/`_Gold` |
| 官方口径评估器字段表 | [evaluate_official_v3.py](file:///e:/ICT竞赛项目/ai_extraction/experiments/official_caliber_eval/evaluate_official_v3.py#L28-L36) | `FIELDS` 恰为 7 项，金额字段标记 `is_money=True` |
| Gold 结构 | `E:\项目数据与统计\validation_v0.json` / `validation_v550.json` | `products[].keys` 恰为 7 项 |
| 当前骨架 | [config.py](file:///e:/ICT竞赛项目/ai_extraction/config.py#L116-L129) | `empty_result()` 11 个键 |

> 说明：仓库内未找到老师提供的"标准输出表"原件，故以"赛道五七字段定义 + goldset 模板 + 官方口径评估器"三者交叉作为对照基准；三者字段集合完全一致。

---

## 1. 逐项对照：老师 7 列 vs 当前字段

| # | 老师列（中文） | 当前字段（英文） | 所在结构 | 是否覆盖 |
|---|---|---|---|---|
| 1 | 产品/服务名称 | `product_service_name` | `products[]` | ✅ |
| 2 | 品目 | `category` | `products[]` | ✅ |
| 3 | 品牌 | `brand` | `products[]` | ✅ |
| 4 | 规格型号 | `spec_model` | `products[]` | ✅ |
| 5 | 单价 | `unit_price` | `products[]` | ✅ |
| 6 | 数量 | `quantity` | `products[]` | ✅ |
| 7 | 总价 | `total_price` | `products[]` | ✅ |

**7 列全部覆盖，无缺失、无多余。**

骨架中除这 7 列外还有：

| 类别 | 字段 | 说明 |
|---|---|---|
| 后端回填（Prompt 明确不抽） | `announcement_id`、`file_id`、`source_file` | [config.py L67](file:///e:/ICT竞赛项目/ai_extraction/config.py#L67) 规定不由模型输出 |
| 项目级标量 | `project_name`、`project_code`、`purchaser`、`project_amount` | 非老师 7 列范围内 |
| 项目级列表 | `winning_suppliers`、`bid_participants` | 同上（关系分析用） |

对照标准表模板，`announcement_id` 与 `source_file` 恰是老师的**前两列**（行标识），当前骨架也有 → 对得上。

---

## 2. `project_amount` 单位：**元（非万元）**

证据链：

1. Prompt 明文规定 —— [config.py L70](file:///e:/ICT竞赛项目/ai_extraction/config.py#L70)：
   > "数字字段(project_amount、unit_price、quantity、total_price)只返回数字，不带"元""万元""台"等单位；**金额以元为单位**。"
2. Gold 实测 —— `validation_v0.json` 中 `t20260609_26711977` 的 `project_amount = 1128000.0`，该项目为"数字档案管理系统升级改造项目"，即 **112.8 万元 → 存 1128000 元**。
3. 产品级同口径 —— [goldset_task1_template.csv](file:///e:/ICT竞赛项目/ai_extraction/data/gold/goldset_task1_template.csv) 单价/总价直接写 `2664400`、`349000`，无小数缩放。

**结论：当前全链路（Prompt + Gold + 骨架）金额单位为「元」。**

### 老师"万元"要求的偏差量（可量化）

官方口径评估器为此专门提供了 `+万元单位容错`（万/亿倍换算），实测差异：

| 配置 | Macro-F1 | 总价 TP | 单价 TP |
|---|---|---|---|
| 旧口径(base) | 0.5287 | 108 | 91 |
| **+万元单位容错** | **0.5330** | **115** | 91 |
| 差值 | **+0.0043** | **+7** | 0 |

即：50 条测试集中有 **7 条产品的总价存在"万元 vs 元"错配**（`field_judgments.csv` 中记为 `TP:unit_scale`），换算后即可判对。单价无此问题。

> 注意：`+品牌等价` 与 `+金额整数下限` 在本数据集上**零增益**（Macro-F1 不变），仅"万元单位容错"有效。

---

## 3. 三个缺口的取证结论

### ① 标段/合同包名称 —— 当前无独立字段，且实测污染"品目"

**能从哪来（有据可查）：**

| 来源 | 实测比例 | 形态示例 |
|---|---|---|
| 公告正文 | `合同包` 出现于 **672 / 1038** 个 HTML；`标段` 出现于 **54 / 1038** | `合同包1(为市局民辅警及职工采购人身意外险及重大疾病等保险服务项目):` |
| 附件文件名 | 常见于入选表所属附件名 | `包1(其他终端设备)_安徽格诚…_合同包1：关于符合本国产品标准的声明函（…）.pdf` |

**风险实证（这是当前最需要修的一处）：**

[field_judgments.csv](file:///e:/ICT竞赛项目/ai_extraction/experiments/official_caliber_eval/field_judgments.csv) 中 `t20260206_26158480`：

| 字段 | Gold | Pred | 判定 |
|---|---|---|---|
| 品目 | `货物类` | `1标段营养馕` | FP+FN |
| 品目 | `货物类` | `3标段鲜鸡蛋` | FP+FN |
| 规格型号 | `3标段，投标下浮率10%` | （空） | FN |

→ 模型把"**标段**"塞进了 `category`（品目），而 Gold 的品目是"货物类"。**标段信息当前无处安放，直接造成品目字段 FP+FN。**

副产品证据：[t20260206_26158480.json](file:///e:/ICT竞赛项目/ai_extraction/data/validation_run_final/t20260206_26158480.json) 里 `category` 被写成 `1标段营养馕` / `2标段营养馕` / `3标段鲜鸡蛋` / `4标段鲜鸡蛋`。

### ② HTML 源地址 —— 当前只有一个"文件名"，没有 URL

| 项 | 现状 |
|---|---|
| 骨架字段 | `source_file` = `"{announcement_id}.html"`（**仅文件名**）[config.py L116-L129](file:///e:/ICT竞赛项目/ai_extraction/config.py#L116-L129) |
| `file_id` | 恒为 `""` |
| Gold 各版本 | `validation_v0 / v550 / plus(393)` 顶层键**均无 URL 字段**，同样只有 `source_file` |
| HTML 内部 | 未内嵌自指公告页 URL；只含**附件下载 URL**（域名分布：`dzjy.sdcz.gov.cn:30181` 1010、`gdgpo.czt.gd.gov.cn` 667、`ggzy.qingdao.gov.cn` 249、`gpx.ccgp-sichuan.gov.cn` 132 …）与页脚站点链接 |

→ **仓库内无法还原源地址**，只能由上传时后端记录（或数据源导出时补充）。

### ③ 万元单位 —— 见第 2 节，偏差 7 条总价

---

## 4. 需要确认的三件事（请老师/后端确认后我再动代码）

| # | 问题 | 我的初步判断（待确认） | 影响面 |
|---|---|---|---|
| 1 | **标段/合同包名称从哪来？** 公告正文？附件？ | 两者都有：正文覆盖率更高（`合同包` 672/1038），结构为 `合同包N(名称)`；附件文件名亦可。建议**正文优先、附件名兜底**，并新增**产品级** `contract_package`（或项目级 `packages[]`）字段，同时从 `category` 的语义中排除标段 | 新增字段 + Prompt 改 + Gold 需回填；可修掉当前品目的 FP+FN |
| 2 | **HTML 源地址从哪来？** 上传时记录？ | 目前无处可取，必须**上传时由后端记录原始 URL**（骨架加 `source_url`，或复用 `source_file` 存完整 URL） | 后端 + 骨架契约变更；离线数据无法补齐 |
| 3 | **产品七字段的金额是否也要改万元？** | 老师口径存在"万元"要求（官方口径为此专门做单位容错，+7 条总价）。两种做法：**(A)** 存储仍用元，展示/导出层换算万元；**(B)** 全链路改万元（需回改 Gold + Prompt）。**建议 A**（改动小、不破坏已有 Gold 与评估口径） | 若选 B：Gold、Prompt、评估器、前端展示全部要改；若选 A：仅展示层 |

### 待确认后可能涉及改动的文件（**当前未改动**）

| 文件 | 若确认要改，改什么 |
|---|---|
| [config.py](file:///e:/ICT竞赛项目/ai_extraction/config.py#L116-L129) | `empty_result()` 增字段；`PRODUCT_FIELDS` 增列；Prompt 字段语义与"标段不算品目"约束 |
| [qwen_extractor.py](file:///e:/ICT竞赛项目/ai_extraction/extractors/qwen_extractor.py) | 新增/透传字段的清洗与回填规则 |
| [evaluate_official_v3.py](file:///e:/ICT竞赛项目/ai_extraction/experiments/official_caliber_eval/evaluate_official_v3.py#L28-L36) | `FIELDS` 增列 + 万元口径开关 |
| Gold（`validation_v550.json` 等） | 回填标段/合同包、源地址；金额单位若改 B 需整体换算 |
| 前端 `ExtractionDetail.vue` | 明细表新增列展示 |
