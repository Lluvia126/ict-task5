"""build_lora_input.py — LoRA 输入装配（对齐训练时逐字节口径）。

来源（只读复制，未修改原件）：
  - ai_extraction/preprocessing/cleaner.py::preprocess_text         （原样复制）
  - ai_extraction/run_v4_1_prompt_v22.py::build_input               （原样复制，格式 B，仅作对照）
  - ai_extraction/experiments/qwen_sft/make_messages_550.py::build_user
        （格式 A = 训练 user 的真实生成口径；build_lora_input 对齐此格式）

重要（格式 A vs 格式 B 差异，已按约束先报差异、未“修正”训练格式）：
  - 格式 A（训练口径，make_messages_550.build_user）：每张入选表都重复输出
    “===== 第一层附件：{fn} =====”，parts 用 "\\n\\n" 连接。
  - 格式 B（run_v4_1_prompt_v22.build_input）：同一附件文件名去重（last_fn），
    att_parts 用 "\\n" 连接。二者仅在“单附件 + 单表”时逐字节相同。
  - 因此本模块的 build_lora_input 采用格式 A；下方 build_input 仅原样保留作为对照，
    线上装配路径不使用它。
"""
import re
from pathlib import Path

from .table_filter_lora import (
    TABLE_SCORE_THRESHOLD, render_table, score_table_v22, selected_v22,
)

BASE_DIR = Path(__file__).resolve().parents[1]   # ai_extraction/
VAL_DIR = BASE_DIR / "data" / "validation"


def preprocess_text(text):
    """文本预处理：去空白、统一换行、清理 HTML 标签，保留关键信息。

    （原样复制自 ai_extraction/preprocessing/cleaner.py）
    """
    if not text:
        return ""
    # 清理 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 常见 HTML 实体
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    # 统一换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # 连续空行最多留一个
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 压缩连续空格（保留单个空格，不破坏规格型号中的空格）
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def build_input(aid, selected_by_aid):
    """格式 B（原样复制自 ai_extraction/run_v4_1_prompt_v22.py，仅作对照，线上不使用）。

    注意：与训练口径（格式 A）在多表/多附件时逐字节不同——见模块 docstring。
    """
    html = (VAL_DIR / f"{aid}.html").read_text(encoding="utf-8", errors="ignore")
    parts = ["===== 公告正文 =====\n" + preprocess_text(html)]
    items = selected_by_aid.get(aid, [])
    if items:
        last_fn = None
        att_parts = []
        for fn, ti, tb in items:
            if fn != last_fn:
                att_parts.append(f"===== 第一层附件：{fn} =====")
                last_fn = fn
            att_parts.append(f"===== 报价明细表：表格{ti + 1} =====\n" + render_table(tb))
        parts.append("\n".join(att_parts))
    return "\n\n".join(parts), ("html+v22tables" if items else "html_only")


def _normalize_table(raw_tb, idx):
    """附件表格结构归一：兼容「新接口二维 list」与「旧/训练 dict」两种口径。

    - dict（旧 / 训练口径）：{"table_index": int, "data": [[...], ...]}
      → **原样使用**（table_index 缺失时沿用原有默认值 0，保持向后兼容）
    - list（新业务接口契约）：二维列表 [[...], ...]
      → 包成 {"table_index": idx, "data": raw_tb}，table_index 由 enumerate 生成

    返回 (tb, ti)：tb 为下游 score_table_v22 / selected_v22 / render_table 统一使用的 dict。
    """
    if isinstance(raw_tb, dict):
        return raw_tb, raw_tb.get("table_index", 0)
    if isinstance(raw_tb, list):
        return {"table_index": idx, "data": raw_tb}, idx
    raise TypeError(
        f"attachments[].tables[{idx}] 结构不支持：期望二维 list 或 dict，"
        f"实际为 {type(raw_tb).__name__}"
    )


def build_lora_input(payload):
    """LoRA 输入装配：与训练 user 逐字节一致（格式 A）。

    payload（dict）:
      {
        "announcement_id": "...",
        "text": "后端拼好的（含附件段标记）",   # 仅 qwen 路径使用，本函数忽略
        "html_text": "清洗前的正文",           # 本函数对其跑 preprocess_text 作为正文段
        "html_tables": [[[...]]],               # 默认忽略
        "attachments": [
          {"filename": "分项报价表.pdf",
           "tables": [[["产品名称", "数量", "单价"],
                       ["测试设备", "2", "1000"]], ...]}   # 新接口契约：二维 list
        ]
      }

    附件表格两种结构均支持（由 _normalize_table 归一，见其 docstring）：
      - 新接口契约：每张表是二维 list，table_index 由 enumerate 生成
      - 旧 / 训练口径：每张表是 {"table_index": int, "data": [[...], ...]}

    处理：
      - 正文段：preprocess_text(html_text)
      - 每个 attachments[i] 内的表按顺序跑 score_table_v22 + selected_v22 状态机
        （续表状态在每个附件内独立重置，跨附件不延续）
      - 入选表用 render_table 渲染，拼成
        “===== 第一层附件：{filename} =====\\n===== 报价明细表：表格{ti+1} =====\\n{render}”
      - 每张入选表一个 part（文件名不去重），parts 用 "\\n\\n" 连接
      - 表格顺序按 attachments[].tables 的原始顺序（enumerate），附件顺序按 attachments 顺序
      - html_tables 忽略
    """
    parts = ["===== 公告正文 =====\n" + preprocess_text(payload.get("html_text") or "")]
    for att in payload.get("attachments") or []:
        fn = att.get("filename") or ""
        prev_selected = False
        prev_depth = 0
        prev_tb = None
        for idx, raw_tb in enumerate(att.get("tables") or []):
            tb, ti = _normalize_table(raw_tb, idx)
            score, _matched, _neg, _detail = score_table_v22(tb)
            sel = selected_v22(tb, score, prev_selected=prev_selected,
                               prev_depth=prev_depth, prev_tb=prev_tb)
            if sel:
                parts.append(f"===== 第一层附件：{fn} =====\n"
                             f"===== 报价明细表：表格{ti + 1} =====\n"
                             + render_table(tb))
            prev_selected = sel
            prev_tb = tb
            if sel and score >= TABLE_SCORE_THRESHOLD:
                prev_depth = 0
            elif sel:
                prev_depth += 1
    return "\n\n".join(parts)
