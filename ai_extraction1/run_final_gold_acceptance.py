"""10 个 Gold 公告最终验收：A 组（当前可复现 V1 基线）vs B 组（正式接入预处理主流程）。

严格控制变量：
  - 不修改任何业务代码 / Prompt / Gold
  - B 组运行时临时把 qwen_extractor.EXTRACT_PROMPT 换成原始 V1（run_v1_control 常量）
  - qwen-vl-max，temperature=0，json_object，调用参数不变
  - B 组输出 data/gold/final_pipeline_test/，不覆盖旧 V1/V2/POC
  - A 组直接读取 data/baseline_output_v1_rerun（不重跑）

产出：
  final_pipeline_test/predictions/<aid>.json   B 组逐公告 record
  final_pipeline_test/run_logs/<aid>.log       B 组逐公告原始调用日志
  final_pipeline_test/final_acceptance.csv / .json

口径：
  - 7 字段 P/R/F1 + Macro-F1：沿用现有改进版匹配 evaluate_goldset_v2（三级 1:1 匹配）
  - 主口径为剔除 t20260811 的 9 公告（Gold 与 Pred 同粒度）
  - t20260811 Gold 为宗级列表口径（2 行=19+28 个位置），单列“复杂表格专项”，
    不做 18 Gold × N Pred 的普通 1:1 匹配
  - 另附含 t20260811 的 10 公告旧口径数字，仅用于与历史 evaluation_result_v2 对照

用法：
  python -m ai_extraction.run_final_gold_acceptance            # 跑 B 组 + 评估
  python -m ai_extraction.run_final_gold_acceptance --eval-only  # 只评估（不调 Qwen）
"""
import argparse
import contextlib
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.evaluation.evaluate_goldset import FIELDS, normalize_text
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    evaluate_version, read_gold_full,
)
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
A_DIR = BASE / "data" / "baseline_output_v1_rerun"
OUT_DIR = BASE / "data" / "gold" / "final_pipeline_test"
PRED_DIR = OUT_DIR / "predictions"
LOG_DIR = OUT_DIR / "run_logs"

SPECIAL_AID = "t20260811_27115458"
UNCLOSED_PAREN = re.compile(r"[（(][^)）]*$")


# ============================================================ B 组运行

class TeeToFile:
    """stdout 分流：写日志文件，同时统计标记，不把巨量输出留在内存。"""

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.json_parse_failures = 0

    def write(self, s):
        if "JSON 解析失败" in s:
            self.json_parse_failures += s.count("JSON 解析失败")
        return self.f.write(s)

    def flush(self):
        self.f.flush()

    def close(self):
        self.f.close()


def run_group_b(aids):
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    extractor = qwen_extractor.QwenExtractor()
    print("B组模型:", extractor.model, "| Prompt=原始V1 | temperature=0 | json_object")
    for i, aid in enumerate(aids, 1):
        html_path = VAL_DIR / f"{aid}.html"
        print(f"\n[{i}/{len(aids)}] B组处理 {aid}")
        html = html_path.read_text(encoding="utf-8", errors="ignore")
        tee = TeeToFile(LOG_DIR / f"{aid}.log")
        try:
            with contextlib.redirect_stdout(tee):
                rec = process_html(html, aid, extractor,
                                   file_id="", source_file=html_path.name)
        finally:
            tee.close()
        rec["json_parse_failures_in_log"] = tee.json_parse_failures
        (PRED_DIR / f"{aid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        n = len(rec.get("result", {}).get("products", [])) if rec.get("result") else 0
        print(f"    -> mode={rec['mode']} status={rec['status']} "
              f"products={n} chunks={rec['chunks_count']} "
              f"api_calls={rec['api_calls']} "
              f"json_fail={tee.json_parse_failures} "
              f"errors={len(rec['chunk_errors'])}")


# ============================================================ 读取

def read_records(d):
    out = {}
    for jf in sorted(Path(d).glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        out[r.get("announcement_id", jf.stem)] = r
    return out


def preds_of(records):
    return {aid: (r.get("result", {}) or {}).get("products", [])
            for aid, r in records.items()}


# ============================================================ 结构指标

def count_truncated_specs(products):
    hits = []
    for p in products:
        s = p.get("spec_model")
        if s and UNCLOSED_PAREN.search(str(s)):
            hits.append(str(s))
    return hits


def structured_fidelity(rec):
    """structured 记录：按结构位置核对重复丢失与品牌↔型号错位。

    legacy/无位置记录返回 None（不可机械判定）。
    """
    if rec.get("mode") != "structured":
        return None
    html = (VAL_DIR / f"{rec['announcement_id']}.html").read_text(
        encoding="utf-8", errors="ignore")
    plan = build_plan(html, announcement_id=rec["announcement_id"])
    expect = {}
    for _, _, u in plan["units_all"]:
        expect[u["position_id"]] = (u.get("brand"), u.get("spec_model"))
    got = {}
    for t in rec.get("position_trace", []):
        if t["source"] in ("model", "structural"):
            got[t["position"]] = (t.get("brand"), t.get("spec_model"))

    # 重复项丢失：期望 (品牌,型号) 多重集 vs 最终多重集
    exp_ms, got_ms = Counter(expect.values()), Counter(got.values())
    dup_lost = 0
    missing_pairs = []
    for pair, n in exp_ms.items():
        gap = n - got_ms.get(pair, 0)
        if gap > 0:
            dup_lost += gap
            missing_pairs.append({"brand": pair[0], "spec_model": pair[1],
                                  "missing": gap})
    # 错位：该位置拿到的完整 (品牌,型号) 对等于另一个位置的期望对
    exp_pair_positions = {}
    for pid, pair in expect.items():
        exp_pair_positions.setdefault(pair, []).append(pid)
    misaligned = []
    for pid, pair in got.items():
        owners = exp_pair_positions.get(pair, [])
        if pid not in owners and owners:
            misaligned.append({"position": pid, "got": list(pair),
                               "belongs_to": owners})
    missing_positions = sorted(set(expect) - set(got))
    return {
        "expected_positions": len(expect),
        "returned_positions": len(got),
        "missing_positions": missing_positions,
        "duplicate_pairs_lost": dup_lost,
        "missing_pairs": missing_pairs,
        "misaligned_positions": len(misaligned),
        "misaligned_examples": misaligned[:10],
    }


def per_file_row(group, aid, rec):
    products = (rec.get("result", {}) or {}).get("products", []) if rec.get("result") else []
    trunc = count_truncated_specs(products)
    fid = structured_fidelity(rec)
    return {
        "group": group,
        "announcement_id": aid,
        "mode": rec.get("mode") or "legacy",
        "status": rec.get("status"),
        "products": len(products),
        "planned_chunks": rec.get("chunks_count"),
        "api_calls": rec.get("api_calls", len(rec.get("raw_responses", []))),
        "chunk_errors": len(rec.get("chunk_errors", [])),
        "json_parse_failures": rec.get("json_parse_failures_in_log"),
        "spec_truncated": len(trunc),
        "spec_truncated_examples": trunc[:5],
        "duplicate_pairs_lost": fid["duplicate_pairs_lost"] if fid else None,
        "misaligned_positions": fid["misaligned_positions"] if fid else None,
        "expected_positions": fid["expected_positions"] if fid else None,
        "returned_positions": fid["returned_positions"] if fid else None,
        "chunk_error_detail": rec.get("chunk_errors", [])[:3],
    }


# ============================================================ F1

def macro(stats):
    return round(sum(stats[en]["F1"] for en, _, _ in FIELDS) / len(FIELDS), 4)


def field_block(stats, counts, group, scope):
    rows = []
    for en, cn, _ in FIELDS:
        s = stats[en]
        rows.append({"scope": scope, "group": group, "field": cn,
                     "TP": s["TP"], "FP": s["FP"], "FN": s["FN"],
                     "Precision": s["Precision"], "Recall": s["Recall"],
                     "F1": s["F1"], "gold_valid": s["gold_valid"]})
    return rows


def eval_scope(gold_data, a_preds, b_preds, aids, scope):
    g = [x for x in gold_data if x["announcement_id"] in aids]
    a_stats, a_c = evaluate_version(g, {k: v for k, v in a_preds.items() if k in aids})
    b_stats, b_c = evaluate_version(g, {k: v for k, v in b_preds.items() if k in aids})
    rows = field_block(a_stats, a_c, "A_v1", scope) + \
        field_block(b_stats, b_c, "B_pipeline", scope)
    summary = {
        "scope": scope,
        "announcements": len(aids),
        "A_v1": {"macro_f1": macro(a_stats), "products": a_c["total_pred"],
                 "matched": a_c["matched"], "true_miss": a_c["true_miss"],
                 "true_extra": a_c["true_extra"]},
        "B_pipeline": {"macro_f1": macro(b_stats), "products": b_c["total_pred"],
                       "matched": b_c["matched"], "true_miss": b_c["true_miss"],
                       "true_extra": b_c["true_extra"]},
        "field_rows": rows,
    }
    return summary


# ============================================================ t20260811 专项

def gold_positions_47(gold_data):
    """从 t20260811 的 2 条宗级 Gold 行还原 47 个有序品牌/型号位置。"""
    rows = [g for g in gold_data if g["announcement_id"] == SPECIAL_AID]
    out = {}
    for g in rows:
        name = normalize_text(g.get("product_service_name"))
        if not name:
            continue
        brands = [x.strip() for x in re.split(r"[、；;]", str(g.get("brand") or ""))
                  if x.strip()]
        specs = [x.strip() for x in re.split(r"[、；;]", str(g.get("spec_model") or ""))
                 if x.strip()]
        out[name] = list(zip(brands, specs))
    return out


def b_position_pairs(rec):
    """B 组最终每个结构位置的 (品牌,型号)，按表/行/子项顺序。"""
    html = (VAL_DIR / f"{SPECIAL_AID}.html").read_text(
        encoding="utf-8", errors="ignore")
    plan = build_plan(html, announcement_id=SPECIAL_AID)
    units = []
    for _, _, u in plan["units_all"]:
        units.append((u["position_id"], u.get("product_service_name"),
                      u.get("brand"), u.get("spec_model")))

    def keyf(pid):
        m = re.match(r"t(\d+)-r(\d+)-s(\d+)$", pid)
        return tuple(int(x) for x in m.groups())

    got = {t["position"]: (t.get("brand"), t.get("spec_model"), t["source"])
           for t in rec.get("position_trace", [])
           if t["source"] in ("model", "structural")}
    ordered = []
    for pid, target, _eb, _es in sorted(units, key=lambda x: keyf(x[0])):
        b, s, src = got.get(pid, (None, None, None))
        ordered.append({"position": pid, "target": target,
                        "gold_brand": _eb, "gold_spec": _es,
                        "pred_brand": b, "pred_spec": s, "source": src})
    return ordered


def special_section(gold_data, a_rec, b_rec):
    gold_rows = gold_positions_47(gold_data)
    a_products = (a_rec.get("result", {}) or {}).get("products", [])
    b_products = (b_rec.get("result", {}) or {}).get("products", [])
    ordered = b_position_pairs(b_rec)

    # 按采购目标把 47 位置对齐到 Gold 两行（顺序一一对应）
    detail = []
    n_brand_ok = n_spec_ok = n_pair_ok = n_trunc = 0
    cross_pair = []
    gold_pair_index = {}
    for target, pairs in gold_rows.items():
        for i, pair in enumerate(pairs):
            gold_pair_index.setdefault((normalize_text(pair[0]),
                                        normalize_text(pair[1])), []).append(
                f"{target}#{i+1}")
    for o in ordered:
        gt = o["target"]
        idx = sum(1 for x in detail if x["target"] == gt)
        gpairs = gold_rows.get(gt, [])
        gb, gs = gpairs[idx] if idx < len(gpairs) else (None, None)
        pb, ps = o["pred_brand"], o["pred_spec"]
        bok = gb is not None and normalize_text(pb) == normalize_text(gb)
        sok = gs is not None and normalize_text(ps) == normalize_text(gs)
        pok = bok and sok
        n_brand_ok += bok
        n_spec_ok += sok
        n_pair_ok += pok
        trunc = False
        if ps and gs:
            pn, gn = normalize_text(ps), normalize_text(gs)
            if (UNCLOSED_PAREN.search(str(ps))
                    or (pn and gn.startswith(pn) and len(gn) - len(pn) >= 2)):
                trunc = True
                n_trunc += 1
        key = (normalize_text(pb), normalize_text(ps))
        own_label = f"{gt}#{idx+1}"
        owners = gold_pair_index.get(key, [])
        # 仅当本位置拿到的对≠本位置期望对、却等于其他位置期望对时才算错位；
        # 重复对（如 11 个「讯方/定制」）在本位置正确时不算错位。
        if own_label not in owners and owners:
            cross_pair.append({"position": o["position"], "pred": [pb, ps],
                               "gold_owner_at_other_positions": owners})
        detail.append({"target": gt, "seq": idx + 1, "position": o["position"],
                       "gold_brand": gb, "pred_brand": pb, "brand_ok": bok,
                       "gold_spec": gs, "pred_spec": ps, "spec_ok": sok,
                       "pair_ok": pok, "truncated": trunc})

    # 重复项丢失（按 Gold 47 位置多重集）
    gold_ms = Counter((normalize_text(b), normalize_text(s))
                      for pairs in gold_rows.values() for b, s in pairs)
    pred_ms = Counter((normalize_text(o["pred_brand"]),
                       normalize_text(o["pred_spec"])) for o in ordered)
    missing_pairs, dup_lost = [], 0
    for pair, n in gold_ms.items():
        gap = n - pred_ms.get(pair, 0)
        if gap > 0:
            dup_lost += gap
            missing_pairs.append({"brand": pair[0], "spec_model": pair[1],
                                  "gold_count": n,
                                  "pred_count": pred_ms.get(pair, 0),
                                  "missing": gap})
    gold_dingzhi = sum(1 for pairs in gold_rows.values()
                       for _, s in pairs if normalize_text(s) == "定制")
    pred_dingzhi = sum(1 for o in ordered
                       if normalize_text(o["pred_spec"]) == "定制")

    # A 组在 47 位置上的补充覆盖（非 1:1，仅看型号 token 覆盖）
    a_specs = Counter(normalize_text(p.get("spec_model"))
                      for p in a_products if p.get("spec_model"))
    a_spec_token_hit = sum(1 for pairs in gold_rows.values()
                           for _, s in pairs if normalize_text(s) in a_specs)

    return {
        "gold_granularity": "宗级列表 2 行 = AIGC 19 位置 + 人工智能与数字电路 28 位置 = 47",
        "A_original_v1": {
            "products": len(a_products),
            "spec_token_of_47_found_anywhere": a_spec_token_hit,
            "dingzhi_specs": sum(v for k, v in a_specs.items() if k == "定制"),
        },
        "B_pipeline_v1": {
            "products": len(b_products),
            "gold_positions": sum(len(v) for v in gold_rows.values()),
            "brand_correct": n_brand_ok,
            "spec_correct": n_spec_ok,
            "pair_correct": n_pair_ok,
            "misaligned_positions": len(cross_pair),
            "misaligned_examples": cross_pair[:10],
            "truncated_positions": n_trunc,
            "duplicate_pairs_lost": dup_lost,
            "missing_pairs": missing_pairs,
            "dingzhi_gold": gold_dingzhi,
            "dingzhi_pred": pred_dingzhi,
            "model_bound": sum(1 for o in ordered if o["source"] == "model"),
            "structural_backfill": sum(
                1 for o in ordered if o["source"] == "structural"),
        },
        "position_detail": detail,
    }


# ============================================================ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-only", action="store_true")
    args = ap.parse_args()

    gold_data = read_gold_full()
    aids = sorted({g["announcement_id"] for g in gold_data})
    assert len(aids) == 10, f"期望10公告，实际 {len(aids)}"

    if not args.eval_only:
        run_group_b(aids)

    a_records = read_records(A_DIR)
    b_records = read_records(PRED_DIR)
    missing = [a for a in aids if a not in a_records or a not in b_records]
    assert not missing, f"缺少记录: {missing}"
    a_preds, b_preds = preds_of(a_records), preds_of(b_records)

    # ---- 每公告结构指标
    per_file = ([per_file_row("A_v1", a, a_records[a]) for a in aids]
                + [per_file_row("B_pipeline", a, b_records[a]) for a in aids])

    # ---- F1：9 公告主口径 + 10 公告对照口径
    nine = [a for a in aids if a != SPECIAL_AID]
    f1_nine = eval_scope(gold_data, a_preds, b_preds, nine,
                         "9公告_剔除t20260811_同粒度主口径")
    f1_ten = eval_scope(gold_data, a_preds, b_preds, aids,
                        "10公告_含宗级口径_仅与历史对照")

    # ---- t20260811 专项
    special = special_section(gold_data, a_records[SPECIAL_AID],
                              b_records[SPECIAL_AID])

    result = {
        "note": "10 Gold 最终验收：A=当前可复现V1基线(baseline_output_v1_rerun)；"
                "B=正式接入预处理主流程(pipeline+原V1 Prompt+qwen-vl-max,temp0)。"
                "非官方比赛最终得分。",
        "announcements": aids,
        "per_file": per_file,
        "f1_scopes": [f1_nine, f1_ten],
        "special_t20260811": special,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "final_acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- CSV（分节单文件）
    with open(OUT_DIR / "final_acceptance.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["# section1 每公告结构指标"])
        w.writerow(["group", "announcement_id", "mode", "status", "products",
                    "planned_chunks", "api_calls", "chunk_errors",
                    "json_parse_failures", "spec_truncated",
                    "duplicate_pairs_lost", "misaligned_positions",
                    "expected_positions", "returned_positions"])
        for r in per_file:
            w.writerow([r["group"], r["announcement_id"], r["mode"], r["status"],
                        r["products"], r["planned_chunks"], r["api_calls"],
                        r["chunk_errors"],
                        "" if r["json_parse_failures"] is None
                        else r["json_parse_failures"],
                        r["spec_truncated"],
                        "" if r["duplicate_pairs_lost"] is None
                        else r["duplicate_pairs_lost"],
                        "" if r["misaligned_positions"] is None
                        else r["misaligned_positions"],
                        "" if r["expected_positions"] is None
                        else r["expected_positions"],
                        "" if r["returned_positions"] is None
                        else r["returned_positions"]])
        w.writerow([])
        w.writerow(["# section2 字段级 P/R/F1（改进版 Gold 匹配 v2）"])
        w.writerow(["scope", "group", "field", "TP", "FP", "FN",
                    "Precision", "Recall", "F1", "gold_valid"])
        for sc in result["f1_scopes"]:
            for r in sc["field_rows"]:
                w.writerow([r["scope"], r["group"], r["field"], r["TP"],
                            r["FP"], r["FN"], r["Precision"], r["Recall"],
                            r["F1"], r["gold_valid"]])
        w.writerow([])
        w.writerow(["# section3 汇总"])
        w.writerow(["scope", "group", "macro_f1", "products", "matched",
                    "true_miss", "true_extra"])
        for sc in result["f1_scopes"]:
            for g in ("A_v1", "B_pipeline"):
                d = sc[g]
                w.writerow([sc["scope"], g, d["macro_f1"], d["products"],
                            d["matched"], d["true_miss"], d["true_extra"]])

    print_report(result)


def print_report(result):
    pf = result["per_file"]
    arows = [r for r in pf if r["group"] == "A_v1"]
    brows = [r for r in pf if r["group"] == "B_pipeline"]
    nine = next(s for s in result["f1_scopes"] if s["scope"].startswith("9"))
    ten = next(s for s in result["f1_scopes"] if s["scope"].startswith("10"))
    sp = result["special_t20260811"]

    print("\n" + "=" * 72)
    print("每公告结构指标（A 原始V1 / B 预处理V1）")
    print("=" * 72)
    print(f"{'announcement_id':<22}{'组':>3}{'mode':>11}{'status':>9}"
          f"{'products':>9}{'chunks':>7}{'calls':>6}{'JSON失败':>8}"
          f"{'截断':>5}{'重复丢':>6}{'错位':>5}")
    for r in arows + brows:
        def v(x):
            return "-" if x is None else x
        print(f"{r['announcement_id']:<22}{r['group'][0]:>3}{r['mode']:>11}"
              f"{str(r['status']):>9}{r['products']:>9}"
              f"{v(r['planned_chunks']):>7}{v(r['api_calls']):>6}"
              f"{v(r['json_parse_failures']):>8}{r['spec_truncated']:>5}"
              f"{v(r['duplicate_pairs_lost']):>6}{v(r['misaligned_positions']):>5}")

    for sc, tag in [(nine, "主口径 9 公告（剔除 t20260811）"),
                    (ten, "对照口径 10 公告（含宗级口径）")]:
        print("\n" + "=" * 72)
        print(tag)
        print("=" * 72)
        af, bf = {r["field"]: r for r in sc["field_rows"] if r["group"] == "A_v1"}, \
                 {r["field"]: r for r in sc["field_rows"] if r["group"] == "B_pipeline"}
        print(f"{'字段':<12}{'A P':>8}{'A R':>8}{'A F1':>8}"
              f"{'B P':>8}{'B R':>8}{'B F1':>8}{'F1变化':>9}")
        for _, cn, _ in FIELDS:
            a, b = af[cn], bf[cn]
            print(f"{cn:<12}{a['Precision']:>8.4f}{a['Recall']:>8.4f}{a['F1']:>8.4f}"
                  f"{b['Precision']:>8.4f}{b['Recall']:>8.4f}{b['F1']:>8.4f}"
                  f"{b['F1']-a['F1']:>+9.4f}")
        print(f"Macro-F1  A={sc['A_v1']['macro_f1']}  "
              f"B={sc['B_pipeline']['macro_f1']}  "
              f"变化 {sc['B_pipeline']['macro_f1']-sc['A_v1']['macro_f1']:+.4f}")
        print(f"products  A={sc['A_v1']['products']}  B={sc['B_pipeline']['products']}")

    print("\n" + "=" * 72)
    print("===== t20260811 复杂表格专项 =====")
    print("=" * 72)
    print("Gold 口径:", sp["gold_granularity"])
    print(f"原始 V1：products={sp['A_original_v1']['products']}；"
          f"47位置型号token在A输出中出现 {sp['A_original_v1']['spec_token_of_47_found_anywhere']} 次；"
          f"A中『定制』型号 {sp['A_original_v1']['dingzhi_specs']} 条")
    b = sp["B_pipeline_v1"]
    print(f"预处理 V1：products={b['products']}（期望 {b['gold_positions']} 位置）")
    print(f"品牌对应正确 {b['brand_correct']}/47；型号对应正确 {b['spec_correct']}/47；"
          f"整对正确 {b['pair_correct']}/47")
    print(f"错位 {b['misaligned_positions']}；截断 {b['truncated_positions']}；"
          f"重复项丢失 {b['duplicate_pairs_lost']}")
    print(f"『定制』Gold {b['dingzhi_gold']} 个 → Pred {b['dingzhi_pred']} 个；"
          f"模型直接对齐 {b['model_bound']} 位置，机械回填 "
          f"{b['structural_backfill']} 位置")
    print(f"\n输出: {OUT_DIR}")


if __name__ == "__main__":
    main()
