"""50 条独立验证集字段级评估（Task 1 七字段 P/R/F1 + Macro-F1）。

数据：
  Gold: E:\\项目数据与统计\\.. 之外，人工验证 JSON（结构 = AI 输出格式，含 products 七字段）
  Pred: ai_extraction/data/validation_run_final/*.json

复用 evaluate_goldset / evaluate_goldset_v2 的规范化与三级匹配逻辑：
  1. announcement_id 一致  2. 产品名完全一致  3. canonical 规范化
  4. 显式人工映射（EXPLICIT_MAP 仅含 10 Gold 公告的键，本 50 条不会命中，安全复用）
  5. 顺序兜底（全空 Gold 行不参与）

不调用 Qwen，不修改 Gold / AI 输出 / 任何业务代码。
"""
import csv
import json
import re
from pathlib import Path

from ai_extraction.evaluation.evaluate_goldset import (
    FIELDS, is_empty, fields_match, normalize_field,
)
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    canonical_name, match_products_v2,
)

BASE = Path(__file__).parent.parent
GOLD_JSON = Path(
    r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json")
PRED_DIR = BASE / "data" / "validation_run_final"
RESULT_CSV = PRED_DIR / "evaluation_50_result.csv"
RESULT_JSON = PRED_DIR / "evaluation_50_result.json"

# 前导序号前缀，如 "1-1 其他维修和保养服务"
ENUM_PREFIX = re.compile(r"^\s*\d+(?:[-.、]\d+)*\s*")
SPACE_ALL = re.compile(r"\s+")


def read_gold():
    data = json.loads(GOLD_JSON.read_text(encoding="utf-8"))
    return {r["announcement_id"]: r.get("products", []) for r in data}


def read_pred():
    out, status = {}, {}
    for jf in sorted(PRED_DIR.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        out[aid] = (r.get("result") or {}).get("products", []) or []
        status[aid] = r.get("status", "unknown")
    return out, status


def classify_text_diff(ai_val, gold_val):
    """双方非空但规范化后不同的文本字段：区分 格式差异 / 值错误。"""
    a = normalize_field(ai_val, False)
    g = normalize_field(gold_val, False)
    if a == g:
        return None
    if SPACE_ALL.sub("", a) == SPACE_ALL.sub("", g):
        return "格式差异"
    if ENUM_PREFIX.sub("", a) == ENUM_PREFIX.sub("", g):
        return "格式差异"
    if a in g or g in a:
        return "格式差异"
    return "值错误"


def evaluate(gold_by_aid, pred_by_aid):
    stats = {en: {"TP": 0, "FP": 0, "FN": 0, "gold_valid": 0,
                  "value_error": 0, "format_diff": 0,
                  "miss_in_ai": 0, "extra_in_ai": 0}
             for en, _, _ in FIELDS}
    counts = {"matched": 0, "unmatched_gold": 0, "unmatched_pred": 0,
              "empty_gold_rows": 0,
              "gold_products": sum(len(v) for v in gold_by_aid.values()),
              "pred_products": sum(len(v) for v in pred_by_aid.values())}
    match_how = {"exact": 0, "canonical": 0, "mapped": 0, "order": 0}

    for aid in sorted(gold_by_aid):
        gold_products = gold_by_aid[aid]
        pred_products = pred_by_aid.get(aid, [])

        matched = match_products_v2(aid, gold_products, pred_products)
        matched_g = {m[0] for m in matched}
        matched_p = {m[1] for m in matched}
        counts["matched"] += len(matched)
        for _, _, how in matched:
            match_how[how] = match_how.get(how, 0) + 1

        # 已匹配记录：逐字段判定
        for gi, pi, _how in matched:
            gp, pp = gold_products[gi], pred_products[pi]
            for en, _cn, is_num in FIELDS:
                gv, av = gp.get(en), pp.get(en)
                ge, ae = is_empty(gv), is_empty(av)
                if ge and ae:
                    continue
                if not ge:
                    stats[en]["gold_valid"] += 1
                if not ge and not ae:
                    if fields_match(av, gv, is_num):
                        stats[en]["TP"] += 1
                    else:
                        stats[en]["FP"] += 1
                        stats[en]["FN"] += 1
                        if is_num:
                            stats[en]["value_error"] += 1
                        else:
                            kind = classify_text_diff(av, gv)
                            stats[en][
                                "format_diff" if kind == "格式差异"
                                else "value_error"] += 1
                elif not ge and ae:
                    stats[en]["FN"] += 1
                    stats[en]["miss_in_ai"] += 1
                else:
                    stats[en]["FP"] += 1
                    stats[en]["extra_in_ai"] += 1

        # 未匹配 Gold（全空行不计错误）
        for gi, gp in enumerate(gold_products):
            if gi in matched_g:
                continue
            if all(is_empty(gp.get(en)) for en, _, _ in FIELDS):
                counts["empty_gold_rows"] += 1
                continue
            counts["unmatched_gold"] += 1
            for en, _, _ in FIELDS:
                if not is_empty(gp.get(en)):
                    stats[en]["FN"] += 1
                    stats[en]["gold_valid"] += 1
                    stats[en]["miss_in_ai"] += 1

        # 未匹配 Pred
        for pi, pp in enumerate(pred_products):
            if pi in matched_p:
                continue
            counts["unmatched_pred"] += 1
            for en, _, _ in FIELDS:
                if not is_empty(pp.get(en)):
                    stats[en]["FP"] += 1
                    stats[en]["extra_in_ai"] += 1

    for en, _, _ in FIELDS:
        s = stats[en]
        tp, fp, fn = s["TP"], s["FP"], s["FN"]
        s["Precision"] = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
        s["Recall"] = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
        pr = s["Precision"] + s["Recall"]
        s["F1"] = round(2 * s["Precision"] * s["Recall"] / pr, 4) if pr else 0.0
    return stats, counts, match_how


def main():
    gold_by_aid = read_gold()
    pred_by_aid, pred_status = read_pred()

    stats, counts, match_how = evaluate(gold_by_aid, pred_by_aid)
    macro = round(sum(stats[en]["F1"] for en, _, _ in FIELDS) / len(FIELDS), 4)

    n_files = len(pred_by_aid)
    n_success = sum(1 for v in pred_status.values() if v == "success")

    # CSV
    with open(RESULT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["字段", "TP", "FP", "FN", "Precision", "Recall", "F1",
                    "Gold非空数量", "值错误", "格式差异", "AI漏值", "AI多值"])
        for en, cn, _ in FIELDS:
            s = stats[en]
            w.writerow([cn, s["TP"], s["FP"], s["FN"], s["Precision"],
                        s["Recall"], s["F1"], s["gold_valid"],
                        s["value_error"], s["format_diff"],
                        s["miss_in_ai"], s["extra_in_ai"]])
        w.writerow(["Macro-F1", "", "", "", "", "", macro, "", "", "", "", ""])

    # JSON
    result = {
        "note": "50条独立验证集 Task1 七字段评估；Macro-F1 为本验证集综合指标，非比赛官方最终得分",
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "empty_gold_rows": counts["empty_gold_rows"],
        "match_breakdown": match_how,
        "ai_files": n_files,
        "ai_success": n_success,
        "avg_products_per_announcement": {
            "gold": round(counts["gold_products"] / 50, 2),
            "pred": round(counts["pred_products"] / 50, 2),
        },
        "macro_f1": macro,
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "baseline_reference": {
            "goldset_133_v1_rerun_macro_f1": 0.8922,
            "note": "数据集不同（133条Gold vs 50条独立验证集），不直接比较高低",
        },
    }
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 控制台报告
    print("===== 50条独立验证集最终评估 =====\n")
    print(f"AI 成功: {n_success}/{n_files}")
    print(f"Gold products: {counts['gold_products']}")
    print(f"Pred products: {counts['pred_products']}")
    print(f"Matched: {counts['matched']}  "
          f"(exact={match_how.get('exact', 0)} "
          f"canonical={match_how.get('canonical', 0)} "
          f"mapped={match_how.get('mapped', 0)} "
          f"order={match_how.get('order', 0)})")
    print(f"Unmatched Gold (漏抽): {counts['unmatched_gold']}")
    print(f"Unmatched Pred (多抽): {counts['unmatched_pred']}")
    print(f"全空Gold行(不计错误): {counts['empty_gold_rows']}")
    print(f"平均 products/公告: Gold={counts['gold_products']/50:.2f} "
          f"Pred={counts['pred_products']/50:.2f}\n")

    print(f"{'字段':<14}{'TP':>5}{'FP':>5}{'FN':>5}{'P':>9}{'R':>9}{'F1':>9}"
          f"{'Gold非空':>9}{'值错误':>7}{'格式差异':>9}{'AI漏值':>7}{'AI多值':>7}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        print(f"{cn:<14}{s['TP']:>5}{s['FP']:>5}{s['FN']:>5}"
              f"{s['Precision']:>9.4f}{s['Recall']:>9.4f}{s['F1']:>9.4f}"
              f"{s['gold_valid']:>9}{s['value_error']:>7}{s['format_diff']:>9}"
              f"{s['miss_in_ai']:>7}{s['extra_in_ai']:>7}")

    print(f"\nMacro-F1: {macro}")
    print("\n对照（数据集不同，不直接比较高低）：")
    print(f"  133条Gold 当前可复现V1 Macro-F1 = 0.8922")
    print(f"  50条独立验证集 Macro-F1 = {macro}")
    print(f"\nCSV: {RESULT_CSV}\nJSON: {RESULT_JSON}")


if __name__ == "__main__":
    main()
