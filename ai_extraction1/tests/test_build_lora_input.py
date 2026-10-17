# -*- coding: utf-8 -*-
"""test_build_lora_input.py — 校验 build_lora_input 与训练 user 逐字节一致。

数据来源（全部只读，不修改训练脚本）：
  - 训练基准：ai_extraction/experiments/qwen_sft/test_550_att_messages.jsonl
  - 正文 html：ai_extraction/data/validation/{aid}.html
  - 附件表格：ai_extraction/data/attachments_550/parsed_550/{aid}/*.json

复原方式（与训练管线一致）：
  - 附件顺序 = sorted(parsed json 文件路径) 对同一 aid 等价于按文件名排序
  - 只取 parse_status == "success" 的附件
  - 每张表保留 table_index / data

运行：
  E:\\ICT竞赛项目\\.venv\\Scripts\\python.exe ai_extraction/tests/test_build_lora_input.py
（也可用 pytest 收集 test_* 函数）
"""
import json
import sys
from pathlib import Path

# 让脚本可直接运行（把项目根加入 sys.path）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_extraction.preprocessing.build_lora_input import build_lora_input  # noqa: E402

EXTRACT = ROOT / "ai_extraction"
VAL_DIR = EXTRACT / "data" / "validation"
PARSED_DIR = EXTRACT / "data" / "attachments_550" / "parsed_550"
TEST_JSONL = EXTRACT / "experiments" / "qwen_sft" / "test_550_att_messages.jsonl"

# 必须覆盖的类别：{类别: aid}
REQUIRED = {
    "单附件": "t20260402_26351983",
    "多附件": "t20260610_26727619",
    "多表(单附件6表)": "t20260603_26679396",
    "无附件": "t20260212_26174981",
}


def _load_parsed_index():
    idx = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        idx.setdefault(rec["announcement_id"], []).append((jf.name, rec))
    return idx


def _attachments_of(aid, idx):
    items = [(fn, rec) for fn, rec in idx.get(aid, [])
             if rec["parse_status"] == "success"]
    items.sort(key=lambda kv: kv[0])
    return [{"filename": rec["attachment_filename"], "tables": rec["tables"]}
            for _, rec in items]


def _load_samples():
    """返回 [(aid, payload, expected_user), ...]。"""
    idx = _load_parsed_index()
    samples = []
    for line in TEST_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        aid = rec["metadata"]["announcement_id"]
        expected = rec["messages"][1]["content"]
        html_path = VAL_DIR / f"{aid}.html"
        if not html_path.exists():
            continue
        payload = {
            "announcement_id": aid,
            "text": "",                       # qwen 路径用；lora 装配忽略
            "html_text": html_path.read_text(encoding="utf-8", errors="ignore"),
            "html_tables": [],                # 默认忽略
            "attachments": _attachments_of(aid, idx),
        }
        samples.append((aid, payload, expected))
    return samples


def _first_diff(got, exp):
    n = min(len(got), len(exp))
    for i in range(n):
        if got[i] != exp[i]:
            return i
    return n if len(got) != len(exp) else -1


def _report_failure(aid, got, exp):
    pos = _first_diff(got, exp)
    if pos < 0:
        return
    lo = max(0, pos - 30)
    print(f"  FAIL {aid}: len(got)={len(got)} len(exp)={len(exp)} 首个差异位置={pos}")
    print(f"    got[{lo}:{pos + 30}] = {got[lo:pos + 30]!r}")
    print(f"    exp[{lo}:{pos + 30}] = {exp[lo:pos + 30]!r}")


def run():
    samples = _load_samples()
    assert samples, f"未找到可用样本：{TEST_JSONL}"

    by_aid = {aid: (payload, exp) for aid, payload, exp in samples}

    # 类别覆盖检查
    print("== 类别覆盖 ==")
    for cat, aid in REQUIRED.items():
        hit = aid in by_aid
        print(f"  {cat}: {aid} -> {'OK' if hit else '缺失'}")
        assert hit, f"必需类别样本缺失: {cat} ({aid})"

    # 逐条逐字节断言
    print("== 逐字节比对 ==")
    passed = failed = 0
    for aid, payload, exp in samples:
        got = build_lora_input(payload)
        if got == exp:
            passed += 1
        else:
            failed += 1
            _report_failure(aid, got, exp)

    n_att = len(by_aid[REQUIRED["单附件"]][0]["attachments"])
    print(f"  {REQUIRED['单附件']} 附件数={n_att}")
    print(f"  {REQUIRED['多附件']} 附件数={len(by_aid[REQUIRED['多附件']][0]['attachments'])}")
    print(f"  {REQUIRED['多表(单附件6表)']} 入选表数="
          f"{by_aid[REQUIRED['多表(单附件6表)']][1].count('===== 报价明细表：')}")
    print(f"  {REQUIRED['无附件']} 附件数={len(by_aid[REQUIRED['无附件']][0]['attachments'])}")

    print(f"== 结果 == 通过 {passed} / 共 {len(samples)}，失败 {failed}")
    assert failed == 0, f"存在 {failed} 条不一致"
    print("ALL PASS：build_lora_input 输出与训练 user 逐字节一致")
    return passed, len(samples)


def test_build_lora_input_all():
    run()


if __name__ == "__main__":
    run()
