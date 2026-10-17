"""V4.1 结果离线去重后处理（不调用 Qwen、不修改原始 V4.1 结果、不修改评估脚本）。

背景：
  V4.1 50 条全部走 legacy 模式，legacy_merge 只做「全 7 字段元组完全相等」去重，
  跨 chunk 同一实体只要任一字段有差异（部分为空/单价vs总价）即幸存为近重复。

输入：data/validation_attachment_test/run_v4_1_prompt_v22/t*.json（保持不变）
输出：data/validation_attachment_test/run_v4_1_dedup/
  - t*.json                  完整记录副本，仅 result.products 经过去重
  - dedupe_summary.json      去重前后统计 + 每文档明细
  - evaluation_v4_1_dedup_result.json  (--evaluate 时生成)

两层去重：
  Layer1 exact  : 规范化后全 7 字段指纹完全相同 → 保留第一条
  Layer2 similar: product_name 规范化后完全相等（容忍空白/全半角差异，不用模糊阈值，
                  防止 海温/海浪、第一包/第二包 这类长名称差 1 字的误合并）
                  且 brand/spec_model/unit_price/quantity/total_price
                  每字段均「相同或都为空」→ 合并，保留字段最完整的一条
  不做字段互补，不根据 Gold 定制任何规则。

用法：
  python -m ai_extraction.dedupe_v41            # 仅去重
  python -m ai_extraction.dedupe_v41 --evaluate # 去重 + 双跑评估对比
"""
import copy
import json
import re
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).parent
SRC_DIR = BASE / "data" / "validation_attachment_test" / "run_v4_1_prompt_v22"
OUT_DIR = BASE / "data" / "validation_attachment_test" / "run_v4_1_dedup"

FIELDS7 = ("product_service_name", "category", "brand", "spec_model",
           "unit_price", "quantity", "total_price")
NUM_FIELDS = ("unit_price", "quantity", "total_price")

_WS = re.compile(r"\s+")


# ================================================================ 规范化（仅用于指纹，不改存储值）

def norm_text(s):
    """首尾去空格、连续空白归一、全角→半角（NFKC 含中英文括号统一）、小写。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _WS.sub(" ", s).strip()
    if s.lower() in ("none", "null"):
        return ""
    return s.lower()


def norm_num(v):
    """安全数字规范化：去千分位/空白/末尾“元”，可解析则归一为数值字符串。"""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    s = unicodedata.normalize("NFKC", str(v))
    s = _WS.sub("", s).replace(",", "").replace("，", "")
    s = re.sub(r"元$", "", s)
    if not s or s.lower() in ("none", "null"):
        return ""
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return s.lower()


def norm_field(k, v):
    return norm_num(v) if k in NUM_FIELDS else norm_text(v)


def fingerprint(p):
    return tuple(norm_field(k, p.get(k)) for k in FIELDS7)


# ================================================================ 两层去重

LAYER2_FIELDS = ("brand", "spec_model", "unit_price", "quantity", "total_price")


def _field_compatible(pa, pb, category_guard=False):
    """Layer2 逐字段字面语义：「相同或都为空」。

    对 brand/spec_model/unit_price/quantity/total_price：
      规范化值相等，或两侧都为空；一侧有值一侧为空 / 两侧值不同 → 不兼容。
    category_guard=True 时追加 category 冲突拦截：
      两侧 category 均非空且不同（如 3标段/4标段）→ 不兼容；
      一侧为空不拦截（常见互补情形）。
    """
    for k in LAYER2_FIELDS:
        a, b = norm_field(k, pa.get(k)), norm_field(k, pb.get(k))
        if a != b and (a or b):
            return False
    if category_guard:
        ca, cb = norm_text(pa.get("category")), norm_text(pb.get("category"))
        if ca and cb and ca != cb:
            return False
    return True


def _name_similar(na, nb):
    """名称判定：规范化后完全相等（含去除全部空白）。

    不用模糊阈值：长名称差 1 字（海温/海浪、第一包/第二包）即为不同实体。
    """
    if not na or not nb:
        return False
    return na == nb or na.replace(" ", "") == nb.replace(" ", "")


def _completeness(p):
    return sum(1 for k in FIELDS7 if norm_field(k, p.get(k)))


def dedupe_products(products, category_guard=False):
    """返回 (kept, removed)。removed 项: {index, product, duplicate_type, kept_index}"""
    # ---- Layer 1: exact（规范化全字段指纹完全相同，保留第一条）
    seen = {}
    kept, removed = [], []
    for i, p in enumerate(products):
        fp = fingerprint(p)
        if fp in seen:
            removed.append({"index": i, "product": p,
                            "duplicate_type": "exact",
                            "kept_index": seen[fp],
                            "kept_product": kept[seen[fp]]})
        else:
            seen[fp] = len(kept)
            kept.append(p)

    # ---- Layer 2: similar（名称高度相似 + 5 字段「相同或都为空」）
    # 代表元聚类：按完整度降序处理，每条只与「簇代表」比较，
    # 不用并查集，杜绝经全空/部分空枢纽记录的链式传递合并。
    order = sorted(range(len(kept)),
                   key=lambda i: (-_completeness(kept[i]), i))
    clusters = []  # [rep_index, member_indices]
    for i in order:
        ni = norm_text(kept[i].get("product_service_name"))
        placed = False
        for rep, members in clusters:
            if _name_similar(ni, norm_text(kept[rep].get("product_service_name"))) \
                    and _field_compatible(kept[i], kept[rep],
                                          category_guard=category_guard):
                members.append(i)
                placed = True
                break
        if not placed:
            clusters.append((i, [i]))

    final, sim_removed = [], []
    for rep, members in clusters:
        final.append(rep)
        for m in members:
            if m != rep:
                sim_removed.append({"index": m, "product": kept[m],
                                    "duplicate_type": "similar",
                                    "kept_index": rep,
                                    "kept_product": kept[rep]})

    # 保持稳定：按原列表相对顺序输出
    final.sort()
    kept_products = [kept[i] for i in final]
    return kept_products, removed + sim_removed


# ================================================================ 跨 chunk 来源诊断（仅报告用）

def chunk_name_overlap(record, removed):
    """对被删的同名簇，判断其名称是否出现在 ≥2 个 chunk 的原始响应中（跨 chunk 证据）。"""
    raws = record.get("raw_responses") or []
    chunk_names = []
    for r in raws:
        ps = (r or {}).get("products", []) or []
        chunk_names.append({norm_text(p.get("product_service_name")) for p in ps})
    out = {}
    for rm in removed:
        nm = norm_text(rm["product"].get("product_service_name"))
        if not nm:
            continue
        hits = sum(1 for s in chunk_names if nm in s)
        out.setdefault(nm, hits)
    return out


# ================================================================ 主流程

REVIEWED_DIR = BASE / "data" / "validation_attachment_test" / "run_v4_1_dedup_reviewed"


def do_dedupe(reviewed=False):
    """reviewed=True 时启用 category 冲突拦截（复核后规则），输出到 run_v4_1_dedup_reviewed/"""
    out_dir = REVIEWED_DIR if reviewed else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(SRC_DIR.glob("t*.json"))
    per_doc = []
    tot_before = tot_exact = tot_similar = tot_after = 0

    for jf in files:
        rec = json.loads(jf.read_text(encoding="utf-8"))
        aid = rec.get("announcement_id", jf.stem)
        products = ((rec.get("result") or {}).get("products") or [])
        kept, removed = dedupe_products(products, category_guard=reviewed)

        n_exact = sum(1 for r in removed if r["duplicate_type"] == "exact")
        n_similar = len(removed) - n_exact
        before, after = len(products), len(kept)
        tot_before += before
        tot_exact += n_exact
        tot_similar += n_similar
        tot_after += after

        new_rec = copy.deepcopy(rec)
        if new_rec.get("result") is not None:
            new_rec["result"]["products"] = kept
        new_rec["products_count"] = after
        new_rec["dedupe_info"] = {
            "before": before, "after": after,
            "exact_removed": n_exact, "similar_removed": n_similar,
        }
        (out_dir / jf.name).write_text(
            json.dumps(new_rec, ensure_ascii=False, indent=1), encoding="utf-8")

        per_doc.append({
            "sample_id": aid,
            "mode": rec.get("mode"),
            "chunks": len(rec.get("raw_responses") or []),
            "before": before,
            "exact_removed": n_exact,
            "similar_removed": n_similar,
            "after": after,
            "removed_items": [
                {"duplicate_type": r["duplicate_type"],
                 "original_index": r["index"],
                 "product": r["product"],
                 "kept_product": r.get("kept_product")} for r in removed],
            "remaining_items": kept,
            "cross_chunk_name_hits": chunk_name_overlap(rec, removed),
        })

    summary = {
        "source_dir": str(SRC_DIR),
        "output_dir": str(out_dir),
        "category_guard": reviewed,
        "rules": {
            "exact": "规范化(去空白/NFKC全半角统一/数字安全规范化)后全7字段指纹完全相同，保留第一条",
            "similar": "product_name 规范化后完全相等（容忍空白/全半角差异，不用模糊阈值）；"
                       "brand/spec_model/unit_price/quantity/total_price 每字段均「相同或都为空」；"
                       + ("category 两侧均非空且不同（如不同标段）→ 禁止合并；" if reviewed else "")
                       + "保留字段最完整一条，不做字段互补",
        },
        "documents": len(files),
        "products_before": tot_before,
        "exact_removed": tot_exact,
        "similar_removed": tot_similar,
        "products_after": tot_after,
        "per_document": per_doc,
    }
    (out_dir / "dedupe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"===== V4.1 去重汇总{'（reviewed: category 冲突拦截）' if reviewed else ''} =====")
    print(f"文档数: {len(files)}")
    print(f"原始 products: {tot_before}")
    print(f"删除 exact duplicate: {tot_exact}")
    print(f"删除 similar duplicate: {tot_similar}")
    print(f"去重后 products: {tot_after}")
    changed = [d for d in per_doc if d["before"] != d["after"]]
    print(f"发生去重的文档({len(changed)}):")
    for d in changed:
        print(f"  {d['sample_id']}: {d['before']} -> {d['after']} "
              f"(exact={d['exact_removed']}, similar={d['similar_removed']})")
    print(f"输出: {out_dir}")


# ================================================================ 逐条复核（生成 dedupe_review.csv）

def _eq(pa, pb, k):
    """字段比对结果: EQUAL / BOTH_EMPTY / DIFFER / ONE_EMPTY"""
    a, b = norm_field(k, pa.get(k)), norm_field(k, pb.get(k))
    if not a and not b:
        return "BOTH_EMPTY"
    if a == b:
        return "EQUAL"
    if not a or not b:
        return "ONE_EMPTY"
    return "DIFFER"


def do_review():
    """对 run_v4_1_dedup 的全部 removed 记录逐条复核，输出 dedupe_review.csv"""
    import csv
    summary = json.loads((OUT_DIR / "dedupe_summary.json").read_text(encoding="utf-8"))
    rows = []
    for d in summary["per_document"]:
        for rm in d["removed_items"]:
            p, kp = rm["product"], rm.get("kept_product") or {}
            name_a = norm_text(p.get("product_service_name")).replace(" ", "")
            name_b = norm_text(kp.get("product_service_name")).replace(" ", "")
            name_sim = "EQUAL" if name_a == name_b else "SIMILAR"
            field_eq = {k: _eq(p, kp, k) for k in
                        ("brand", "spec_model", "unit_price", "quantity", "total_price")}
            cat_eq = _eq(p, kp, "category")

            # 风险判定
            if cat_eq == "DIFFER":
                risk = "HIGH"
                reason = (f"category 冲突（{p.get('category')} vs {kp.get('category')}），"
                          "疑似不同标段/采购上下文，不应自动合并")
            elif name_sim != "EQUAL":
                risk = "HIGH"
                reason = "名称不完全相同即被合并"
            elif any(v == "DIFFER" for v in field_eq.values()):
                risk = "HIGH"
                reason = "存在字段值冲突仍被合并"
            elif any(v == "ONE_EMPTY" for v in field_eq.values()):
                risk = "MEDIUM"
                reason = "一侧字段缺失的互补合并，建议人工确认"
            else:
                risk = "LOW"
                reason = "同名同规格同价同量，跨 chunk 重复抽取，合并安全"

            rows.append({
                "sample_id": d["sample_id"],
                "duplicate_type": rm["duplicate_type"],
                "product_name_similarity": name_sim,
                "brand_equal": field_eq["brand"],
                "spec_equal": field_eq["spec_model"],
                "unit_price_equal": field_eq["unit_price"],
                "quantity_equal": field_eq["quantity"],
                "total_price_equal": field_eq["total_price"],
                "category_equal": cat_eq,
                "risk_level": risk,
                "reason": reason,
                "removed_item": json.dumps(p, ensure_ascii=False),
                "kept_item": json.dumps(kp, ensure_ascii=False),
            })

    csv_path = OUT_DIR / "dedupe_review.csv"
    cols = ["sample_id", "kept_item", "removed_item", "duplicate_type",
            "product_name_similarity", "brand_equal", "spec_equal",
            "unit_price_equal", "quantity_equal", "total_price_equal",
            "category_equal", "reason", "risk_level"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    from collections import Counter
    c = Counter(r["risk_level"] for r in rows)
    print("===== 去重逐条复核 =====")
    print(f"复核条数: {len(rows)}  LOW: {c.get('LOW',0)}  MEDIUM: {c.get('MEDIUM',0)}  HIGH: {c.get('HIGH',0)}")
    for r in rows:
        if r["risk_level"] != "LOW":
            print(f"  [{r['risk_level']}] {r['sample_id']}: {r['reason']}")
    print(f"CSV: {csv_path}")


# ================================================================ 评估对比

def _macro_pr(stats, fields):
    mp = sum(stats[en]["Precision"] for en, _, _ in fields) / len(fields)
    mr = sum(stats[en]["Recall"] for en, _, _ in fields) / len(fields)
    mf = sum(stats[en]["F1"] for en, _, _ in fields) / len(fields)
    return round(mp, 4), round(mr, 4), round(mf, 4)


def do_evaluate():
    from ai_extraction.evaluation.evaluate_attachment_run import read_pred
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_raw, _ = read_pred(SRC_DIR)
    pred_dedup, _ = read_pred(OUT_DIR)
    has_reviewed = REVIEWED_DIR.exists() and any(REVIEWED_DIR.glob("t*.json"))

    stats_raw, counts_raw, _ = evaluate(gold, pred_raw)
    stats_dedup, counts_dedup, _ = evaluate(gold, pred_dedup)
    mp_r, mr_r, mf_r = _macro_pr(stats_raw, FIELDS)
    mp_d, mr_d, mf_d = _macro_pr(stats_dedup, FIELDS)
    if has_reviewed:
        pred_rev, _ = read_pred(REVIEWED_DIR)
        stats_rev, counts_rev, _ = evaluate(gold, pred_rev)
        mp_v, mr_v, mf_v = _macro_pr(stats_rev, FIELDS)

    # ---- t20260508 专项（单文档独立评估，不修改任何全局逻辑）
    target = "t20260508_26523656"
    g1 = {target: gold.get(target, [])}
    st_t_raw, ct_t_raw, _ = evaluate(g1, {target: pred_raw.get(target, [])})
    st_t_dedup, ct_t_dedup, _ = evaluate(g1, {target: pred_dedup.get(target, [])})
    _, _, mf_t_raw = _macro_pr(st_t_raw, FIELDS)
    _, _, mf_t_dedup = _macro_pr(st_t_dedup, FIELDS)
    dedup_info = json.loads(
        (OUT_DIR / f"{target}.json").read_text(encoding="utf-8"))["dedupe_info"]

    result = {
        "note": "V4.1 原始 vs V4.1+离线去重 vs V4.1+复核去重；同一 Gold、同一评估脚本、同一匹配规则",
        "macro": {
            "v4_1": {"Precision": mp_r, "Recall": mr_r, "F1": mf_r},
            "v4_1_dedup": {"Precision": mp_d, "Recall": mr_d, "F1": mf_d},
            "delta": {"Precision": round(mp_d - mp_r, 4),
                      "Recall": round(mr_d - mr_r, 4),
                      "F1": round(mf_d - mf_r, 4)},
        },
        "counts": {
            "v4_1": counts_raw,
            "v4_1_dedup": counts_dedup,
        },
        "fields": {cn: {"v4_1": stats_raw[en], "v4_1_dedup": stats_dedup[en],
                        "delta_F1": round(stats_dedup[en]["F1"] - stats_raw[en]["F1"], 4)}
                   for en, cn, _ in FIELDS},
        "t20260508_26523656": {
            "口径说明": "Gold口径争议样本（Gold=11服务实体, Pred大量设备实体），"
                        "去重策略不为该样本定制，结果如实记录",
            "dedupe_info": dedup_info,
            "v4_1": {"macro_f1": mf_t_raw, "counts": ct_t_raw},
            "v4_1_dedup": {"macro_f1": mf_t_dedup, "counts": ct_t_dedup},
        },
    }
    if has_reviewed:
        result["macro"]["v4_1_dedup_reviewed"] = {
            "Precision": mp_v, "Recall": mr_v, "F1": mf_v}
        result["macro"]["delta_reviewed_vs_v41"] = {
            "Precision": round(mp_v - mp_r, 4),
            "Recall": round(mr_v - mr_r, 4),
            "F1": round(mf_v - mf_r, 4)}
        result["counts"]["v4_1_dedup_reviewed"] = counts_rev
        result["fields_reviewed"] = {
            cn: {"v4_1": stats_raw[en], "v4_1_dedup_reviewed": stats_rev[en],
                 "delta_F1": round(stats_rev[en]["F1"] - stats_raw[en]["F1"], 4)}
            for en, cn, _ in FIELDS}

    out_path = OUT_DIR / "evaluation_v4_1_dedup_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    hdr = "V4.1+rev" if has_reviewed else ""
    print("\n===== V4.1 vs V4.1+dedupe" + (" vs V4.1+reviewed" if has_reviewed else "") + " =====\n")
    print(f"{'指标':<18}{'V4.1':>10}{'V4.1+dedup':>12}{hdr:>11}")
    def _row(label, a, b, c=None):
        line = f"{label:<18}{a:>10.4f}{b:>12.4f}"
        if c is not None:
            line += f"{c:>11.4f}"
        print(line)
    _row("Macro Precision", mp_r, mp_d, mp_v if has_reviewed else None)
    _row("Macro Recall", mr_r, mr_d, mr_v if has_reviewed else None)
    _row("Macro F1", mf_r, mf_d, mf_v if has_reviewed else None)
    print(f"{'Pred products':<18}{counts_raw['pred_products']:>10}"
          f"{counts_dedup['pred_products']:>12}"
          + (f"{counts_rev['pred_products']:>11}" if has_reviewed else ""))
    print(f"{'Matched':<18}{counts_raw['matched']:>10}{counts_dedup['matched']:>12}"
          + (f"{counts_rev['matched']:>11}" if has_reviewed else ""))
    print(f"{'Unmatched Gold':<18}{counts_raw['unmatched_gold']:>10}"
          f"{counts_dedup['unmatched_gold']:>12}"
          + (f"{counts_rev['unmatched_gold']:>11}" if has_reviewed else ""))
    print(f"{'Unmatched Pred':<18}{counts_raw['unmatched_pred']:>10}"
          f"{counts_dedup['unmatched_pred']:>12}"
          + (f"{counts_rev['unmatched_pred']:>11}" if has_reviewed else ""))

    if has_reviewed:
        print(f"\n{'字段':<14}{'V4.1-F1':>9}{'dedup-F1':>10}{'rev-F1':>9}{'Δrev-vs-V41':>12}")
        for en, cn, _ in FIELDS:
            a, b, c = stats_raw[en], stats_dedup[en], stats_rev[en]
            print(f"{cn:<14}{a['F1']:>9.4f}{b['F1']:>10.4f}{c['F1']:>9.4f}"
                  f"{c['F1']-a['F1']:>+12.4f}")
    else:
        print(f"\n{'字段':<14}{'V4.1-P':>8}{'dedup-P':>9}{'V4.1-R':>8}{'dedup-R':>9}"
              f"{'V4.1-F1':>9}{'dedup-F1':>10}{'ΔF1':>8}")
        for en, cn, _ in FIELDS:
            a, b = stats_raw[en], stats_dedup[en]
            print(f"{cn:<14}{a['Precision']:>8.4f}{b['Precision']:>9.4f}"
                  f"{a['Recall']:>8.4f}{b['Recall']:>9.4f}"
                  f"{a['F1']:>9.4f}{b['F1']:>10.4f}{b['F1']-a['F1']:>+8.4f}")

    print(f"\n--- t20260508_26523656（Gold口径争议样本） ---")
    print(f"去重: {dedup_info['before']} -> {dedup_info['after']} "
          f"(exact={dedup_info['exact_removed']}, similar={dedup_info['similar_removed']})")
    print(f"单文档 Macro-F1: {mf_t_raw:.4f} -> {mf_t_dedup:.4f}")
    print(f"counts before: {ct_t_raw}")
    print(f"counts after : {ct_t_dedup}")
    print(f"\nJSON: {out_path}")


if __name__ == "__main__":
    if "--reviewed" in sys.argv:
        # 复核后规则（category 冲突拦截）→ run_v4_1_dedup_reviewed/
        do_dedupe(reviewed=True)
    else:
        do_dedupe()
    if "--review" in sys.argv:
        do_review()
    if "--evaluate" in sys.argv:
        do_evaluate()
