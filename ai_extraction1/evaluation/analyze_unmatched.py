"""只读分析脚本：列出 V2 未匹配的 Gold / Pred 记录详情。

不修改任何数据、不调用 Qwen。复用 evaluate_goldset 的匹配逻辑。
"""
import json
from pathlib import Path

from ai_extraction.evaluation.evaluate_goldset import (
    read_gold, read_predictions, match_products, FIELDS,
    GOLD_XLSX, V2_DIR,
)

# Gold Excel 列位置：announcement_id=0, source_file=1, AI列 2-8, Gold列 9-15
GOLD_NAME_IDX = {en: 9 + i for i, (en, _, _) in enumerate(FIELDS)}


def read_gold_full():
    """读取 Gold Excel，返回带完整字段的记录。"""
    from openpyxl import load_workbook
    wb = load_workbook(GOLD_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    data = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        rec = {"announcement_id": str(row[0]).strip()}
        for en, idx in GOLD_NAME_IDX.items():
            rec[en] = row[idx]
        data.append(rec)
    return data


def fmt(v):
    if v is None or str(v).strip() == "":
        return "(空)"
    return str(v)


def main():
    gold_data = read_gold_full()
    gold_by_aid = {}
    for g in gold_data:
        gold_by_aid.setdefault(g["announcement_id"], []).append(g)

    v2_preds = read_predictions(V2_DIR)

    all_um_gold = []
    all_um_pred = []

    for aid, gold_products in gold_by_aid.items():
        pred_products = v2_preds.get(aid, [])
        matched, um_gold, um_pred = match_products(gold_products, pred_products)
        for gi in um_gold:
            all_um_gold.append((aid, gold_products[gi]))
        for pi in um_pred:
            all_um_pred.append((aid, pred_products[pi]))

    print("=" * 72)
    print(f"A. V2 未匹配的 Gold 记录（{len(all_um_gold)} 条）")
    print("=" * 72)
    for i, (aid, g) in enumerate(all_um_gold, 1):
        print(f"\n[G{i}] announcement_id: {aid}")
        print(f"      产品/服务名称: {fmt(g['product_service_name'])}")
        print(f"      品牌: {fmt(g['brand'])}")
        print(f"      规格型号: {fmt(g['spec_model'])}")
        print(f"      单价: {fmt(g['unit_price'])}  数量: {fmt(g['quantity'])}  总价: {fmt(g['total_price'])}")

    print("\n" + "=" * 72)
    print(f"B. V2 未匹配的 Pred 记录（{len(all_um_pred)} 条）")
    print("=" * 72)
    for i, (aid, p) in enumerate(all_um_pred, 1):
        print(f"\n[P{i}] announcement_id: {aid}")
        print(f"      产品/服务名称: {fmt(p.get('product_service_name'))}")
        print(f"      品牌: {fmt(p.get('brand'))}")
        print(f"      规格型号: {fmt(p.get('spec_model'))}")
        print(f"      单价: {fmt(p.get('unit_price'))}  数量: {fmt(p.get('quantity'))}  总价: {fmt(p.get('total_price'))}")

    print("\n" + "=" * 72)
    print("C. 同一 announcement_id 内的未匹配配对（供人工判断）")
    print("=" * 72)
    # 按 aid 分组展示，方便判断"可对应/拆分/合并"
    from collections import defaultdict
    g_by_aid = defaultdict(list)
    p_by_aid = defaultdict(list)
    for i, (aid, g) in enumerate(all_um_gold, 1):
        g_by_aid[aid].append(f"G{i}")
    for i, (aid, p) in enumerate(all_um_pred, 1):
        p_by_aid[aid].append(f"P{i}")
    all_aids = sorted(set(g_by_aid) | set(p_by_aid))
    for aid in all_aids:
        print(f"  {aid}: Gold未匹配={','.join(g_by_aid.get(aid, [])) or '无'} | Pred未匹配={','.join(p_by_aid.get(aid, [])) or '无'}")


if __name__ == "__main__":
    main()
