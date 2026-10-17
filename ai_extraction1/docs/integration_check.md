# integration_check.md — v2.2 筛选与输入装配搬迁（对齐 LoRA 训练口径）

范围：把 v2.2 表格筛选 + 输入装配从训练脚本搬到 AI 服务，使线上 lora 推理输入与训练 `user` 逐字节一致。
不做的事：不改 AI 模型、不改 `EXTRACT_BACKEND` 默认值（仍 `qwen`）、不部署、不改训练脚本、不改前端与业务后端。

---

## 1. 改动文件清单

**新增（AI 服务侧，独立副本，与训练脚本解耦）**

| 文件 | 说明 |
|---|---|
| [table_filter_lora.py](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/table_filter_lora.py) | 复制 `score_table_v22` / `selected_v22` / `_is_continuation` / `_has_negative` / `render_table` 及相关常量，**逻辑不改** |
| [build_lora_input.py](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/build_lora_input.py) | 复制 `preprocess_text`（原样）与 `build_input`（原样，格式 B，仅对照）；新增 `build_lora_input(payload)`（格式 A） |
| [tests/test_build_lora_input.py](file:///e:/ICT竞赛项目/ai_extraction/tests/test_build_lora_input.py) | 单测：与训练 `user` 逐字节比对 |
| [docs/input_format_precheck.md](file:///e:/ICT竞赛项目/ai_extraction/docs/input_format_precheck.md) | 任务 0 前置确认 |
| [docs/payload_schema.md](file:///e:/ICT竞赛项目/ai_extraction/docs/payload_schema.md) | 请求体结构说明 |
| [docs/integration_check.md](file:///e:/ICT竞赛项目/ai_extraction/docs/integration_check.md) | 本文件 |

**修改（改动最小化）**

| 文件 | 改动 |
|---|---|
| [config.py](file:///e:/ICT竞赛项目/ai_extraction/config.py#L18-L22) | 新增 `EXTRACT_BACKEND = os.environ.get("EXTRACT_BACKEND", "qwen")`，**默认仍 qwen** |
| [api.py](file:///e:/ICT竞赛项目/ai_extraction/api.py) | 新增 `_resolve_user_input(request)` 分支；`ExtractRequest` 增加可选字段 `backend/html_text/html_tables/attachments` |

**未改动（受保护）**：`run_v4_1_prompt_v22.py`、`make_messages_550.py`、`v2_2_table_filter.py`、`v2_table_filter.py`、`cleaner.py`。
**未改动**：前端、业务后端。

---

## 2. `EXTRACT_BACKEND=qwen` 时行为是否与改动前一致

**一致。依据：**

1. `ExtractRequest` 新增字段全部**可选**，`text` 仍必填 → 原有请求不受影响。
2. 分支代码 `backend = request.backend or config.EXTRACT_BACKEND`；`qwen` 时 `_resolve_user_input` 返回 `request.text`，
   与改动前 `extractor.extract(request.text, ...)` 完全等价。
3. 冒烟验证（未起服务，直接调用分支函数）：

```
EXTRACT_BACKEND default = qwen
qwen path -> 'TEXT-X'
lora path -> '===== 公告正文 =====\nhi\n\n===== 第一层附件：f.pdf =====\n===== 报价明细表：表格1 =====\n产品名称 | 单价 | 数量\nA | 1 | 2'
```

---

## 3. `EXTRACT_BACKEND=lora` 待验证项（本次未切换、未部署）

| 项 | 状态 | 说明 |
|---|---|---|
| 输入装配逐字节一致 | ✅ 已验证 | 见第 4 节单测 |
| 模型指向 LoRA 部署 | ⏳ 待办 | 需将 `QWEN_MODEL` 指向已部署 LoRA（`qwen3-8b-33dd2dfc123f`）；本次未改 |
| 附件顺序 | ⏳ 待后端确认 | 后端须按**文件名排序**传入 `attachments`（对齐训练 `sorted(rglob)`） |
| `html_text` 为原始 HTML | ⏳ 待后端确认 | 不能传预清洗文本 |
| ~~`/extract-pair` 语义~~ | 已移除 | 该端点原本不存在，后端只用 `/extract`，已删除该试探性端点 |
| 端到端联调 | ⏳ 待办 | 上传 HTML+ZIP → lora 装配 → 抽取 → 评估，与后端联调后再切 `EXTRACT_BACKEND=lora` |

---

## 4. 单测结果

命令：

```powershell
E:\ICT竞赛项目\.venv\Scripts\python.exe e:\ICT竞赛项目\ai_extraction\tests\test_build_lora_input.py
```

输出：

```
== 类别覆盖 ==
  单附件: t20260402_26351983 -> OK
  多附件: t20260610_26727619 -> OK
  多表(单附件6表): t20260603_26679396 -> OK
  无附件: t20260212_26174981 -> OK
== 逐字节比对 ==
  t20260402_26351983 附件数=1
  t20260610_26727619 附件数=3
  t20260603_26679396 入选表数=6
  t20260212_26174981 附件数=0
== 结果 == 通过 60 / 共 60，失败 0
ALL PASS：build_lora_input 输出与训练 user 逐字节一致
```

- 覆盖 `test_550_att_messages.jsonl` **全部 60 条**（含 1 条单附件、多附件、单附件 6 表、无附件等场景），逐字节相等。
- 复原方式：`html_text` ← `data/validation/{aid}.html`；`attachments` ← `data/attachments_550/parsed_550/{aid}/*.json`（按文件名排序，仅 `parse_status=="success"`）。
- 失败时会打印首个差异位置与该位置前后 30 字符。

---

## 5. 格式 A vs 格式 B 差异（已按要求先报差异，未“修正”训练格式）

| 维度 | 格式 A（训练口径，`make_messages_550.build_user`） | 格式 B（`run_v4_1_prompt_v22.build_input`） |
|---|---|---|
| 文件名 | 每张入选表**都重复** `===== 第一层附件：{fn} =====` | 同附件只出现一次（`last_fn` 去重） |
| parts 连接符 | `"\n\n"` | `"\n"` |
| 逐字节相同场景 | — | 仅“单附件 + 单表” |

`test_550_att_messages.jsonl` 使用**格式 A**。因此 `build_lora_input` 对齐格式 A；
`build_input`（格式 B）在 `build_lora_input.py` 中**原样保留仅作对照**，线上装配路径不使用。

---

## 6. 回退方式

1. **不动任何代码即可回退**：`EXTRACT_BACKEND` 默认 `qwen`；不设置该环境变量（或显式设为 `qwen`）即回到改动前行为。
2. 单次请求回退：请求体带 `"backend": "qwen"`。
3. 删除新增文件（`preprocessing/table_filter_lora.py`、`preprocessing/build_lora_input.py`、`tests/`、`docs/`），
   并还原 `config.py` / `api.py` 两处最小改动，即可完全回到改动前状态。
4. git 回退：本次改动集中在上述文件，可用 `git checkout -- <file>` 精确还原。
