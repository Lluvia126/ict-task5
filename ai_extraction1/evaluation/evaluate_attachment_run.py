"""run_v1_attachment（HTML+第一层附件）评估 + 与 HTML-only 对照。

复用 evaluate_validation_50 的 Gold 读取 / 三级匹配 / 字段判定 / 错误分类。
输出：
  run_v1_attachment/evaluation_attachment_result.csv / .json
不调用 Qwen，不修改任何数据。
"""
import csv
import json
from pathlib import Path

from ai_extraction.evaluation.evaluate_goldset import FIELDS
from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

BASE = Path(__file__).parent.parent
RUN_DIR = BASE / "data" / "validation_attachment_test" / "run_v1_attachment"
HTML_ONLY_JSON = BASE / "data" / "validation_run_final" / "evaluation_50_result.json"
HTML_ONLY_DIR = BASE / "data" / "validation_run_final"
OUT_CSV = RUN_DIR / "evaluation_attachment_result.csv"
OUT_JSON = RUN_DIR / "evaluation_attachment_result.json"


def read_pred(pred_dir):
    out, meta = {}, {}
    for jf in sorted(pred_dir.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        out[aid] = (r.get("result") or {}).get("products", []) or []
        meta[aid] = r
    return out, meta


def macro(stats):
    return round(sum(stats[en]["F1"] for en, _, _ in FIELDS) / len(FIELDS), 4)


def main():
    gold_by_aid = read_gold()
    pred_new, meta_new = read_pred(RUN_DIR)
    pred_old, meta_old = read_pred(HTML_ONLY_DIR)

    stats, counts, match_how = evaluate(gold_by_aid, pred_new)
    macro_new = macro(stats)

    old = json.loads(HTML_ONLY_JSON.read_text(encoding="utf-8"))
    old_fields = old["fields"]  # {中文字段: {TP,FP,FN,Precision,Recall,F1,...}}

    # CSV
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["字段", "TP", "FP", "FN", "Precision", "Recall", "F1",
                    "Gold非空数量", "值错误", "格式差异", "AI漏值", "AI多值",
                    "HTML-only F1", "F1变化"])
        for en, cn, _ in FIELDS:
            s = stats[en]
            old_f1 = old_fields[cn]["F1"]
            w.writerow([cn, s["TP"], s["FP"], s["FN"], s["Precision"],
                        s["Recall"], s["F1"], s["gold_valid"],
                        s["value_error"], s["format_diff"],
                        s["miss_in_ai"], s["extra_in_ai"],
                        old_f1, round(s["F1"] - old_f1, 4)])
        w.writerow(["Macro-F1", "", "", "", "", "", macro_new, "", "", "",
                    "", "", old["macro_f1"],
                    round(macro_new - old["macro_f1"], 4)])

    # 附件效果专项
    n_zero_old = [aid for aid in pred_old
                  if len(pred_old[aid]) == 0]
    zero_fixed = [aid for aid in n_zero_old if len(pred_new.get(aid, [])) > 0]
    increased = [aid for aid in pred_old
                 if len(pred_new.get(aid, [])) > len(pred_old[aid])]
    decreased = [aid for aid in pred_old
                 if len(pred_new.get(aid, [])) < len(pred_old[aid])]

    # 29 个第一层检出报价明细的公告
    pc_rows = list(csv.DictReader(open(
        BASE / "data" / "validation_attachment_test"
        / "price_detail_candidates.csv", encoding="utf-8-sig")))
    price_aids = sorted({r["announcement_id"] for r in pc_rows})
    price_increased = [a for a in price_aids
                       if len(pred_new.get(a, [])) > len(pred_old.get(a, []))]
    price_zero_fixed = [a for a in price_aids if a in zero_fixed]

    result = {
        "note": "50条独立验证集 HTML+第一层附件 增强抽取评估；与HTML-only对照（同一Gold、同一匹配逻辑）",
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "pred_products_html_only": old["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "match_breakdown": match_how,
        "macro_f1": macro_new,
        "macro_f1_html_only": old["macro_f1"],
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "fields_html_only": old_fields,
        "attachment_effects": {
            "zero_product_aids_html_only": n_zero_old,
            "zero_product_fixed": zero_fixed,
            "products_increased_aids": increased,
            "products_decreased_aids": decreased,
            "price_detail_aids_n": len(price_aids),
            "price_detail_aids_increased": price_increased,
            "price_detail_aids_zero_fixed": price_zero_fixed,
        },
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 控制台报告
    print("===== HTML-only vs HTML+附件 =====\n")
    print(f"HTML-only Macro-F1: {old['macro_f1']}")
    print(f"HTML+附件 Macro-F1: {macro_new}")
    print(f"\nGold products: {counts['gold_products']}")
    print(f"Pred products: {counts['pred_products']} (HTML-only: {old['pred_products']})")
    print(f"Matched: {counts['matched']}  "
          f"(exact={match_how.get('exact', 0)} canonical={match_how.get('canonical', 0)} "
          f"order={match_how.get('order', 0)})")
    print(f"Unmatched Gold: {counts['unmatched_gold']} (HTML-only: {old['unmatched_gold']})")
    print(f"Unmatched Pred: {counts['unmatched_pred']} (HTML-only: {old['unmatched_pred']})")

    print(f"\n{'字段':<12}{'P':>8}{'R':>8}{'F1':>8}"
          f"{'旧P':>8}{'旧R':>8}{'旧F1':>8}{'ΔF1':>8}")
    for en, cn, _ in FIELDS:
        s, o = stats[en], old_fields[cn]
        print(f"{cn:<12}{s['Precision']:>8.4f}{s['Recall']:>8.4f}{s['F1']:>8.4f}"
              f"{o['Precision']:>8.4f}{o['Recall']:>8.4f}{o['F1']:>8.4f}"
              f"{s['F1'] - o['F1']:>+8.4f}")

    print("\n--- 错误分类(新) ---")
    print(f"{'字段':<12}{'值错误':>7}{'格式差异':>9}{'AI漏值':>7}{'AI多值':>7}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        print(f"{cn:<12}{s['value_error']:>7}{s['format_diff']:>9}"
              f"{s['miss_in_ai']:>7}{s['extra_in_ai']:>7}")

    print("\n--- 附件效果专项 ---")
    print(f"HTML-only products=0 的公告: {n_zero_old}")
    print(f"  其中附件增强后获得产品的: {zero_fixed}")
    print(f"产品数增加的公告({len(increased)}): {increased}")
    print(f"产品数减少的公告({len(decreased)}): {decreased}")
    print(f"29个报价明细公告中产品数增加({len(price_increased)}): {price_increased}")
    print(f"CSV: {OUT_CSV}\nJSON: {OUT_JSON}")


if __name__ == "__main__":
    main()
