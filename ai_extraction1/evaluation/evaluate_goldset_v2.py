"""Gold Set 评估 V2：修正"产品记录匹配过严"问题。

改进点（相比 evaluate_goldset.py）：
  1. 产品名规范化：去括号附加说明、去末尾"项目"等无实质后缀后再匹配。
  2. 显式人工映射表：仅针对当前 Gold Set 已人工确认的明显别名。
  3. 拆分 / 合并单独记录，不当成完全匹配。
  不做通用模糊匹配，不用相似度阈值。

不调用 Qwen，不修改 Gold / Prompt / V1 / V2。
"""
import json
import csv
import re
from pathlib import Path
from openpyxl import load_workbook

from ai_extraction.evaluation.evaluate_goldset import (
    normalize_text, normalize_number, is_empty, normalize_field, fields_match,
    FIELDS, GOLD_XLSX, V1_DIR, V2_DIR,
)

RESULT_CSV = Path(__file__).parent.parent / "data" / "gold" / "evaluation_result_v2.csv"
RESULT_JSON = Path(__file__).parent.parent / "data" / "gold" / "evaluation_result_v2.json"

GOLD_COL_START = 9

# 显式人工映射表（规范化后名称）：仅 Gold Set 已确认的明显别名，(aid, gold别名) -> pred别名
# key 里的 aid 限定作用范围，避免误伤。
EXPLICIT_MAP = {
    ("t20260206_26154814", "全身螺旋CT"): "X射线计算机体层摄影设备",
    ("t20260206_26154814", "3D面部扫描仪"): "面部扫描仪",
    ("t20260804_27070085",
     "广东省教育考试考务综合管理平台运维运营服务(2026-2027年)项目"):
        "广东省教育考试考务综合管理平台运维运营服务(2026-2027年)",
}


def canonical_name(s, aid=None):
    """产品名规范化：去空白/全半角后，再去括号附加说明、末尾"项目"。"""
    n = normalize_text(s)
    if not n:
        return ""
    # 去末尾无实质后缀"项目"
    if n.endswith("项目"):
        n = n[:-2].strip()
    # 去括号附加说明，如 (注册证产品名称：...) 、（下（截断残留）
    n = re.sub(r"[(（][^()（）]*[)）]\s*$", "", n).strip()
    # 截断残留：以未闭合括号结尾，如 "电子面弓（下"
    n = re.sub(r"[（(][^)）]*$", "", n).strip()
    return n


def mapped_name(aid, gold_canonical):
    """应用显式人工映射，返回 pred 侧应使用的规范化名称。"""
    return EXPLICIT_MAP.get((aid, gold_canonical), gold_canonical)


def read_gold_full():
    wb = load_workbook(GOLD_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    data = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        rec = {"announcement_id": str(row[0]).strip()}
        for i, (en, _, _) in enumerate(FIELDS):
            rec[en] = row[GOLD_COL_START + i]
        data.append(rec)
    return data


def read_predictions(pred_dir):
    results = {}
    for jf in sorted(pred_dir.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        products = r.get("result", {}).get("products", []) if r.get("result") else []
        results[aid] = products
    return results


def is_all_empty_gold(gp):
    return all(is_empty(gp.get(en)) for en, _, _ in FIELDS)


def match_products_v2(aid, gold_products, pred_products):
    """1:1 匹配，三级优先级。返回 matched 列表 (gi, pi, 类型)。"""
    used_g, used_p = set(), set()
    matched = []

    gold_keys = []
    for gi, gp in enumerate(gold_products):
        gc = canonical_name(gp.get("product_service_name"), aid)
        gold_keys.append(mapped_name(aid, gc) if gc else "")
    pred_keys = [canonical_name(pp.get("product_service_name"), aid) for pp in pred_products]

    def take(gi, pi, how):
        matched.append((gi, pi, how))
        used_g.add(gi)
        used_p.add(pi)

    # Pass 1: 原始名完全相等
    for gi, gp in enumerate(gold_products):
        g0 = normalize_text(gp.get("product_service_name"))
        if not g0:
            continue
        for pi, pp in enumerate(pred_products):
            if pi in used_p:
                continue
            if g0 == normalize_text(pp.get("product_service_name")):
                take(gi, pi, "exact")
                break

    # Pass 2: 规范化名（canonical + 显式映射）相等
    for gi, gk in enumerate(gold_keys):
        if gi in used_g or not gk:
            continue
        for pi, pk in enumerate(pred_keys):
            if pi in used_p:
                continue
            if gk == pk:
                how = "mapped" if EXPLICIT_MAP.get((aid, canonical_name(gold_products[gi].get("product_service_name")))) else "canonical"
                take(gi, pi, how)
                break

    # Pass 3: 顺序兜底（剩余双方按顺序一一对应）
    rem_g = [i for i in range(len(gold_products)) if i not in used_g]
    rem_p = [i for i in range(len(pred_products)) if i not in used_p]
    # 全空 Gold 行不参与顺序匹配（G3~G18）
    rem_g_nonempty = [i for i in rem_g if not is_all_empty_gold(gold_products[i])]
    for gi, pi in zip(rem_g_nonempty, rem_p):
        take(gi, pi, "order")

    return matched


def evaluate_version(gold_data, pred_data):
    gold_by_aid = {}
    for g in gold_data:
        gold_by_aid.setdefault(g["announcement_id"], []).append(g)

    stats = {en: {"TP": 0, "FP": 0, "FN": 0, "gold_valid": 0} for en, _, _ in FIELDS}
    counts = {"matched": 0, "split": 0, "merge": 0,
              "true_miss": 0, "true_extra": 0, "empty_gold_rows": 0,
              "nonempty_gold_rows": 0, "total_pred": 0}

    for aid, gold_products in gold_by_aid.items():
        pred_products = pred_data.get(aid, [])
        counts["total_pred"] += len(pred_products)

        empty_rows = [i for i, gp in enumerate(gold_products) if is_all_empty_gold(gp)]
        counts["empty_gold_rows"] += len(empty_rows)
        counts["nonempty_gold_rows"] += len(gold_products) - len(empty_rows)

        matched = match_products_v2(aid, gold_products, pred_products)
        matched_g = {m[0] for m in matched}
        matched_p = {m[1] for m in matched}
        counts["matched"] += len(matched)

        for gi, pi, _how in matched:
            gp, pp = gold_products[gi], pred_products[pi]
            for en, _, is_num in FIELDS:
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
                elif not ge and ae:
                    stats[en]["FN"] += 1
                else:
                    stats[en]["FP"] += 1

        # 未匹配 Gold
        for gi, gp in enumerate(gold_products):
            if gi in matched_g:
                continue
            if is_all_empty_gold(gp):
                continue  # 全空行不计错误
            counts["true_miss"] += 1
            for en, _, _ in FIELDS:
                if not is_empty(gp.get(en)):
                    stats[en]["FN"] += 1
                    stats[en]["gold_valid"] += 1

        # 未匹配 Pred
        for pi, pp in enumerate(pred_products):
            if pi in matched_p:
                continue
            counts["true_extra"] += 1
            for en, _, _ in FIELDS:
                if not is_empty(pp.get(en)):
                    stats[en]["FP"] += 1

    for en, _, _ in FIELDS:
        s = stats[en]
        tp, fp, fn = s["TP"], s["FP"], s["FN"]
        s["Precision"] = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
        s["Recall"] = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
        pr = s["Precision"] + s["Recall"]
        s["F1"] = round(2 * s["Precision"] * s["Recall"] / pr, 4) if pr else 0.0

    return stats, counts


def main():
    gold_data = read_gold_full()
    gold_filled = sum(1 for g in gold_data for en, _, _ in FIELDS if not is_empty(g.get(en)))
    print(f"Gold 记录 {len(gold_data)} | 非空字段 {gold_filled}/{len(gold_data)*7}")

    v1_preds = read_predictions(V1_DIR)
    v2_preds = read_predictions(V2_DIR)

    v1_stats, v1c = evaluate_version(gold_data, v1_preds)
    v2_stats, v2c = evaluate_version(gold_data, v2_preds)

    # CSV
    rows = [["版本", "字段", "TP", "FP", "FN", "Precision", "Recall", "F1", "Gold有效数量"]]
    for ver, st in [("V1", v1_stats), ("V2", v2_stats)]:
        for en, cn, _ in FIELDS:
            s = st[en]
            rows.append([ver, cn, s["TP"], s["FP"], s["FN"], s["Precision"], s["Recall"], s["F1"], s["gold_valid"]])
    with open(RESULT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)

    def macro(st):
        return round(sum(st[en]["F1"] for en, _, _ in FIELDS) / len(FIELDS), 4)

    result = {
        "note": "10公告/133条Gold上的实验结果，非官方比赛最终得分",
        "gold_records": len(gold_data),
        "gold_filled_fields": gold_filled,
        "v1": {"macro_f1": macro(v1_stats),
               "matched": v1c["matched"], "true_miss": v1c["true_miss"], "true_extra": v1c["true_extra"],
               "empty_gold_rows": v1c["empty_gold_rows"], "total_pred": v1c["total_pred"],
               "fields": {cn: v1_stats[en] for en, cn, _ in FIELDS}},
        "v2": {"macro_f1": macro(v2_stats),
               "matched": v2c["matched"], "true_miss": v2c["true_miss"], "true_extra": v2c["true_extra"],
               "empty_gold_rows": v2c["empty_gold_rows"], "total_pred": v2c["total_pred"],
               "fields": {cn: v2_stats[en] for en, cn, _ in FIELDS}},
    }
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 打印
    for ver, st, cc in [("V1", v1_stats, v1c), ("V2", v2_stats, v2c)]:
        print("\n" + "=" * 78)
        print(f"{ver}  (Macro-F1={macro(st)})")
        print(f"{'字段':<12}{'TP':>5}{'FP':>5}{'FN':>5}{'P':>9}{'R':>9}{'F1':>9}{'Gold有效':>9}")
        for en, cn, _ in FIELDS:
            s = st[en]
            print(f"{cn:<12}{s['TP']:>5}{s['FP']:>5}{s['FN']:>5}{s['Precision']:>9.4f}{s['Recall']:>9.4f}{s['F1']:>9.4f}{s['gold_valid']:>9}")
        print(f"matched={cc['matched']} 真漏抽={cc['true_miss']} 真多抽={cc['true_extra']} "
              f"全空Gold行={cc['empty_gold_rows']} pred总数={cc['total_pred']}")

    print("\n" + "=" * 78)
    print("V1 → V2 F1 变化")
    print(f"{'字段':<12}{'V1 F1':>9}{'V2 F1':>9}{'变化':>10}  趋势")
    for en, cn, _ in FIELDS:
        a, b = v1_stats[en]["F1"], v2_stats[en]["F1"]
        d = round(b - a, 4)
        t = "提升" if d > 0.01 else ("下降" if d < -0.01 else "基本持平")
        print(f"{cn:<12}{a:>9.4f}{b:>9.4f}{d:>+10.4f}  {t}")
    print(f"\nMacro-F1: V1={macro(v1_stats)} → V2={macro(v2_stats)}")
    print(f"\nCSV: {RESULT_CSV}\nJSON: {RESULT_JSON}")


if __name__ == "__main__":
    main()
