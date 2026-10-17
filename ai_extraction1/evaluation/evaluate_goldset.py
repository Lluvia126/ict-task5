"""Gold Set 评估工具：计算 V1/V2 与人工 Gold 的字段级 P/R/F1。

不调用 Qwen，不修改 Gold 文件，不修改业务代码。
"""
import json
import csv
import re
from pathlib import Path
from openpyxl import load_workbook

GOLD_XLSX = Path(__file__).parent.parent / "data" / "gold" / "goldset_manual_check.xlsx"
V1_DIR = Path(__file__).parent.parent / "data" / "baseline_output"
V2_DIR = Path(__file__).parent.parent / "data" / "baseline_output_v2"
RESULT_CSV = Path(__file__).parent.parent / "data" / "gold" / "evaluation_result.csv"
RESULT_JSON = Path(__file__).parent.parent / "data" / "gold" / "evaluation_result.json"

FIELDS = [
    ("product_service_name", "产品/服务名称", False),
    ("category", "品目", False),
    ("brand", "品牌", False),
    ("spec_model", "规格型号", False),
    ("unit_price", "单价", True),
    ("quantity", "数量", True),
    ("total_price", "总价", True),
]

GOLD_COL_START = 9


def normalize_text(s):
    if s is None:
        return ""
    s = str(s).strip()
    if not s or s.lower() in ("none", "null"):
        return ""
    s = re.sub(r"\s+", " ", s)
    s = s.translate(str.maketrans("（），：；！　", "(),:;! "))
    return s.strip()


def normalize_number(s):
    if s is None:
        return ""
    s = str(s).strip()
    if not s or s.lower() in ("none", "null"):
        return ""
    s = re.sub(r"[万元台个件套批次项立方米千米/]", "", s)
    s = s.strip()
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return normalize_text(s)


def is_empty(v):
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.lower() in ("none", "null")


def normalize_field(v, is_numeric):
    if is_empty(v):
        return ""
    return normalize_number(v) if is_numeric else normalize_text(v)


def fields_match(ai_val, gold_val, is_numeric):
    ai_n = normalize_field(ai_val, is_numeric)
    gold_n = normalize_field(gold_val, is_numeric)
    if not ai_n or not gold_n:
        return False
    return ai_n == gold_n


def read_gold():
    wb = load_workbook(GOLD_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    data = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        record = {"announcement_id": str(row[0]).strip()}
        for i, (en, cn, _) in enumerate(FIELDS):
            record[f"{en}_Gold"] = row[GOLD_COL_START + i]
        data.append(record)
    return data


def read_predictions(pred_dir):
    results = {}
    for jf in sorted(pred_dir.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        products = r.get("result", {}).get("products", []) if r.get("result") else []
        results[aid] = products
    return results


def match_products(gold_products, pred_products):
    matched = []
    used_pred = set()

    # Pass 1: name match
    for gi, gp in enumerate(gold_products):
        gname = normalize_text(gp.get("product_service_name_Gold"))
        if not gname:
            continue
        for pi, pp in enumerate(pred_products):
            if pi in used_pred:
                continue
            if gname == normalize_text(pp.get("product_service_name")):
                matched.append((gi, pi))
                used_pred.add(pi)
                break

    # Pass 2: sequential order
    matched_gold = {m[0] for m in matched}
    rem_gold = [i for i in range(len(gold_products)) if i not in matched_gold]
    rem_pred = [i for i in range(len(pred_products)) if i not in used_pred]
    for gi, pi in zip(rem_gold, rem_pred):
        matched.append((gi, pi))
        used_pred.add(pi)

    all_g = {m[0] for m in matched}
    all_p = {m[1] for m in matched}
    return matched, [i for i in range(len(gold_products)) if i not in all_g], [i for i in range(len(pred_products)) if i not in all_p]


def evaluate_version(gold_data, pred_data):
    gold_by_aid = {}
    for g in gold_data:
        gold_by_aid.setdefault(g["announcement_id"], []).append(g)

    stats = {en: {"TP": 0, "FP": 0, "FN": 0, "gold_valid": 0} for en, _, _ in FIELDS}
    total_matched = total_um_gold = total_um_pred = total_prod = 0

    for aid, gold_products in gold_by_aid.items():
        pred_products = pred_data.get(aid, [])
        total_prod += len(pred_products)

        if not pred_products:
            for gp in gold_products:
                for en, _, _ in FIELDS:
                    if not is_empty(gp.get(f"{en}_Gold")):
                        stats[en]["FN"] += 1
                        stats[en]["gold_valid"] += 1
            total_um_gold += len(gold_products)
            continue

        matched, um_gold, um_pred = match_products(gold_products, pred_products)
        total_matched += len(matched)
        total_um_gold += len(um_gold)
        total_um_pred += len(um_pred)

        for gi, pi in matched:
            gp = gold_products[gi]
            pp = pred_products[pi]
            for en, _, is_num in FIELDS:
                gold_val = gp.get(f"{en}_Gold")
                ai_val = pp.get(en)
                g_empty = is_empty(gold_val)
                a_empty = is_empty(ai_val)
                if g_empty and a_empty:
                    continue
                if not g_empty:
                    stats[en]["gold_valid"] += 1
                if not g_empty and not a_empty:
                    if fields_match(ai_val, gold_val, is_num):
                        stats[en]["TP"] += 1
                    else:
                        stats[en]["FP"] += 1
                        stats[en]["FN"] += 1
                elif not g_empty and a_empty:
                    stats[en]["FN"] += 1
                elif g_empty and not a_empty:
                    stats[en]["FP"] += 1

        for gi in um_gold:
            gp = gold_products[gi]
            for en, _, _ in FIELDS:
                if not is_empty(gp.get(f"{en}_Gold")):
                    stats[en]["FN"] += 1
                    stats[en]["gold_valid"] += 1

        for pi in um_pred:
            pp = pred_products[pi]
            for en, _, _ in FIELDS:
                if not is_empty(pp.get(en)):
                    stats[en]["FP"] += 1

    for en, _, _ in FIELDS:
        s = stats[en]
        tp, fp, fn = s["TP"], s["FP"], s["FN"]
        s["Precision"] = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
        s["Recall"] = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
        pr = s["Precision"] + s["Recall"]
        s["F1"] = round(2 * s["Precision"] * s["Recall"] / pr, 4) if pr > 0 else 0.0

    return stats, total_matched, total_um_gold, total_um_pred, total_prod


def main():
    print("===== Gold Set 评估 =====")

    gold_data = read_gold()
    gold_filled = sum(1 for g in gold_data for en, _, _ in FIELDS if not is_empty(g.get(f"{en}_Gold")))
    print(f"Gold 记录: {len(gold_data)} | 非空字段: {gold_filled} / {len(gold_data) * 7}")

    v1_preds = read_predictions(V1_DIR)
    v2_preds = read_predictions(V2_DIR)
    v1_total = sum(len(v) for v in v1_preds.values())
    v2_total = sum(len(v) for v in v2_preds.values())
    print(f"V1 products: {v1_total} | V2 products: {v2_total}")

    v1_stats, v1_m, v1_ug, v1_up, v1_tp = evaluate_version(gold_data, v1_preds)
    v2_stats, v2_m, v2_ug, v2_up, v2_tp = evaluate_version(gold_data, v2_preds)

    # CSV
    csv_rows = [["版本", "字段", "TP", "FP", "FN", "Precision", "Recall", "F1", "Gold有效数量"]]
    for ver, stats in [("V1", v1_stats), ("V2", v2_stats)]:
        for en, cn, _ in FIELDS:
            s = stats[en]
            csv_rows.append([ver, cn, s["TP"], s["FP"], s["FN"], s["Precision"], s["Recall"], s["F1"], s["gold_valid"]])

    with open(RESULT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(csv_rows)

    # JSON
    result = {
        "gold_total_records": len(gold_data),
        "gold_filled_fields": gold_filled,
        "v1": {"products": v1_tp, "matched": v1_m, "unmatched_gold": v1_ug, "unmatched_pred": v1_up,
               "fields": {cn: v1_stats[en] for en, cn, _ in FIELDS}},
        "v2": {"products": v2_tp, "matched": v2_m, "unmatched_gold": v2_ug, "unmatched_pred": v2_up,
               "fields": {cn: v2_stats[en] for en, cn, _ in FIELDS}},
    }
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Report
    print("\n" + "=" * 70)
    print(f"{'字段':<12} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>8} {'R':>8} {'F1':>8} {'Gold有效':>8}")
    print("-" * 70)
    for ver, stats in [("V1", v1_stats), ("V2", v2_stats)]:
        print(f"--- {ver} ---")
        for en, cn, _ in FIELDS:
            s = stats[en]
            print(f"{cn:<12} {s['TP']:>4} {s['FP']:>4} {s['FN']:>4} {s['Precision']:>8.4f} {s['Recall']:>8.4f} {s['F1']:>8.4f} {s['gold_valid']:>8}")

    print("\n" + "=" * 70)
    print("V1 → V2 变化")
    print(f"{'字段':<12} {'V1 F1':>8} {'V2 F1':>8} {'变化':>8} {'趋势':>6}")
    print("-" * 70)
    improved, declined, stable = [], [], []
    for en, cn, _ in FIELDS:
        v1f, v2f = v1_stats[en]["F1"], v2_stats[en]["F1"]
        diff = round(v2f - v1f, 4)
        if diff > 0.01:
            improved.append(cn)
            trend = "↑"
        elif diff < -0.01:
            declined.append(cn)
            trend = "↓"
        else:
            stable.append(cn)
            trend = "→"
        print(f"{cn:<12} {v1f:>8.4f} {v2f:>8.4f} {diff:>+8.4f} {trend}")

    v1_f1s = [v1_stats[en]["F1"] for en, _, _ in FIELDS]
    v2_f1s = [v2_stats[en]["F1"] for en, _, _ in FIELDS]
    v1_macro = round(sum(v1_f1s) / len(v1_f1s), 4)
    v2_macro = round(sum(v2_f1s) / len(v2_f1s), 4)

    print("\n" + "=" * 70)
    print("汇总")
    print(f"  Gold: {len(gold_data)} 条, 非空 {gold_filled}/{len(gold_data)*7}")
    print(f"  V1: products={v1_tp}, matched={v1_m}, um_gold={v1_ug}, um_pred={v1_up}")
    print(f"  V2: products={v2_tp}, matched={v2_m}, um_gold={v2_ug}, um_pred={v2_up}")
    print(f"  V1 Macro-F1: {v1_macro}")
    print(f"  V2 Macro-F1: {v2_macro}")
    print(f"  (Gold Set 综合统计，非官方比赛最终得分)")
    print(f"  CSV: {RESULT_CSV}")
    print(f"  JSON: {RESULT_JSON}")


if __name__ == "__main__":
    main()
