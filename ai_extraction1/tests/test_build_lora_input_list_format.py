# -*- coding: utf-8 -*-
"""test_build_lora_input_list_format.py — 新接口二维 list 表格兼容性离线回归。

覆盖六项：
  1. 单附件单表（list）
  2. 单附件多表（list，含「第 1 张不入选」的编号用例）
  3. 多附件（list）
  4. 空 tables / 空 attachments
  5. 旧 dict 格式（向后兼容，须与 list 结果逐字节相同）
  6. HTML 正文 + 附件联合输入
另加：list 格式 payload 与训练 user 逐字节一致 + SHA-256 比对。

全程离线，不调用任何模型 API、不读写 Gold / 预测文件。
运行：
  E:\\ICT竞赛项目\\.venv\\Scripts\\python.exe ai_extraction/tests/test_build_lora_input_list_format.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_extraction.preprocessing.build_lora_input import build_lora_input  # noqa: E402

EXTRACT = ROOT / "ai_extraction"
VAL_DIR = EXTRACT / "data" / "validation"
PARSED_DIR = EXTRACT / "data" / "attachments_550" / "parsed_550"
TEST_JSONL = EXTRACT / "experiments" / "qwen_sft" / "test_550_att_messages.jsonl"

BODY = "===== 公告正文 =====\n公告正文"

PRICE_TABLE = [["产品名称", "数量", "单价"], ["测试设备", "2", "1000"]]
PRICE_TABLE_2 = [["产品名称", "数量", "单价"], ["第二设备", "3", "2000"]]
NEG_TABLE = [["目录", "页码"], ["第一章 总则", "1"]]


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _p(html_text, attachments, html_tables=None):
    return {"announcement_id": "T", "text": "", "html_text": html_text,
            "html_tables": html_tables if html_tables is not None else [],
            "attachments": attachments}


def _seg(fn, ti1, table):
    rows = "\n".join(" | ".join(str(c) for c in r) for r in table)
    return f"===== 第一层附件：{fn} =====\n===== 报价明细表：表格{ti1} =====\n{rows}"


def case1_single_att_single_table():
    out = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "报价明细表.pdf", "tables": [PRICE_TABLE]}]))
    exp = BODY + "\n\n" + _seg("报价明细表.pdf", 1, PRICE_TABLE)
    assert out == exp, f"case1 不符\n got={out!r}\n exp={exp!r}"
    return "单附件单表：入选 1 张，表格编号 表格1，正文段在前"


def case2_single_att_multi_table():
    out = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "明细表.pdf", "tables": [PRICE_TABLE, PRICE_TABLE_2]}]))
    exp = (BODY + "\n\n" + _seg("明细表.pdf", 1, PRICE_TABLE)
           + "\n\n" + _seg("明细表.pdf", 2, PRICE_TABLE_2))
    assert out == exp, f"case2 不符\n got={out!r}\n exp={exp!r}"
    assert out.count("===== 第一层附件：") == 2, "同附件多表应各出一段（文件名不去重）"
    return "单附件多表：2 张均入选，按原顺序输出 表格1/表格2，文件名各重复一次"


def case2b_numbering_keeps_original_index():
    out = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "明细表.pdf", "tables": [NEG_TABLE, PRICE_TABLE]}]))
    exp = BODY + "\n\n" + _seg("明细表.pdf", 2, PRICE_TABLE)
    assert out == exp, f"case2b 不符\n got={out!r}\n exp={exp!r}"
    assert "表格1" not in out, "未入选的第 1 张不应出现"
    return "编号用例：第 1 张（负面表）不入选，入选表仍按原始序号显示为 表格2"


def case3_multi_attachments():
    out = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "A报价表.pdf", "tables": [PRICE_TABLE]},
        {"filename": "B报价表.pdf", "tables": [PRICE_TABLE_2]}]))
    exp = (BODY + "\n\n" + _seg("A报价表.pdf", 1, PRICE_TABLE)
           + "\n\n" + _seg("B报价表.pdf", 1, PRICE_TABLE_2))
    assert out == exp, f"case3 不符\n got={out!r}\n exp={exp!r}"
    return "多附件：附件顺序保留（A→B），各自独立编号 表格1，文件名分隔符不变"


def case4_empty_tables():
    out1 = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "空表.pdf", "tables": []}]))
    out2 = build_lora_input(_p("<p>公告正文</p>", []))
    assert out1 == BODY and out2 == BODY, f"case4 不符: {out1!r} / {out2!r}"
    return "空 tables / 空 attachments：仅正文段，不抛异常，不产生附件段"


def case5_legacy_dict_format():
    out_dict = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "报价明细表.pdf",
         "tables": [{"table_index": 0, "data": PRICE_TABLE}]}]))
    out_list = build_lora_input(
        _p("<p>公告正文</p>", [{"filename": "报价明细表.pdf", "tables": [PRICE_TABLE]}]))
    assert out_dict == out_list, "旧 dict 与新 list 结果应逐字节相同"
    # dict 缺 table_index → 沿用原有默认 0（渲染为 表格1）
    out_no_ti = build_lora_input(_p("<p>公告正文</p>", [
        {"filename": "报价明细表.pdf", "tables": [{"data": PRICE_TABLE}]}]))
    assert out_no_ti == out_list, "dict 缺 table_index 应保持原默认行为（表格1）"
    return "旧 dict 格式：与新 list 结果逐字节相同；缺 table_index 仍为 表格1"


def case6_html_plus_attachment():
    html = "<div>招标公告<br/>项目名称：测试项目</div>\n<p>预算：1000 元</p>"
    out = build_lora_input(_p(html, [
        {"filename": "报价明细表.pdf", "tables": [PRICE_TABLE]}]))
    assert out.startswith("===== 公告正文 =====\n"), out[:40]
    assert "测试项目" in out and "预算：1000 元" in out, "正文内容应进入输入"
    assert out.endswith(_seg("报价明细表.pdf", 1, PRICE_TABLE)), out[-80:]
    assert out.count("===== 公告正文 =====") == 1
    return "HTML 正文 + 附件联合：正文段（preprocess_text 清洗后）在前，附件段在后"


def case7_training_byte_identity():
    """list 格式 payload ↔ 训练 user 逐字节一致（含 SHA-256）。"""
    idx = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        idx.setdefault(rec["announcement_id"], []).append((jf.name, rec))

    total = 0
    bad = []
    first = None
    for line in TEST_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        aid = rec["metadata"]["announcement_id"]
        expected = rec["messages"][1]["content"]
        hp = VAL_DIR / f"{aid}.html"
        if not hp.exists():
            continue
        items = [(fn, r) for fn, r in idx.get(aid, []) if r["parse_status"] == "success"]
        items.sort(key=lambda kv: kv[0])
        atts = [{"filename": r["attachment_filename"],
                 "tables": [t.get("data") or [] for t in r["tables"]]}
                for _, r in items]                       # 新接口二维 list 结构
        payload = {"announcement_id": aid, "text": "",
                   "html_text": hp.read_text(encoding="utf-8", errors="ignore"),
                   "html_tables": [], "attachments": atts}
        got = build_lora_input(payload)
        total += 1
        if first is None and atts:
            first = (aid, sha256(got), sha256(expected),
                     len(got), len(expected), len(atts),
                     sum(len(a["tables"]) for a in atts))
        if got != expected:
            bad.append(aid)
    assert total > 0, "未找到可比训练样本"
    return total, bad, first


def run():
    checks = [
        ("1 单附件单表", case1_single_att_single_table),
        ("2 单附件多表", case2_single_att_multi_table),
        ("2b 表编号保持原序号", case2b_numbering_keeps_original_index),
        ("3 多附件", case3_multi_attachments),
        ("4 空 tables", case4_empty_tables),
        ("5 旧 dict 格式", case5_legacy_dict_format),
        ("6 HTML 正文+附件", case6_html_plus_attachment),
    ]
    print("== 六项结构回归 ==")
    for name, fn in checks:
        print(f"  PASS [{name}] {fn()}")

    total, bad, first = case7_training_byte_identity()
    print("== 训练输入逐字节一致性（list 格式 payload）==")
    if first:
        aid, h_got, h_exp, lg, le, na, nt = first
        print(f"  样本 {aid}：附件 {na} 个 / 表 {nt} 张；len(got)={lg} len(exp)={le}")
        print(f"  SHA-256 got = {h_got}")
        print(f"  SHA-256 exp = {h_exp}")
    print(f"  逐字节一致：{total - len(bad)} / {total}；不一致：{len(bad)} {bad[:5]}")
    assert not bad, f"存在不一致样本：{bad}"
    print("ALL PASS：list 格式输入适配后与训练 user 逐字节一致")


if __name__ == "__main__":
    run()
