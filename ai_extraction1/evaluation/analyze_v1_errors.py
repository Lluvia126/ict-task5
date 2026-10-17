"""只读：V1 Gold Set 错误逐条分析。不修改任何数据、不调用 Qwen。

记录匹配采用 evaluate_goldset_v2 的改进逻辑（规范化+显式映射），
确保产品对应可靠后，再列出 3 个重点字段的不匹配案例。
"""
import json
from pathlib import Path

from ai_extraction.evaluation.evaluate_goldset import (
    is_empty, fields_match, FIELDS,
)
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    read_gold_full, read_predictions, match_products_v2,
)
from ai_extraction.evaluation.evaluate_goldset import V1_DIR


def show(v):
    if is_empty(v):
        return "(空)"
    return str(v)


def main():
    gold = read_gold_full()
    preds = read_predictions(V1_DIR)

    by_aid = {}
    for g in gold:
        by_aid.setdefault(g["announcement_id"], []).append(g)

    # 收集每个字段的错误案例
    errors = {en: [] for en, _, _ in FIELDS}
    extra_pred = {en: [] for en, _, _ in FIELDS}

    for aid, gprods in by_aid.items():
        pprods = preds.get(aid, [])
        matched = match_products_v2(aid, gprods, pprods)
        mg = {m[0] for m in matched}
        mp = {m[1] for m in matched}

        from ai_extraction.evaluation.evaluate_goldset_v2 import is_all_empty_gold

        for gi, pi, _how in matched:
            gp, pp = gprods[gi], pprods[pi]
            for en, _, isnum in FIELDS:
                gv, av = gp.get(en), pp.get(en)
                ge, ae = is_empty(gv), is_empty(av)
                if ge and ae:
                    continue
                if ge and not ae:
                    errors[en].append((aid, gv, av, "FP-Gold空而Pred有值"))
                elif not ge and ae:
                    errors[en].append((aid, gv, av, "FN-Gold有值而Pred空"))
                elif not fields_match(av, gv, isnum):
                    errors[en].append((aid, gv, av, "MISMATCH-都有值但不等"))

        # 未匹配 Gold（漏抽），跳过全空行
        for gi, gp in enumerate(gprods):
            if gi in mg or is_all_empty_gold(gp):
                continue
            for en, _, _ in FIELDS:
                if not is_empty(gp.get(en)):
                    errors[en].append((aid, gp.get(en), None, "FN-未匹配Gold"))

        # 未匹配 Pred（多抽）
        for pi, pp in enumerate(pprods):
            if pi in mp:
                continue
            for en, _, _ in FIELDS:
                if not is_empty(pp.get(en)):
                    extra_pred[en].append((aid, None, pp.get(en), "FP-未匹配Pred"))

    # 输出 3 个重点字段
    for en in ("product_service_name", "category", "spec_model"):
        cn = {e: c for e, c, _ in FIELDS}[en]
        allerr = errors[en] + extra_pred[en]
        print("=" * 84)
        print(f"字段【{cn}】错误案例：{len(allerr)} 条")
        print("=" * 84)
        for i, (aid, gv, av, typ) in enumerate(allerr, 1):
            print(f"\n[{i}] {aid}  | {typ}")
            print(f"    Gold: {show(gv)}")
            print(f"    V1  : {show(av)}")

    # 其他字段简要
    print("\n" + "=" * 84)
    print("品牌 / 单价 / 数量 / 总价 明显错误案例")
    print("=" * 84)
    for en, cn, _ in FIELDS:
        if en in ("product_service_name", "category", "spec_model"):
            continue
        allerr = errors[en] + extra_pred[en]
        print(f"\n--- {cn}：{len(allerr)} 条 ---")
        for aid, gv, av, typ in allerr:
            print(f"  {aid} | {typ} | Gold={show(gv)} V1={show(av)}")


if __name__ == "__main__":
    main()
