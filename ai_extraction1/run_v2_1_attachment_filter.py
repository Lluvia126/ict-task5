"""V2.1 正式 50 条 Qwen 重跑与评估。

用法：
  python -m ai_extraction.run_v2_1_attachment_filter --extract   # 正式抽取（复用 V2.1 审计结果）
  python -m ai_extraction.run_v2_1_attachment_filter --evaluate  # 评估 + 四组对比
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
from ai_extraction.preprocessing.v2_1_table_filter import (
    KW_IDENTITY_V21, STRONG_IDENTITY, TABLE_SCORE_THRESHOLD, render_table,
)
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL
from ai_extraction.run_validation_50 import CountingExtractor, TeeLog

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
DATA = BASE / "data" / "validation_attachment_test"
PARSED_DIR = DATA / "parsed"
AUDIT_DIR = DATA / "run_v2_1_attachment_filter"
RUN_DIR = DATA / "run_v2_1_attachment"
LOG_DIR = RUN_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")


def read_validation_aids():
    rows = list(csv.DictReader(
        CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    return [r["announcement_id"].strip() for r in rows]


def load_selected():
    """从 V2.1 selection_audit.csv 恢复入选表。"""
    parsed = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        parsed[(rec["announcement_id"], rec["attachment_filename"])] = rec
    selected = {}
    for r in csv.DictReader(
            open(AUDIT_DIR / "selection_audit.csv", encoding="utf-8-sig")):
        if r["selected_v21"] != "True":
            continue
        aid, fn, ti = r["announcement_id"], r["attachment_name"], int(
            r["table_index"])
        tb = parsed[(aid, fn)]["tables"][ti]
        selected.setdefault(aid, []).append((fn, ti, tb))
    return selected


def build_input(aid, selected_by_aid):
    """公告正文 + V2.1 入选报价表；无入选表 → HTML-only。"""
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
    return "\n\n".join(parts), ("html+v21tables" if items else "html_only")


def do_extract():
    rerun = "--rerun" in sys.argv
    selected_by_aid = load_selected()
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())
    print(f"[v2.1] Prompt=V1原 模型: {qwen_extractor.QWEN_MODEL} "
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
        "experiment": "V2.1: HTML + V2.1 候选报价表",
        "threshold": TABLE_SCORE_THRESHOLD,
        "total": len(records),
        "success": sum(1 for r in records if r.get("status") == "success"),
        "input_kind": {
            "html+v21tables": sum(1 for r in records
                                  if r.get("input_kind") == "html+v21tables"),
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
    print("\n===== V2.1 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def do_evaluate():
    from ai_extraction.evaluation.evaluate_attachment_run import macro, read_pred
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_v21, _ = read_pred(RUN_DIR)
    stats, counts, match_how = evaluate(gold, pred_v21)
    m = macro(stats)

    html_only = json.loads((BASE / "data" / "validation_run_final"
                            / "evaluation_50_result.json")
                           .read_text(encoding="utf-8"))
    v1 = json.loads((DATA / "run_v1_attachment"
                     / "evaluation_attachment_result.json")
                    .read_text(encoding="utf-8"))
    v2 = json.loads((DATA / "run_v2_attachment_filter"
                     / "evaluation_v2_result.json")
                    .read_text(encoding="utf-8"))

    pred_v1, _ = read_pred(DATA / "run_v1_attachment")
    pred_v2, _ = read_pred(DATA / "run_v2_attachment_filter")

    # 专项
    t206 = "t20260206_26155241"
    t206_v2_miss = 65
    t206_v21_gold_miss = None
    gold_raw = json.loads(Path(
        r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json"
    ).read_text(encoding="utf-8"))
    gold_by = {r["announcement_id"]: r.get("products", []) for r in gold_raw}
    def norm(s):
        if not s: return ""
        import re
        return re.sub(r"[\s（）()\[\]【】、,，。:：·\-—_/\\]+", "", str(s))
    gnames = {norm(p.get("product_service_name")) for p in gold_by.get(t206, []) if p.get("product_service_name")}
    pnames = {norm(p.get("product_service_name")) for p in pred_v21.get(t206, []) if p.get("product_service_name")}
    t206_v21_gold_miss = len(gnames - pnames)

    t302 = "t20260302_26207995"
    t302_gold = len(gold_by.get(t302, []))
    t302_pred = len(pred_v21.get(t302, []))

    t508 = "t20260508_26523656"
    t508_pred_v1 = len(pred_v1.get(t508, []))
    t508_pred_v2 = len(pred_v2.get(t508, []))
    t508_pred_v21 = len(pred_v21.get(t508, []))

    result = {
        "macro_f1": m,
        "macro_f1_html_only": html_only["macro_f1"],
        "macro_f1_v1_full_attachment": v1["macro_f1"],
        "macro_f1_v2": v2["macro_f1"],
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "match_breakdown": match_how,
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "special": {
            "t20260206_gold_missed_v2": t206_v2_miss,
            "t20260206_gold_missed_v21": t206_v21_gold_miss,
            "t20260302_gold_products": t302_gold,
            "t20260302_pred_products_v21": t302_pred,
            "t20260508_pred_v1": t508_pred_v1,
            "t20260508_pred_v2": t508_pred_v2,
            "t20260508_pred_v21": t508_pred_v21,
        },
    }
    (RUN_DIR / "evaluation_v2_1_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== HTML-only vs V1 vs V2 vs V2.1 =====\n")
    print(f"{'指标':<16}{'HTML-only':>10}{'V1':>10}{'V2':>10}{'V2.1':>10}")
    print(f"{'Macro-F1':<16}{html_only['macro_f1']:>10.4f}{v1['macro_f1']:>10.4f}{v2['macro_f1']:>10.4f}{m:>10.4f}")
    print(f"{'Pred products':<16}{html_only['pred_products']:>10}{v1['pred_products']:>10}{v2['pred_products']:>10}{counts['pred_products']:>10}")
    print(f"{'Matched':<16}{html_only['matched']:>10}{v1['matched']:>10}{v2['matched']:>10}{counts['matched']:>10}")
    print(f"{'Unmatched Gold':<16}{html_only['unmatched_gold']:>10}{v1['unmatched_gold']:>10}{v2['unmatched_gold']:>10}{counts['unmatched_gold']:>10}")
    print(f"{'Unmatched Pred':<16}{html_only['unmatched_pred']:>10}{v1['unmatched_pred']:>10}{v2['unmatched_pred']:>10}{counts['unmatched_pred']:>10}")

    print("\n7字段 P/R/F1 四组对比：")
    print(f"{'字段':<12}{'HTML-F1':>8}{'V1-F1':>8}{'V2-F1':>8}{'V2.1-F1':>9}{'ΔvsV2':>8}{'V2.1-P':>8}{'V2.1-R':>8}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        print(f"{cn:<12}{html_only['fields'][cn]['F1']:>8.4f}{v1['fields'][cn]['F1']:>8.4f}{v2['fields'][cn]['F1']:>8.4f}{s['F1']:>9.4f}{s['F1']-v2['fields'][cn]['F1']:>+8.4f}{s['Precision']:>8.4f}{s['Recall']:>8.4f}")

    print(f"\nV2.1 vs HTML-only ΔMacro: {m - html_only['macro_f1']:+.4f}")
    print(f"V2.1 vs V1 ΔMacro: {m - v1['macro_f1']:+.4f}")
    print(f"V2.1 vs V2 ΔMacro: {m - v2['macro_f1']:+.4f}")

    print("\n===== 专项公告检查 =====")
    print(f"t20260206: V2漏{t206_v2_miss} → V2.1漏{t206_v21_gold_miss} (恢复{t206_v2_miss - t206_v21_gold_miss})")
    print(f"t20260302: Gold={t302_gold} V2.1 Pred={t302_pred} (损坏PDF仍不可恢复)")
    print(f"t20260508: V1抽{t508_pred_v1} → V2抽{t508_pred_v2} → V2.1抽{t508_pred_v21} (技术要求清单未回流)")


if __name__ == "__main__":
    if "--evaluate" in sys.argv:
        do_evaluate()
    else:
        do_extract()
