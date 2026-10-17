# sort_key_report.md — 附件排序键与排序方式查证

查证对象：`make_messages_550.py::build_user` 的附件顺序来自哪里、排序键是 **filename 还是完整路径**。
（`test_550_att_messages.jsonl` 实际由 `make_messages_550_att.py` 生成，其 `build_user` 与前者逐字一致，一并查证。）

> 结论先行：**`build_user` 自身不做任何排序**。附件顺序来自 `load_selected_tables()` 读取的 **`selection_audit_550.csv` 的行序**；
> 而该 CSV 由 `build_attachments_550.py` 用 **`sorted(PARSED_DIR.rglob("*.json"))`** 生成 —— 排序键是 **完整路径（`Path` 对象，规范化后的整串路径）**，**升序**。
> 由于 `parsed_550/{aid}/` 是扁平目录，同一公告内等价于 **按文件名升序**。

---

## 1. `build_user` 本身不排序

[make_messages_550.py](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550.py#L74-L80)（L74-80）：

```python
def build_user(aid, selected, html_cache):
    html = html_cache[aid]
    parts = ["===== 公告正文 =====\n" + preprocess_text(html)]
    for fn, ti, tb in selected.get(aid, []):        # ← 直接按 list 顺序遍历，无 sort
        parts.append(f"===== 第一层附件：{fn} =====\n===== 报价明细表：表格{ti + 1} =====\n"
                     + render_table(tb))
    return "\n\n".join(parts)
```

`selected` 是 `{aid: [(filename, table_index, tb), ...]}`，顺序完全由上游决定。

---

## 2. 顺序的真实来源：CSV 行序

[make_messages_550.py::load_selected_tables](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550.py#L55-L71)：

```python
def load_selected_tables():
    parsed = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):                 # 排序①（进 dict，仅用于按 key 查找）
        rec = json.loads(jf.read_text(encoding="utf-8"))
        parsed[(rec["announcement_id"], rec["attachment_filename"])] = rec
    selected = {}
    for r in csv.DictReader(AUDIT_CSV.open(encoding="utf-8-sig")):  # 排序②（决定 list 顺序）
        if r["selected_v22"] != "True":
            continue
        key = (r["announcement_id"], r["attachment_name"])
        ...
        selected.setdefault(r["announcement_id"], []).append((key[1], ti, tables[ti]))
    return selected
```

- `parsed` 是**按 `(aid, filename)` 做 key 的字典**，插入顺序不影响后续查找 → 排序① 对输出顺序**无影响**。
- `selected[aid]` 的追加顺序 = **`AUDIT_CSV` 的行遍历顺序**（过滤 `selected_v22 == "True"`）→ **排序② 才是决定 `build_user` 输出顺序的键**。

`make_messages_550_att.py` 同构造，仅把 `AUDIT_CSV` 换成 `attachments_550/selection_audit_550.csv`（[L37](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L37)、[L49-L69](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L49-L69)）。

---

## 3. CSV 行序的来源：`sorted(rglob)`（完整路径，升序）

`selection_audit_550.csv` 由 [build_attachments_550.py](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/build_attachments_550.py#L245-L274) Phase 3 生成（L248）：

```python
for jf in sorted(PARSED_DIR.rglob("*.json")):      # ← 唯一排序点
    rec = json.loads(jf.read_text(encoding="utf-8"))
    ...
    for tb in rec.get("tables") or []:             # ← 附件内按 tables 顺序（table_index 升序）
        ...
        audit_rows.append({...})                   # 行序 = 附件序 × 表序
```

### 排序键究竟是 filename 还是完整路径？

- `sorted()` 作用在 **`pathlib.Path` 对象** 上。CPython 的 `PurePath` 比较使用 `self._str_normcase`，
  即 **`os.path.normcase(str(path))`**（Windows 下：整串路径转小写 + `/`→`\`）。
  已实测确认：`sorted(paths) == sorted(paths, key=lambda p: os.path.normcase(str(p)))` → **True**。
- 因此严格意义的排序键 = **规范化后的完整路径字符串**（不是裸文件名），**升序**。
- 但 `parsed_550/{aid}/` 是**扁平目录**（实测所有 json 相对路径深度恒为 `{2}`，即 `{aid}/{file}.json`），
  同一公告下的路径共享前缀 → **同一公告内等价于按“`{attachment_filename}.json`”文件名升序**。

---

## 4. 表内顺序与状态机重置粒度

- 附件内表格顺序：`for tb in rec["tables"]` → `table_index` 升序（`render_table` 输出 `表格{ti+1}`）。
- 跨页续表状态机 `prev_sel/prev_depth/prev_tb` 在**每个附件内初始化并重置**（[build_attachments_550.py L253](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/build_attachments_550.py#L253)），**跨附件不延续**。

---

## 5. 实测数据（全量 `parsed_550`）

| 指标 | 结果 |
|---|---|
| `parsed_550/{aid}/` 是否扁平 | 是（相对路径深度恒为 2） |
| 多附件公告数 | 394 |
| 「完整路径排序」≠「文件名排序」的公告数 | **0** |
| 存在「同附件 ≥2 张入选表」的 (aid, 附件) 组合数 | **912** |
| CSV 附件顺序与 glob（按文件名）顺序不一致的公告数 | **0** |

> 注：912 个组合存在同附件多张入选表，意味着训练口径（格式 A：**每张表重复文件名头**）与
> `run_v4_1_prompt_v22.build_input`（格式 B：文件名去重）在该数据集上**普遍**产生不同字节，不止个例。

---

## 6. 对线上装配（`build_lora_input`）的含义

训练口径的附件顺序 = **按 `attachment_filename` 升序**（= 完整路径升序的等价形式）。
因此后端传 `payload["attachments"]` 时**必须按文件名升序排列**，`tables` 保持 `table_index` 升序，
才能保证与训练 `user` 逐字节一致。当前 `build_lora_input` 严格按 payload 给定顺序处理、不做排序
（见 [build_lora_input.py L94-L115](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/build_lora_input.py#L94-L115)），顺序责任在调用方。
