"""V3: V2.2 输入 + 新 Prompt + qwen3.8-max 模型。

对照 V2.2（V1 Prompt + qwen-vl-max），唯一变化：
  - Prompt → 用户设计的新 V3 Prompt
  - 模型 → qwen3.8-max（.env 已改）
  - 输入：复用 V2.2 筛选结果（340 张候选报价表）

其余不变：temperature=0 / chunk 参数 / merge / binary split recovery。
"""
import contextlib
import csv
import json
import sys
import time
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.v2_2_table_filter import TABLE_SCORE_THRESHOLD, render_table
from ai_extraction.run_validation_50 import CountingExtractor, TeeLog

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
DATA = BASE / "data" / "validation_attachment_test"
PARSED_DIR = DATA / "parsed"
AUDIT_DIR = DATA / "run_v2_2_filter_audit"
RUN_DIR = DATA / "run_v3_prompt_v22"
LOG_DIR = RUN_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")

V3_PROMPT = """你是一名负责政府采购招采标讯信息抽取的专业信息抽取模型。

你的任务是：从输入的招采公告及其附件文本中，识别并提取属于"标的物"的产品或服务实体，并为每个实体提取以下 7 个字段：

1. 产品/服务名称
2. 品目
3. 品牌（产品供应商）
4. 规格型号
5. 单价
6. 数量
7. 总价

请严格遵守以下要求：

【一、什么可以作为实体】
只提取公告或附件中实际作为采购、销售、报价、成交或服务标的物的产品或服务。

可以提取：
* 产品
* 设备
* 软件
* 服务
* 商品
* 采购清单中的具体产品或服务项目
* 报价表中的具体产品或服务明细

不要把以下内容单独作为产品/服务实体：
* 税费
* 运费
* 安装费（除非它本身明确作为独立服务项目）
* 合计
* 小计
* 折扣
* 投标保证金
* 履约保证金
* 项目编号
* 表格标题
* 表头
* 序号
* 备注
* 采购人、供应商、代理机构等主体名称

【二、附件处理】
附件中的 DOC、DOCX、XLS、XLSX、PDF 等内容与 HTML 公告具有同等重要性。

重点关注：
* 采购清单
* 报价明细表
* 分项报价表
* 中标产品明细
* 技术参数表
* 成交明细
* 服务报价表
* 产品清单

需要结合表格上下文判断一行是否属于真实标的物。

【三、字段提取规则】
产品/服务名称：
提取该标的物在原文中的名称，不要自行改写。

品目：
提取原文明确出现的品目或类别。
如果原文没有明确提供，则填写 null，不要自行推测。

品牌：
提取原文中的品牌或产品供应商。
没有则填写 null。

规格型号：
提取原文明确出现的型号、规格、参数标识。
没有则填写 null。

单价：
提取该实体对应的单价。
没有明确单价则填写 null。

数量：
提取该实体对应的数量。
没有明确数量则填写 null。

总价：
提取该实体对应的总价。
没有明确总价则填写 null。

【四、禁止虚构】
只能依据输入文本提取信息。
不能因为字段通常应该存在，就自行补全。
不能根据常识猜测品牌、型号、品目、价格或数量。
无法确认时必须填写 null。

【五、表格理解】
对于报价表、采购清单等结构化表格：
* 每一行可能对应一个独立的产品或服务实体。
* 需要结合表头和上下文理解字段含义。
* "合计""小计"等统计行不是产品实体。
* 不要因为某个单元格单独出现品牌、型号或数字，就把它们错误拆成新的实体。
* 同一产品的多个字段必须尽可能归并到同一个实体中。

【六、实体边界】
一个产品或服务对应一个实体。
同一实体的产品名称、品牌、规格型号、单价、数量、总价应放在同一个 JSON 对象中。
不要重复输出同一个实体。
如果多个实体名称不同，应分别输出。

【七、输出格式】
只输出合法 JSON，不要输出解释、分析过程、Markdown 或其他文字。

格式如下：

{
"products": [
{
"product_service_name": "产品或服务名称",
"category": "品目",
"brand": "品牌",
"spec_model": "规格型号",
"unit_price": 数值或 null,
"quantity": 数值或 null,
"total_price": 数值或 null
}
]
}

字段缺失时使用 null。

输入文本：
{text}"""


def read_validation_aids():
    rows = list(csv.DictReader(
        CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    return [r["announcement_id"].strip() for r in rows]


def load_selected():
    parsed = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        parsed[(rec["announcement_id"], rec["attachment_filename"])] = rec
    selected = {}
    for r in csv.DictReader(
            open(AUDIT_DIR / "selection_audit.csv", encoding="utf-8-sig")):
        if r["selected_v22"] != "True":
            continue
        aid, fn, ti = r["announcement_id"], r["attachment_name"], int(
            r["table_index"])
        tb = parsed[(aid, fn)]["tables"][ti]
        selected.setdefault(aid, []).append((fn, ti, tb))
    return selected


def build_input(aid, selected_by_aid):
    html = (VAL_DIR / f"{aid}.html").read_text(encoding="utf-8",
                                               errors="ignore")
    parts = ["===== 公告正文 =====\n" + preprocess_text(html)]
    items = selected_by_aid.get(aid, [])
    if items:
        last_fn = None
        att_parts = []
        for fn, ti, tb in items:
            if fn != last_fn:
                att_parts.append(f"===== 第一层附件：{fn} =====")
                last_fn = fn
            att_parts.append(f"===== 报价明细表：表格{ti + 1} =====\n"
                             + render_table(tb))
        parts.append("\n".join(att_parts))
    return "\n\n".join(parts), ("html+v22tables" if items else "html_only")


def do_extract():
    rerun = "--rerun" in sys.argv
    selected_by_aid = load_selected()

    # 替换 Prompt
    qwen_extractor.EXTRACT_PROMPT = V3_PROMPT
    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())
    print(f"[V3] Prompt=V3新(用户设计) 模型: {qwen_extractor.QWEN_MODEL} "
          f"入选表公告: {len(selected_by_aid)}", flush=True)

    aids = read_validation_aids()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for i, aid in enumerate(aids, 1):
        out_path = RUN_DIR / f"{aid}.json"
        if out_path.exists() and not rerun:
            records.append(json.loads(out_path.read_text(encoding="utf-8")))
            print(f"[{i}/50] SKIP {aid}", flush=True)
            continue
        text, kind = build_input(aid, selected_by_aid)
        extractor.reset()
        tee = TeeLog(LOG_DIR / f"{aid}.log")
        t0 = time.time()
        status = "success"
        try:
            with contextlib.redirect_stdout(tee):
                rec = process_html(text, aid, extractor, file_id="",
                                   source_file=f"{aid}.html")
        except Exception as e:
            status = "failed"
            rec = {"announcement_id": aid, "status": "failed",
                   "error": f"{type(e).__name__}: {e}", "result": None}
        finally:
            tee.close()
        elapsed = round(time.time() - t0, 2)
        plan = build_plan(text, announcement_id=aid)
        planned = sum(1 for c in plan["chunks"] if c["kind"] == "product")
        rec.update({
            "status": status, "input_kind": kind,
            "input_chars": len(text),
            "products_count": len((rec.get("result") or {})
                                  .get("products", []) or []),
            "chunks_total": len(plan["chunks"]),
            "planned_product_chunks": planned,
            "api_calls_attempts": extractor.attempts,
            "api_calls_success": extractor.success,
            "json_errors": extractor.json_errors,
            "api_errors": extractor.api_errors,
            "split_retries": max(0, extractor.product_success - planned),
            "elapsed_seconds": elapsed,
        })
        out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        records.append(rec)
        print(f"[{i}/50] RUN {aid} status={status} "
              f"products={rec.get('products_count')} "
              f"chunks={rec.get('chunks_total')} "
              f"api={rec.get('api_calls_success')}/{rec.get('api_calls_attempts')} "
              f"elapsed={elapsed}s", flush=True)

    summary = {
        "experiment": "V3: V2.2输入 + 新Prompt + qwen3.8-max",
        "prompt": "V3_PROMPT（用户设计，含实体边界/附件处理/表格归并规则）",
        "model": qwen_extractor.QWEN_MODEL,
        "threshold": TABLE_SCORE_THRESHOLD,
        "total": len(records),
        "success": sum(1 for r in records if r.get("status") == "success"),
        "input_kind": {
            "html+v22tables": sum(1 for r in records
                                  if r.get("input_kind") == "html+v22tables"),
            "html_only": sum(1 for r in records
                             if r.get("input_kind") == "html_only")},
        "products_total": sum(r.get("products_count", 0) for r in records),
        "chunks_total": sum(r.get("chunks_total", 0) for r in records),
        "api_calls_attempts": sum(r.get("api_calls_attempts", 0)
                                  for r in records),
        "api_calls_success": sum(r.get("api_calls_success", 0)
                                 for r in records),
        "json_errors": sum(r.get("json_errors", 0) for r in records),
        "api_errors": sum(r.get("api_errors", 0) for r in records),
        "split_retries": sum(r.get("split_retries", 0) for r in records),
        "elapsed_total_s": round(sum(r.get("elapsed_seconds", 0)
                                     for r in records), 1),
    }
    (RUN_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(RUN_DIR / "summary.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "status", "input_kind", "products",
                    "chunks", "api_calls", "json_errors", "split_retries",
                    "elapsed_seconds"])
        for r in records:
            w.writerow([r["announcement_id"], r.get("status"),
                        r.get("input_kind"), r.get("products_count"),
                        r.get("chunks_total"), r.get("api_calls_success"),
                        r.get("json_errors"), r.get("split_retries"),
                        r.get("elapsed_seconds")])
    print("\n===== V3 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def do_evaluate():
    from ai_extraction.evaluation.evaluate_attachment_run import macro, read_pred
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_v3, _ = read_pred(RUN_DIR)
    stats, counts, match_how = evaluate(gold, pred_v3)
    m = macro(stats)

    html_only = json.loads((BASE / "data" / "validation_run_final"
                            / "evaluation_50_result.json")
                           .read_text(encoding="utf-8"))
    v2 = json.loads((DATA / "run_v2_attachment_filter"
                     / "evaluation_v2_result.json")
                    .read_text(encoding="utf-8"))
    v22 = json.loads((DATA / "run_v2_2_attachment"
                      / "evaluation_v2_2_result.json")
                     .read_text(encoding="utf-8"))

    pred_v22, _ = read_pred(DATA / "run_v2_2_attachment")

    import re
    def norm(s):
        if not s: return ""
        return re.sub(r"[\s（）()\[\]【】、,，。:：·\-—_/\\]+", "", str(s))

    t508 = "t20260508_26523656"
    t508_pred_v22 = len(pred_v22.get(t508, []))
    t508_pred_v3 = len(pred_v3.get(t508, []))

    t206 = "t20260206_26155241"
    gold_raw = json.loads(Path(
        r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json"
    ).read_text(encoding="utf-8"))
    gold_by = {r["announcement_id"]: r.get("products", []) for r in gold_raw}
    gnames = {norm(p.get("product_service_name")) for p in gold_by.get(t206, []) if p.get("product_service_name")}
    pnames_v3 = {norm(p.get("product_service_name")) for p in pred_v3.get(t206, []) if p.get("product_service_name")}
    pnames_v22 = {norm(p.get("product_service_name")) for p in pred_v22.get(t206, []) if p.get("product_service_name")}
    t206_v22_miss = len(gnames - pnames_v22)
    t206_v3_miss = len(gnames - pnames_v3)

    result = {
        "experiment": "V3: V2.2输入 + 新Prompt + qwen3.8-max",
        "macro_f1": m,
        "macro_f1_html_only": html_only["macro_f1"],
        "macro_f1_v2": v2["macro_f1"],
        "macro_f1_v22": v22["macro_f1"],
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "special": {
            "t20260206_gold_missed_v22": t206_v22_miss,
            "t20260206_gold_missed_v3": t206_v3_miss,
            "t20260508_pred_v22": t508_pred_v22,
            "t20260508_pred_v3": t508_pred_v3,
        },
    }
    (RUN_DIR / "evaluation_v3_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== HTML-only vs V2 vs V2.2 vs V3 =====\n")
    print(f"{'指标':<16}{'HTML-only':>10}{'V2':>10}{'V2.2':>10}{'V3':>10}")
    print(f"{'Macro-F1':<16}{html_only['macro_f1']:>10.4f}{v2['macro_f1']:>10.4f}{v22['macro_f1']:>10.4f}{m:>10.4f}")
    print(f"{'Pred products':<16}{html_only['pred_products']:>10}{v2['pred_products']:>10}{v22['pred_products']:>10}{counts['pred_products']:>10}")
    print(f"{'Matched':<16}{html_only['matched']:>10}{v2['matched']:>10}{v22['matched']:>10}{counts['matched']:>10}")
    print(f"{'Unmatched Gold':<16}{html_only['unmatched_gold']:>10}{v2['unmatched_gold']:>10}{v22['unmatched_gold']:>10}{counts['unmatched_gold']:>10}")
    print(f"{'Unmatched Pred':<16}{html_only['unmatched_pred']:>10}{v2['unmatched_pred']:>10}{v22['unmatched_pred']:>10}{counts['unmatched_pred']:>10}")

    print("\n7字段 P/R/F1 四组对比：")
    print(f"{'字段':<12}{'HTML-F1':>8}{'V2-F1':>8}{'V2.2-F1':>9}{'V3-F1':>9}{'Δv3v22':>9}{'V3-P':>8}{'V3-R':>8}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        v22f = v22["fields"][cn]["F1"]
        print(f"{cn:<12}{html_only['fields'][cn]['F1']:>8.4f}{v2['fields'][cn]['F1']:>8.4f}{v22f:>9.4f}{s['F1']:>9.4f}{s['F1']-v22f:>+9.4f}{s['Precision']:>8.4f}{s['Recall']:>8.4f}")

    print(f"\nV3 vs HTML-only ΔMacro: {m - html_only['macro_f1']:+.4f}")
    print(f"V3 vs V2 ΔMacro: {m - v2['macro_f1']:+.4f}")
    print(f"V3 vs V2.2 ΔMacro: {m - v22['macro_f1']:+.4f}")

    print("\n===== 专项公告 =====")
    print(f"t20260206: V2.2漏{t206_v22_miss} → V3漏{t206_v3_miss}")
    print(f"t20260508: V2.2={t508_pred_v22} → V3={t508_pred_v3}")


if __name__ == "__main__":
    if "--evaluate" in sys.argv:
        do_evaluate()
    else:
        do_extract()
