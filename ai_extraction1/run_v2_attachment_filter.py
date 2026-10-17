"""V2 实验运行器：筛选审计 + 正式抽取 + 评估三阶段。

用法：
  python -m ai_extraction.run_v2_attachment_filter --audit     # 只做筛选审计（不调Qwen）
  python -m ai_extraction.run_v2_attachment_filter --extract   # 正式V2抽取（用已审计结果）
  python -m ai_extraction.run_v2_attachment_filter --evaluate  # 评估+三组对比
"""
import contextlib
import csv
import json
import random
import sys
import time
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.v2_table_filter import (
    TABLE_SCORE_THRESHOLD, HEADER_SCAN_ROWS, score_table, render_table,
    _norm_cell,
)
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL
from ai_extraction.run_validation_50 import CountingExtractor, TeeLog

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
DATA = BASE / "data" / "validation_attachment_test"
PARSED_DIR = DATA / "parsed"
RUN_DIR = DATA / "run_v2_attachment_filter"
LOG_DIR = RUN_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")
GOLD_JSON = Path(
    r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json")


def read_validation_aids():
    rows = list(csv.DictReader(
        CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    aids = [r["announcement_id"].strip() for r in rows]
    assert len(aids) == 50
    return aids


def read_price_candidates():
    pc = list(csv.DictReader(open(
        DATA / "price_detail_candidates.csv", encoding="utf-8-sig")))
    cand = {}
    for r in pc:
        cand.setdefault((r["announcement_id"],
                         r["attachment_filename"]), set()).update(
            r["matched_keywords"].split("|"))
    return cand


def load_parsed():
    """parsed JSON → {(aid, filename): rec}。"""
    out = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        out[(rec["announcement_id"], rec["attachment_filename"])] = rec
    return out


# ================================================================ 审计

def do_audit():
    aids = read_validation_aids()
    cand = read_price_candidates()
    parsed = load_parsed()

    # 6 个损坏附件（parse_status=failed）
    broken = {(r["announcement_id"], r["attachment_filename"])
              for r in parsed.values() if r["parse_status"] != "success"}

    audit_rows = []
    selected_by_aid = {}   # aid -> [(filename, table_index)]
    for aid in aids:
        for (a, fn), kws in cand.items():
            if a != aid:
                continue
            rec = parsed.get((a, fn))
            if not rec or rec["parse_status"] != "success":
                audit_rows.append([aid, fn, "", "", "", "broken_or_unparsed",
                                   False])
                continue
            for tb in rec["tables"]:
                score, matched, neg, detail = score_table(tb)
                sel = score >= TABLE_SCORE_THRESHOLD
                audit_rows.append([aid, fn, tb["table_index"], score,
                                   "|".join(matched),
                                   "|".join(neg) if not sel else "",
                                   sel])
                if sel:
                    selected_by_aid.setdefault(aid, []).append(
                        (fn, tb["table_index"], tb))

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "logs").mkdir(exist_ok=True)
    with open(RUN_DIR / "selection_audit.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_name", "table_index",
                    "score", "matched_keywords", "excluded_reason",
                    "selected"])
        w.writerows(audit_rows)

    # selected_tables/ 每公告保留的表格渲染文本
    sel_dir = RUN_DIR / "selected_tables"
    sel_dir.mkdir(exist_ok=True)
    for aid, items in selected_by_aid.items():
        parts = []
        for fn, ti, tb in items:
            parts.append(f"===== 报价明细表：{fn} / 表格{ti + 1} =====\n"
                         + render_table(tb))
        (sel_dir / f"{aid}.txt").write_text("\n\n".join(parts),
                                            encoding="utf-8")

    # 统计
    sel_rows = [r for r in audit_rows if r[6]]
    filter_rows = [r for r in audit_rows if not r[6]]
    aids_with_cand = sorted({r[0] for r in audit_rows})
    aids_with_sel = sorted(selected_by_aid)
    pc_aids = sorted({a for a, _ in cand})
    retained = [a for a in pc_aids if a in aids_with_sel]
    misfiltered = [a for a in pc_aids if a not in aids_with_sel]

    # 样例展示
    rng = random.Random(42)
    def hdr_of(aid, fn, ti):
        tb = next(t for f_, t_i, t in selected_by_aid.get(aid, [])
                  if f_ == fn and t_i == ti) if False else None
        rec = parsed[(aid, fn)]
        tb = rec["tables"][ti]
        rows = tb["data"][:2]
        return " | ".join(_norm_cell(c)[:18] if c else ""
                          for c in rows[0])[:120]

    sel_sample = rng.sample(sel_rows, min(10, len(sel_rows)))
    filt_sample = rng.sample(filter_rows, min(10, len(filter_rows)))

    # 特别检查
    special = {}
    for aid, focus in [("t20260508_26523656", "技术要求清单"),
                       ("t20260303_26212365", "零部件/研磨罐"),
                       ("t20260303_26212764", "包级标题")]:
        rows = [r for r in audit_rows if r[0] == aid]
        special[aid] = {
            "focus": focus,
            "candidate_tables": len(rows),
            "selected": sum(1 for r in rows if r[6]),
            "selected_detail": [
                {"attachment": r[1], "table": r[2], "score": r[3],
                 "keywords": r[4]} for r in rows if r[6]],
            "filtered_detail": [
                {"attachment": r[1], "table": r[2], "score": r[3],
                 "excluded": r[5]} for r in rows if not r[6]][:8],
        }

    audit_json = {
        "threshold": TABLE_SCORE_THRESHOLD,
        "header_scan_rows": HEADER_SCAN_ROWS,
        "announcements_total": 50,
        "announcements_with_price_candidates": len(aids_with_cand),
        "announcements_with_selected_tables": len(aids_with_sel),
        "avg_selected_tables_per_aid": round(
            len(sel_rows) / max(1, len(aids_with_sel)), 2),
        "candidate_attachments": len(cand),
        "tables_total_in_candidates": len(
            [r for r in audit_rows if r[2] != ""]),
        "tables_selected": len(sel_rows),
        "tables_filtered": len(filter_rows),
        "price_aids_retained": retained,
        "price_aids_misfiltered": misfiltered,
        "broken_attachments_excluded": sorted(f"{a}|{f}" for a, f in broken
                                              if a in aids_with_cand),
        "special_checks": special,
    }
    (RUN_DIR / "selection_audit.json").write_text(
        json.dumps(audit_json, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("===== V2 筛选审计 =====")
    print(f"阈值: score>={TABLE_SCORE_THRESHOLD}, 表头扫描行数: {HEADER_SCAN_ROWS}")
    print(f"50 条公告中保留候选附件: {len(aids_with_sel)} 条")
    print(f"候选表格总数: {audit_json['tables_total_in_candidates']}")
    print(f"入选表格: {len(sel_rows)} | 过滤表格: {len(filter_rows)}")
    print(f"平均每公告保留表格: {audit_json['avg_selected_tables_per_aid']}")
    print(f"29 个报价明细公告: 保留 {len(retained)} | 误过滤 {len(misfiltered)}: {misfiltered}")
    print(f"损坏附件排除: {audit_json['broken_attachments_excluded']}")
    print("\n--- 选中表格表头样例(10) ---")
    for r in sel_sample:
        print(f"  [score={r[3]}] {r[0]} | {r[1][:28]} | 表{r[2]+1}: {hdr_of(r[0], r[1], r[2])}")
    print("\n--- 过滤表格表头样例(10) ---")
    for r in filt_sample:
        print(f"  [score={r[3]} 排除:{r[5]}] {r[0]} | {r[1][:24]} | 表{r[2]+1}: {hdr_of(r[0], r[1], r[2])}")
    print("\n--- 特别检查 ---")
    for aid, sp in special.items():
        print(f"  {aid}({sp['focus']}): 候选表{sp['candidate_tables']} "
              f"入选{sp['selected']} 过滤{sp['candidate_tables'] - sp['selected']}")
    print(f"\n审计产物: {RUN_DIR}\\selection_audit.csv/.json + selected_tables/")
    return selected_by_aid


# ================================================================ 抽取

def build_v2_input(aid, selected_by_aid):
    """V2 统一输入：公告正文 + 入选报价明细表。无入选表 → HTML-only。"""
    html = (VAL_DIR / f"{aid}.html").read_text(encoding="utf-8",
                                               errors="ignore")
    parts = ["===== 公告正文 =====\n" + preprocess_text(html)]
    items = selected_by_aid.get(aid, [])
    if items:
        # 去重同文件多表之间重复文件名头
        last_fn = None
        att_parts = []
        for fn, ti, tb in items:
            if fn != last_fn:
                att_parts.append(f"===== 第一层附件：{fn} =====")
                last_fn = fn
            att_parts.append(f"===== 报价明细表：表格{ti + 1} =====\n"
                             + render_table(tb))
        parts.append("\n".join(att_parts))
    return "\n\n".join(parts), ("html+v2tables" if items else "html_only")


def do_extract():
    rerun = "--rerun" in sys.argv
    selected_by_aid = load_selected()
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())
    print(f"[run_v2] Prompt=V1原 模型: {qwen_extractor.QWEN_MODEL} "
          f"输入含报价表公告: {len(selected_by_aid)}", flush=True)

    aids = read_validation_aids()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    records = []
    for i, aid in enumerate(aids, 1):
        out_path = RUN_DIR / f"{aid}.json"
        if out_path.exists() and not rerun:
            records.append(json.loads(
                out_path.read_text(encoding="utf-8")))
            print(f"[{i}/50] SKIP {aid}", flush=True)
            continue
        text, kind = build_v2_input(aid, selected_by_aid)
        extractor.reset()
        tee = TeeLog(LOG_DIR / f"{aid}.log")
        t0 = time.time()
        status = "success"
        try:
            with contextlib.redirect_stdout(tee):
                rec = process_html(text, aid, extractor, file_id="",
                                   source_file=f"{aid}.html")
        except Exception as e:  # noqa: BLE001
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

    # summary
    summary = {
        "experiment": "V2: HTML + 候选附件 + 报价表定位",
        "threshold": TABLE_SCORE_THRESHOLD,
        "total": len(records),
        "success": sum(1 for r in records if r.get("status") == "success"),
        "input_kind": {
            "html+v2tables": sum(1 for r in records
                                 if r.get("input_kind") == "html+v2tables"),
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
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8")
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
    print("\n===== V2 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def load_selected():
    """从 selection_audit.csv 恢复入选表（供 --extract 使用）。"""
    parsed = load_parsed()
    selected = {}
    for r in csv.DictReader(open(RUN_DIR / "selection_audit.csv",
                                 encoding="utf-8-sig")):
        if r["selected"] != "True":
            continue
        aid, fn, ti = r["announcement_id"], r["attachment_name"], int(
            r["table_index"])
        tb = parsed[(aid, fn)]["tables"][ti]
        selected.setdefault(aid, []).append((fn, ti, tb))
    return selected


# ================================================================ 评估

def do_evaluate():
    import sys as _s
    _s.path.insert(0, str(BASE))
    from ai_extraction.evaluation.evaluate_attachment_run import (
        macro, read_pred,
    )
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_new, _ = read_pred(RUN_DIR)
    stats, counts, match_how = evaluate(gold, pred_new)
    m = macro(stats)

    html_only = json.loads((BASE / "data" / "validation_run_final"
                            / "evaluation_50_result.json")
                           .read_text(encoding="utf-8"))
    v1 = json.loads((DATA / "run_v1_attachment"
                     / "evaluation_attachment_result.json")
                    .read_text(encoding="utf-8"))

    # 专项
    pc_aids = sorted({r["announcement_id"] for r in
                      csv.DictReader(open(DATA / "price_detail_candidates.csv",
                                          encoding="utf-8-sig"))})
    pred_html_only, _ = read_pred(BASE / "data" / "validation_run_final")
    pred_v1, _ = read_pred(DATA / "run_v1_attachment")
    increased = [a for a in pc_aids
                 if len(pred_new.get(a, [])) > len(pred_html_only.get(a, []))]
    zero3 = ["t20260508_26523656", "t20260804_27072000", "t20260811_27115533"]
    zero3_v2 = {a: len(pred_new.get(a, [])) for a in zero3}

    result = {
        "macro_f1": m,
        "macro_f1_html_only": html_only["macro_f1"],
        "macro_f1_v1_full_attachment": v1["macro_f1"],
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "match_breakdown": match_how,
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "special": {
            "price_aids_n": len(pc_aids),
            "price_aids_coverage_increased": increased,
            "zero3_v2_products": zero3_v2,
        },
    }
    (RUN_DIR / "evaluation_v2_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("\n===== V2：HTML + 候选附件 + 报价表定位 =====\n")
    print(f"HTML-only Macro-F1: {html_only['macro_f1']}")
    print(f"HTML+全附件 Macro-F1: {v1['macro_f1']}")
    print(f"V2 Macro-F1: {m}")
    print(f"\nGold products: {counts['gold_products']}")
    print(f"Pred products: {counts['pred_products']} "
          f"(HTML-only {html_only['pred_products']} / V1 {v1['pred_products']})")
    print(f"Matched: {counts['matched']}")
    print(f"Unmatched Gold: {counts['unmatched_gold']} "
          f"(HTML-only {html_only['unmatched_gold']} / V1 {v1['unmatched_gold']})")
    print(f"Unmatched Pred: {counts['unmatched_pred']} "
          f"(HTML-only {html_only['unmatched_pred']} / V1 {v1['unmatched_pred']})")

    print("\n7字段 P/R/F1（对比 V1 全附件 → V2）:")
    print(f"{'字段':<12}{'V2-P':>8}{'V2-R':>8}{'V2-F1':>8}"
          f"{'V1-F1':>8}{'ΔF1':>8}{'HTML-F1':>8}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        f1_v1 = v1["fields"][cn]["F1"]
        f1_h = html_only["fields"][cn]["F1"]
        print(f"{cn:<12}{s['Precision']:>8.4f}{s['Recall']:>8.4f}{s['F1']:>8.4f}"
              f"{f1_v1:>8.4f}{s['F1'] - f1_v1:>+8.4f}{f1_h:>8.4f}")

    print(f"\nV2 vs HTML-only ΔMacro: {m - html_only['macro_f1']:+.4f}")
    print(f"V2 vs V1 ΔMacro: {m - v1['macro_f1']:+.4f}")
    print(f"\n专项: 29报价公告覆盖增加 {len(increased)} | "
          f"3个零产品公告 V2 products: {zero3_v2}")
    print(f"JSON: {RUN_DIR}\\evaluation_v2_result.json")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        do_audit()
    elif "--extract" in sys.argv:
        do_extract()
    elif "--evaluate" in sys.argv:
        do_evaluate()
    else:
        print("用法: --audit | --extract | --evaluate")
