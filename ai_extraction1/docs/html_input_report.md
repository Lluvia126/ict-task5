# html_input_report.md — `preprocess_text` 输入来源查证

查证问题：`preprocess_text` 的输入是**原始 HTML**，还是**已提取的纯文本**？

> 结论：**原始 HTML**。训练/装配管线是把 `data/validation/{aid}.html` **整文件按文本读出（未做正文提取、未纯文本化）**，
> 再交给 `preprocess_text`；由 `preprocess_text` 内部负责剥离 HTML 标签、替换实体、归一空白。

---

## 1. 关键链路：`html_cache` 来自 `.html` 原文

[make_messages_550_att.py L144-147](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L144-L147)：

```python
html_cache = {}
for r in recs:
    p = VAL_DIR / f"{r['announcement_id']}.html"
    html_cache[r["announcement_id"]] = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
```

- `VAL_DIR = EXTRACT / "data" / "validation"`（[L34](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L34)）；
- 读取的是 **`.html` 文件全文**，**没有任何 HTML 解析 / 正文抽取 / 纯文本化**（不经过 BeautifulSoup 等）；
- 该 `html_cache[aid]` 直接进入 `build_user` 的 `preprocess_text(html)`（[L72-74](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L72-L74)）。

`make_messages_550.py` 完全同构（[L148-151](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550.py#L148-L151)、`VAL_DIR` [L40](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550.py#L40)）。

---

## 2. 佐证：源文件确实是完整 HTML（含标签）

`data/validation/t20260603_26679396.html` 首行即 `<!doctype html>`，随后是 `<html>/<head>/<meta>` 等标签：

```
<!doctype html>
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<meta name="SiteName" content="中国政府采购网" />
...
```

→ 输入是**带标签的原始 HTML**，而非提取后的纯文本。

---

## 3. `preprocess_text` 做什么（输入是 HTML 的前提）

[cleaner.py L4-21](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/cleaner.py#L4-L21)：

```python
def preprocess_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)                 # ← 剥 HTML 标签
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))   # ← HTML 实体
    text = text.replace("\r\n", "\n").replace("\r", "\n")     # 统一换行
    text = "\n".join(line.rstrip() for line in text.split("\n"))  # 去行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)                    # 连续空行压缩为 2
    text = re.sub(r"[ \t]{2,}", " ", text)                    # 连续空格压缩为 1
    return text.strip()
```

函数内第一步就是**正则剥离标签**，其设计前提即“输入为 HTML”。若传入已提取纯文本，标签剥离为空操作（通常无害），
但**实体替换/空白归一仍会再次生效**，与“以原始 HTML 为输入”的链路不完全等价。

---

## 4. 同一口径的其他调用点

| 位置 | 传入 preprocess_text 的实参 | 是否原始 HTML |
|---|---|---|
| [make_messages_550_att.py L74](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550_att.py#L74) | `html_cache[aid]`（`.html` 全文） | ✅ 原始 HTML |
| [make_messages_550.py L76](file:///e:/ICT竞赛项目/ai_extraction/experiments/qwen_sft/make_messages_550.py#L76) | `html_cache[aid]`（`.html` 全文） | ✅ 原始 HTML |
| [run_v4_1_prompt_v22.py L226-228](file:///e:/ICT竞赛项目/ai_extraction/run_v4_1_prompt_v22.py#L225-L240) | `(VAL_DIR / f"{aid}.html").read_text(...)` | ✅ 原始 HTML |
| [build_lora_input.py L95](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/build_lora_input.py#L87-L95) | `payload["html_text"]` | ✅ 约定为原始 HTML |
| [pipeline.py L240](file:///e:/ICT竞赛项目/ai_extraction/pipeline.py#L240) | `html`（原始） | ✅ 原始 HTML |
| [pipeline.py L255](file:///e:/ICT竞赛项目/ai_extraction/pipeline.py#L255) | `str(soup)`（BeautifulSoup 序列化） | ⚠️ 仍是 HTML 串（非纯文本） |

> 训练 user 口径（`make_messages_550_att.py` / `make_messages_550.py`）统一使用 **`.html` 原文**。

---

## 5. 对线上 `build_lora_input` 的含义

- `payload["html_text"]` 必须是**原始 HTML**（`data/validation/{aid}.html` 的等价内容），**不能传已提取的纯文本**；
  否则与训练 `preprocess_text` 的输入不一致，破坏逐字节对齐。
- `build_lora_input` 内部对 `html_text` 直接调用 `preprocess_text`，与训练链路一致
  （[build_lora_input.py L95](file:///e:/ICT竞赛项目/ai_extraction/preprocessing/build_lora_input.py#L95)）。
