# payload_schema.md — /extract 请求体结构

对应实现：[api.py](file:///e:/ICT竞赛项目/ai_extraction/api.py)、[build_lora_input.py](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/build_lora_input.py)

---

## 1. 请求体（ExtractRequest）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `text` | str | 是 | — | 后端拼好的文本（含附件段标记）。**qwen 路径的唯一输入** |
| `announcement_id` | str | 是 | — | 公告 ID |
| `file_id` | str | 否 | `""` | 原样回填 |
| `source_file` | str | 否 | `""` | 原样回填 |
| `backend` | str \| null | 否 | `null` | `"qwen"` 或 `"lora"`；**优先于环境变量** `EXTRACT_BACKEND`；不传则用环境变量（默认 `qwen`） |
| `html_text` | str | 否 | `""` | 清洗前正文 HTML；**仅 lora 路径使用**，对其跑 `preprocess_text` |
| `html_tables` | list | 否 | `[]` | **默认忽略**（保留字段，不参与装配） |
| `attachments` | list | 否 | `[]` | 附件列表；**仅 lora 路径使用**，见第 2 节 |

> 兼容性：新增字段全部可选，`text` 仍为必填 → 原有调用方无需改动。

---

## 2. attachments 结构

```json
[
  {
    "filename": "分项报价表.pdf",
    "tables": [
      {"table_index": 0, "data": [["产品名称", "单价", "数量"], ["路由器", "1200.00", "2"]]},
      {"table_index": 1, "data": [["A", "300", "3"]]}
    ]
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `filename` | str | 附件文件名，**原样**输出到 `===== 第一层附件：{filename} =====` |
| `tables` | list | 该附件解析出的**全部**表格（每张表为二维数组 `data`） |
| `table_index` | int | 表在当前附件内的 0-based 索引；输出为 `表格{table_index + 1}` |
| `data` | list[list] | 二维单元格数组；渲染时 `None → ""`，单元格内换行 → 空格 |

**顺序约定（对齐训练管线）**：
- `attachments` 顺序 = 训练管线的 `sorted(rglob)`，对同一公告等价于**按文件名排序**；
- `tables` 顺序 = 附件解析顺序（`table_index` 递增）；
- 跨页续表状态机（`prev_selected/prev_depth/prev_tb`）**在每个附件内独立重置**，跨附件不延续。

---

## 3. 两条路径分工

| 环节 | qwen（默认） | lora |
|---|---|---|
| 输入 | `text` | `html_text` + `attachments` |
| 表格筛选 | 无（后端已拼好） | 现场 `score_table_v22` + `selected_v22` 状态机 |
| 表格渲染 | 无 | `render_table` + 格式 A 拼接 |
| 输出 | `text` 原样 | 与训练 `user` **逐字节一致**的字符串 |
| 使用的模型 | 环境变量 `QWEN_MODEL` | 同上（lora 路径需把 `QWEN_MODEL` 指向已部署 LoRA） |

`backend` 解析优先级：`request.backend` → 环境变量 `EXTRACT_BACKEND` → 默认 `"qwen"`。

---

## 4. 示例

**qwen 路径（默认，保持现状）**

```json
POST /extract
{
  "text": "===== 公告正文 =====\n......",
  "announcement_id": "t20260603_26679396"
}
```

**lora 路径**

```json
POST /extract
{
  "announcement_id": "t20260603_26679396",
  "text": "（可留空占位，lora 路径忽略）",
  "backend": "lora",
  "html_text": "<html>……原始 HTML 正文……</html>",
  "html_tables": [],
  "attachments": [
    {
      "filename": "（发售版）考试院招考业务系统运维项目(一)招标文件5.6.pdf",
      "tables": [{"table_index": 0, "data": [["...", "..."]]}]
    }
  ]
}
```

---

## 5. 注意事项

1. `html_text` 必须是**清洗前的原始 HTML**（函数内部会跑 `preprocess_text`）；传入已清洗文本会与训练口径不一致。
2. `text` 在 lora 路径被忽略；但字段仍必填（保持请求模型兼容）。
3. `html_tables` 当前不参与装配（默认忽略）。
4. 附件顺序与表格顺序必须与训练管线一致，否则输出会与训练 user 不同。
