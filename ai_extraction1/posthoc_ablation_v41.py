"""V4.1 高频错误离线后处理消融实验。

不调用 Qwen、不修改 Gold / evaluator / 原始 V4.1 结果 / 前端 / 生产 API。

基线：V4.1 + reviewed dedupe（483 products, Macro-F1 0.5287）
每个实验相对基线只启用一种改动：
  exp1_rowbind      同行绑定校验（物理行；name 唯一定位 + 字段值不在本行 + 出现在他行）
  exp2_projamount   项目金额隔离（产品价 == 项目金额 且不在本产品行 → 置空）
  exp4_rowrecover   续行/跨页逻辑行恢复后再做同行校验（与 exp1 仅差行分割）
  exp5_strictdedupe dedupe 增加 category 全等条件（一侧为空也不合并）

输出：data/validation_attachment_test/posthoc_ablation/{exp}/t*.json
汇总：posthoc_ablation/ablation_summary.json
"""
import copy
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_extraction.dedupe_v41 import (
    FIELDS7, dedupe_products, fingerprint, norm_field, norm_num, norm_text,
)
from ai_extraction.evaluation.evaluate_goldset import is_empty
from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold
from ai_extraction.pipeline import _product_tuple, build_plan
from ai_extraction.run_v4_1_prompt_v22 import build_input, load_selected

DATA = Path(__file__).parent / "data" / "validation_attachment_test"
RAW_DIR = DATA / "run_v4_1_prompt_v22"
OUT_BASE = DATA / "posthoc_ablation"

CHECK_FIELDS = ("spec_model", "unit_price", "quantity", "total_price")
NUM_FIELDS = ("unit_price", "quantity", "total_price")

NUM_RE = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(万元|万)?元?")
PA_RE = re.compile(
    r"(?:总中标金额|中标（成交）金额|中标金额|采购预算|项目预算|预算金额|最高限价)"
    r"[：:\s￥¥]*([\d.]+)\s*(万元|万|元)?")

MIN_NAME_LEN = 3


# ---------------------------------------------------------------- 行分割

def split_rows(chunk_text, recover=False):
    rows = []
    for ln in chunk_text.split("\n"):
        cells = [c.strip() for c in ln.split("|")] if "|" in ln else [ln.strip()]
        rows.append(cells)
    if not recover:
        return rows
    merged = []
    for cells in rows:
        if (merged and len(cells) >= 2 and not cells[0]
                and len(merged[-1]) == len(cells)):
            prev = merged[-1]
            for i, c in enumerate(cells):
                if c:
                    prev[i] = f"{prev[i]} {c}".strip() if prev[i] else c
        else:
            merged.append(cells)
    return merged


# ---------------------------------------------------------------- 值核验

def row_numbers(cells):
    out = []
    for m in NUM_RE.finditer(" ".join(cells)):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 10000
        out.append(v)
    return out


def num_in_row(val, cells):
    return any(abs(t - val) <= max(0.01, abs(val) * 0.001)
               for t in row_numbers(cells))


def text_in_row(val, cells):
    hay = norm_text(" ".join(cells)).replace(" ", "")
    needle = norm_text(val).replace(" ", "")
    return bool(needle) and needle in hay


def value_in_row(field, val, cells):
    if field in NUM_FIELDS:
        return num_in_row(float(norm_num(val)), cells)
    return text_in_row(val, cells)


def find_name_rows(name, rows):
    needle = norm_text(name).replace(" ", "")
    if len(needle) < MIN_NAME_LEN:
        return []
    return [i for i, cells in enumerate(rows)
            if needle in norm_text(" ".join(cells)).replace(" ", "")]


# ---------------------------------------------------------------- 单产品检查

def binding_repairs(p, rows):
    hits = find_name_rows(p.get("product_service_name"), rows)
    if len(hits) != 1:
        return []
    ri = hits[0]
    repairs = []
    for f in CHECK_FIELDS:
        v = p.get(f)
        if is_empty(v):
            continue
        if value_in_row(f, v, rows[ri]):
            continue
        if any(value_in_row(f, v, rows[k])
               for k in range(len(rows)) if k != ri):
            repairs.append({"field": f, "old_value": v,
                            "reason": "name唯一定位行不含此值，该值出现在其他行"})
    return repairs


def project_amount_repairs(p, rows, pa):
    if not pa:
        return []
    hits = find_name_rows(p.get("product_service_name"), rows)
    if len(hits) != 1:
        return []
    ri = hits[0]
    repairs = []
    for f in ("unit_price", "total_price"):
        v = p.get(f)
        if is_empty(v):
            continue
        nv = float(norm_num(v))
        if abs(nv - pa) > max(1, pa * 0.001):
            continue
        if num_in_row(nv, rows[ri]):
            continue
        if any(num_in_row(nv, rows[k]) for k in range(len(rows)) if k != ri):
            repairs.append({"field": f, "old_value": v,
                            "reason": f"{f}={pa:g} 与项目金额相同且不在本产品行"})
    return repairs


# ---------------------------------------------------------------- dedupe 变体（exp5）

def dedupe_strict_category(products):
    """Layer2 追加：category 必须相同或都为空（一侧为空也不合并）。"""
    kept, removed = [], []
    seen = {}
    for i, p in enumerate(products):
        fp = fingerprint(p)
        if fp in seen:
            removed.append({"index": i, "product": p, "duplicate_type": "exact",
                            "kept_index": seen[fp]})
            continue
        match = None
        for j, q in enumerate(kept):
            if not norm_text(p.get("product_service_name")) == norm_text(
                    q.get("product_service_name")):
                continue
            ok = True
            for k in ("brand", "spec_model", "unit_price", "quantity", "total_price"):
                a, b = norm_field(k, p.get(k)), norm_field(k, q.get(k))
                if a != b and (a or b):
                    ok = False
            ca, cb = norm_text(p.get("category")), norm_text(q.get("category"))
            if ca != cb:
                ok = False
            if ok:
                match = j
                break
        if match is None:
            seen[fp] = len(kept)
            kept.append(p)
        else:
            removed.append({"index": i, "product": p, "duplicate_type": "similar",
                            "kept_index": match})
    return kept, removed


# ---------------------------------------------------------------- 样本装载

class Sample:
    def __init__(self, jf, selected):
        self.rec = json.loads(jf.read_text(encoding="utf-8"))
        self.aid = self.rec["announcement_id"]
        self.text, self.kind = build_input(self.aid, selected)
        plan = build_plan(self.text, announcement_id=self.aid)
        self.chunk_texts = [c["text"] for c in plan["chunks"]]
        self.chunk_products = [ch.get("products") or []
                               for ch in self.rec["raw_responses"]]
        self.raw_products = self.rec["result"]["products"]
        # (ci, lj) -> raw_index（与 legacy_merge 同口径）
        seen_tuple = {}
        self.prov = {}
        for ci, prods in enumerate(self.chunk_products):
            for lj, p in enumerate(prods):
                t = _product_tuple(p)
                if t not in seen_tuple:
                    seen_tuple[t] = len(seen_tuple)
                self.prov[(ci, lj)] = seen_tuple[t]

    def project_amounts(self):
        amounts = set()
        pa = self.rec["result"].get("project_amount")
        if not is_empty(pa):
            amounts.add(float(pa))
        for m in PA_RE.finditer(self.text):
            v = float(m.group(1))
            if m.group(2):
                v *= 10000
            amounts.add(v)
        return amounts


# ---------------------------------------------------------------- 实验执行

def collect_repairs(sample, mode):
    """mode: rowbind / rowrecover / projamount。返回 {raw_index: [repair]}"""
    pas = sample.project_amounts() if mode == "projamount" else set()
    bag = {}
    for ci, prods in enumerate(sample.chunk_products):
        rows = split_rows(sample.chunk_texts[ci], recover=(mode == "rowrecover"))
        for lj, p in enumerate(prods):
            if mode == "projamount":
                found = []
                for pa in pas:
                    found.extend(project_amount_repairs(p, rows, pa))
            else:
                found = binding_repairs(p, rows)
            if found:
                bag.setdefault(sample.prov[(ci, lj)], []).extend(found)
    return bag


def apply_and_dedupe(sample, repairs_by_idx):
    products = copy.deepcopy(sample.raw_products)
    applied = []
    for idx, reps in repairs_by_idx.items():
        fields = {r["field"] for r in reps}
        for f in fields:
            applied.append({"raw_index": idx, **next(
                r for r in reps if r["field"] == f)})
            products[idx][f] = None
    kept, removed = dedupe_products(products, category_guard=True)
    return kept, removed, applied


def run_repair_experiment(samples, name, mode):
    t0 = time.perf_counter()
    out_dir = OUT_BASE / name
    out_dir.mkdir(parents=True, exist_ok=True)
    pred = {}
    per_doc = []
    for s in samples:
        bag = collect_repairs(s, mode)
        kept, removed, applied = apply_and_dedupe(s, bag)
        pred[s.aid] = kept
        new_rec = copy.deepcopy(s.rec)
        new_rec["result"]["products"] = kept
        new_rec["posthoc_meta"] = {"experiment": name, "repairs": applied,
                                   "extra_dedup_removed": len(removed)}
        (out_dir / f"{s.aid}.json").write_text(
            json.dumps(new_rec, ensure_ascii=False, indent=1), encoding="utf-8")
        per_doc.append({"sample_id": s.aid,
                        "before": len(s.raw_products),
                        "fields_nulled": len(applied),
                        "after": len(kept)})
    elapsed = round(time.perf_counter() - t0, 2)
    stats, counts, _ = evaluate(read_gold(), pred)
    return _exp_result(name, stats, counts, per_doc, elapsed)


def run_dedupe_variant(samples):
    t0 = time.perf_counter()
    out_dir = OUT_BASE / "exp5_strictdedupe"
    out_dir.mkdir(parents=True, exist_ok=True)
    pred = {}
    per_doc = []
    for s in samples:
        kept, removed = dedupe_strict_category(copy.deepcopy(s.raw_products))
        pred[s.aid] = kept
        new_rec = copy.deepcopy(s.rec)
        new_rec["result"]["products"] = kept
        new_rec["posthoc_meta"] = {"experiment": "exp5_strictdedupe",
                                   "removed": len(removed)}
        (out_dir / f"{s.aid}.json").write_text(
            json.dumps(new_rec, ensure_ascii=False, indent=1), encoding="utf-8")
        per_doc.append({"sample_id": s.aid, "before": len(s.raw_products),
                        "fields_nulled": 0, "after": len(kept)})
    elapsed = round(time.perf_counter() - t0, 2)
    stats, counts, _ = evaluate(read_gold(), pred)
    return _exp_result("exp5_strictdedupe", stats, counts, per_doc, elapsed)


# ---------------------------------------------------------------- 评估汇总

def _macro(stats):
    p = sum(stats[en]["Precision"] for en, _, _ in _FIELDS) / 7
    r = sum(stats[en]["Recall"] for en, _, _ in _FIELDS) / 7
    f = sum(stats[en]["F1"] for en, _, _ in _FIELDS) / 7
    return round(p, 4), round(r, 4), round(f, 4)


from ai_extraction.evaluation.evaluate_goldset import FIELDS as _FIELDS
FIELD_LABELS = [(en, cn) for en, cn, _ in _FIELDS]


def _exp_result(name, stats, counts, per_doc, elapsed):
    mp, mr, mf = _macro(stats)
    return {
        "name": name, "posthoc_seconds": elapsed,
        "macro": {"Precision": mp, "Recall": mr, "F1": mf},
        "counts": counts,
        "field_f1": {cn: stats[en]["F1"] for en, cn in FIELD_LABELS},
        "field_prf": {cn: {"P": stats[en]["Precision"],
                           "R": stats[en]["Recall"], "F1": stats[en]["F1"]}
                      for en, cn in FIELD_LABELS},
        "per_doc": per_doc,
    }


def baseline_results(samples):
    out = {}
    # V4.1 原始
    stats0, cnt0, _ = evaluate(
        read_gold(), {s.aid: s.raw_products for s in samples})
    out["v4_1"] = _exp_result("v4_1", stats0, cnt0, [], 0)
    # V4.1 + reviewed dedupe（复算，口径一致）
    pred = {}
    t0 = time.perf_counter()
    for s in samples:
        kept, _ = dedupe_products(copy.deepcopy(s.raw_products),
                                  category_guard=True)
        pred[s.aid] = kept
    elapsed = round(time.perf_counter() - t0, 2)
    stats1, cnt1, _ = evaluate(read_gold(), pred)
    out["v4_1_reviewed_dedupe"] = _exp_result(
        "v4_1_reviewed_dedupe", stats1, cnt1, [], elapsed)
    return out


# ---------------------------------------------------------------- 实验3 候选统计（不调用 API）

def count_retarget_candidates(samples):
    n_products = n_located = n_candidates = 0
    per_aid = {}
    for s in samples:
        loc = cand = 0
        seen_rows = set()
        for ci, prods in enumerate(s.chunk_products):
            rows = split_rows(s.chunk_texts[ci])
            for p in prods:
                n_products += 1
                hits = find_name_rows(p.get("product_service_name"), rows)
                if len(hits) != 1:
                    continue
                loc += 1
                missing = [f for f in CHECK_FIELDS if is_empty(p.get(f))]
                if missing and (ci, hits[0]) not in seen_rows:
                    seen_rows.add((ci, hits[0]))
                    cand += 1
        n_located += loc
        n_candidates += cand
        per_aid[s.aid] = {"unique_row_products": loc, "retarget_calls": cand}
    return {"chunk_products": n_products, "unique_row_products": n_located,
            "estimated_api_calls": n_candidates, "per_aid": per_aid}


# ---------------------------------------------------------------- main

def main():
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    print("装载样本（重建输入与 chunk 对齐，不调 API）...")
    selected = load_selected()
    samples = [Sample(jf, selected) for jf in sorted(RAW_DIR.glob("t*.json"))]
    print(f"装载完成: {len(samples)} 条\n")

    summary = {"baseline": baseline_results(samples), "experiments": []}

    experiments = [
        ("exp1_rowbind", "rowbind"),
        ("exp2_projamount", "projamount"),
        ("exp4_rowrecover", "rowrecover"),
    ]
    for name, mode in experiments:
        r = run_repair_experiment(samples, name, mode)
        summary["experiments"].append(r)
        print(f"{name} 完成: F1={r['macro']['F1']} "
              f"({r['posthoc_seconds']}s)")

    r5 = run_dedupe_variant(samples)
    summary["experiments"].append(r5)
    print(f"exp5_strictdedupe 完成: F1={r5['macro']['F1']}")

    summary["exp3_retarget_plan_no_api_calls"] = count_retarget_candidates(samples)

    (OUT_BASE / "ablation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(summary)


def _print_report(summary):
    base = summary["baseline"]["v4_1"]
    rev = summary["baseline"]["v4_1_reviewed_dedupe"]
    print("\n" + "=" * 78)
    print(f"{'实验':<24}{'Pred':>6}{'Matched':>9}{'UnmG':>6}{'UnmP':>6}"
          f"{'P':>8}{'R':>8}{'F1':>8}{'后处理s':>8}")
    def row(tag, r, sec):
        c = r["counts"]
        m = r["macro"]
        print(f"{tag:<24}{c['pred_products']:>6}{c['matched']:>9}"
              f"{c['unmatched_gold']:>6}{c['unmatched_pred']:>6}"
              f"{m['Precision']:>8.4f}{m['Recall']:>8.4f}{m['F1']:>8.4f}"
              f"{sec:>8}")
    row("V4.1", base, 0)
    row("V4.1+reviewed dedupe", rev, rev["posthoc_seconds"])
    for r in summary["experiments"]:
        row(r["name"], r, r["posthoc_seconds"])

    print("\n7 字段 F1：")
    print(f"{'字段':<12}{'V4.1':>9}{'reviewed':>10}", end="")
    for r in summary["experiments"]:
        print(f"{r['name'][:12]:>13}", end="")
    print()
    for en, cn in FIELD_LABELS:
        print(f"{cn:<12}{base['field_f1'][cn]:>9.4f}"
              f"{rev['field_f1'][cn]:>10.4f}", end="")
        for r in summary["experiments"]:
            print(f"{r['field_f1'][cn]:>13.4f}", end="")
        print()

    p3 = summary["exp3_retarget_plan_no_api_calls"]
    print("\n实验3（二次定向补抽）候选统计，本轮未发起任何 API 调用：")
    print(f"  chunk products={p3['chunk_products']}  唯一行定位="
          f"{p3['unique_row_products']}  预计 API 调用数="
          f"{p3['estimated_api_calls']}")


if __name__ == "__main__":
    main()
