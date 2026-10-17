"""HTML 表格预处理正式接入后的集成回归测试（不调用 Qwen）。

核验四部分：
  A. merge 语义（合成数据）
     - 同一结构位置在多个 chunk 重复返回 → 去重为 1
     - 不同结构位置即使 7 字段完全相同（如多个「讯方/定制」）→ 全部保留
     - legacy 无位置产品 → 仍按全字段去重（历史行为不变）
  B. t20260811_27115458 结构化计划
     - 47 个原始子项全部保留、位置 ID 唯一
     - 重复「定制」全部保留、brand↔spec 位置对应正确
     - 每个产品 chunk ≤10 子项、子项不跨 chunk、宗/包上下文随块携带
     - 已结构化产品表从 context 中移除（模型不会再看到畸形并行串）
     - 对齐回填模拟：模型折叠重复项后仍恢复 47 个位置
  C. 30 个 baseline 文件输入行为
     - 29 个无 split 文件：legacy 路径，输入文本/分块与历史逐字一致
     - t20260811_27115458：structured
  D. 11 个真实并行列表 + 11 个门拒绝文件
     - split 文件：子项数与上轮结构回归一致、机械对应、安全分块
     - 拒绝文件：无 split、keep_raw 原文原样保留

输出：ai_extraction/data/table_test/integration_regression_summary.json
"""
import json
import re
from collections import Counter
from pathlib import Path

from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.chunker import chunk_text
from ai_extraction.preprocessing.html_table_preprocessor import parse_announcement
from ai_extraction.pipeline import (
    MAX_ITEMS_PER_CHUNK, align_products, build_plan, merge_chunk_results,
)
from ai_extraction.config import MAX_CHUNK_CHARS, empty_result

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
BASELINE_DIR = BASE / "data" / "baseline_input"
TABLE_TEST = BASE / "data" / "table_test"
STRUCT_JSON = TABLE_TEST / "structure_regression_summary.json"
TARGET = "t20260811_27115458"

results = {"checks": [], "sections": {}}


def check(section, name, ok, detail=""):
    results["checks"].append({"section": section, "name": name,
                              "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {section} :: {name}"
          + (f" — {detail}" if detail else ""))
    return bool(ok)


def read(fp):
    return Path(fp).read_text(encoding="utf-8", errors="ignore")


# ================================================================ A. merge

def _p(name, brand, spec):
    return {"product_service_name": name, "category": None,
            "brand": brand, "spec_model": spec,
            "unit_price": None, "quantity": None, "total_price": None}


def test_merge():
    s = "A_merge"
    # A1 不同位置、完全相同字段 → 保留 2
    units = [
        (0, 1, {"position_id": "t0-r1-s1", "strict": True,
                "product_service_name": "X", "brand": "讯方",
                "spec_model": "定制", "category": None}),
        (0, 1, {"position_id": "t0-r1-s2", "strict": True,
                "product_service_name": "X", "brand": "讯方",
                "spec_model": "定制", "category": None}),
    ]
    prods = [_p("X", "讯方", "定制"), _p("X", "讯方", "定制")]
    r = empty_result("x"); r["products"] = prods
    aligned = align_products(units, prods, chunk_tag="0")
    m = merge_chunk_results([{"result": r, "aligned": aligned}], "a", "", "")
    check(s, "不同位置完全相同字段保留", len(m["products"]) == 2,
          f"products={len(m['products'])}")

    # A2 同一位置在两个 chunk 重复 → 去重为 1
    p1, p2 = _p("X", "讯方", "定制"), _p("X", "讯方", "定制")
    r1 = empty_result("x"); r1["products"] = [p1]
    r2 = empty_result("x"); r2["products"] = [p2]
    payloads = [
        {"result": r1, "aligned": [(p1, "t0-r1-s1", "model")]},
        {"result": r2, "aligned": [(p2, "t0-r1-s1", "model")]},
    ]
    m2 = merge_chunk_results(payloads, "a", "", "")
    check(s, "同一位置跨chunk重复去重",
          len(m2["products"]) == 1
          and m2["_merge_stats"]["same_position_dedup"] == 1,
          f"products={len(m2['products'])} dedup={m2['_merge_stats']['same_position_dedup']}")

    # A3 legacy 全字段去重不变
    q1, q2 = _p("Y", "华为", "S1"), _p("Y", "华为", "S1")
    r3 = empty_result("x"); r3["products"] = [q1, q2]
    m3 = merge_chunk_results(
        [{"result": r3, "aligned": None}], "a", "", "")
    check(s, "legacy 全字段去重保持", len(m3["products"]) == 1,
          f"products={len(m3['products'])}")

    # A4 位置产品输出不含内部字段
    check(s, "输出无内部字段",
          all(set(p.keys()) == {
              "product_service_name", "category", "brand", "spec_model",
              "unit_price", "quantity", "total_price"}
              for p in m["products"]), "")

    # A5 模型折叠重复后机械回填恢复全部位置（见 B 节同款机制，跨 5 chunk）
    return m


# ================================================================ B. t20260811

def test_target_file():
    s = "B_target"
    html = read(VAL_DIR / f"{TARGET}.html")
    parsed = parse_announcement(html, announcement_id=TARGET)
    plan = build_plan(html, announcement_id=TARGET)

    check(s, "mode=structured", plan["mode"] == "structured", plan["mode"])

    units = plan["units_all"]
    pids = [u[2]["position_id"] for u in units]
    check(s, "47 个原始子项全部保留", len(units) == 47,
          f"units={len(units)}")
    check(s, "位置 ID 全唯一", len(set(pids)) == 47,
          f"unique={len(set(pids))}")

    # 重复「定制」「讯方/定制」计数
    pair_counter = Counter((u[2].get("brand"), u[2].get("spec_model"))
                           for u in units)
    n_custom = sum(v for (b, sp), v in pair_counter.items() if sp == "定制")
    check(s, "重复「定制」全部保留（期望11）", n_custom == 11,
          f"定制={n_custom}; 讯方/定制={pair_counter[('讯方', '定制')]}")

    # brand↔spec 与预处理器原始切段的位置对应（offset 单调）
    mech_issues = []
    expected_pairs = []
    for t in parsed["tables"]:
        if t["table_type"] != "product":
            continue
        for r in t["rows"]:
            if r["decision"] != "split":
                continue
            brand_raw = r["raw_columns"].get("品牌", "")
            spec_raw = r["raw_columns"].get("规格型号", "")
            last_b = last_s = -1
            for it in r["sub_items"]:
                b, sp = it.get("brand") or "", it.get("spec_model") or ""
                pb = brand_raw.find(b, last_b + 1 if last_b >= 0 else 0)
                ps = spec_raw.find(sp, last_s + 1 if last_s >= 0 else 0)
                if pb < 0 or ps < 0 or pb <= last_b or ps <= last_s:
                    mech_issues.append(it["position_id"])
                last_b, last_s = pb, ps
                expected_pairs.append((b, sp))
    check(s, "brand↔spec 保持原位置对应（机械单调）", not mech_issues,
          f"issues={mech_issues[:5]}")

    # plan 单元与 parsed 子项多重集一致
    plan_pairs = [(u[2].get("brand"), u[2].get("spec_model")) for u in units]
    check(s, "plan 单元(品牌,规格)多重集==预处理器子项",
          Counter(plan_pairs) == Counter(expected_pairs), "")

    # 分块核验
    pchunks = [c for c in plan["chunks"] if c["kind"] == "products"]
    sizes = [len(c["units"]) for c in pchunks]
    check(s, "每个产品 chunk ≤10 子项", all(n <= MAX_ITEMS_PER_CHUNK for n in sizes),
          f"sizes={sizes}")
    check(s, "产品 chunk 数=5（19→2, 28→3）", len(pchunks) == 5,
          f"chunks={len(pchunks)} sizes={sizes}")
    seen = [u[2]["position_id"] for c in pchunks for u in c["units"]]
    check(s, "子项不跨 chunk（每位置恰好出现一次）",
          len(seen) == 47 and len(set(seen)) == 47
          and Counter(seen) == Counter(pids), "")

    # 同一 chunk 内 position 序号连续（未在行中间切断）
    contiguous_ok = True
    for c in pchunks:
        ids = [u[2]["position_id"] for u in c["units"]]
        rows = {re.match(r"(t\d+-r\d+)-s(\d+)", i).group(1) for i in ids}
        if len(rows) != 1:
            contiguous_ok = False
    check(s, "每个 chunk 只含同一表行的子项", contiguous_ok, "")

    # 宗/包/采购标的上下文随块携带
    ctx_ok = all(("【宗/包】" in c["text"] and "【采购标的】" in c["text"]
                  and "位置：" in c["text"]) for c in pchunks)
    targets = {u[2].get("target") for u in units}
    check(s, "每块携带宗/包、品目、采购标的与位置标记",
          ctx_ok and targets == {"AIGC", "人工智能与数字电路"},
          f"targets={targets}")

    # 已结构化产品表的畸形并行串不出现在任何 chunk（产品块/context）
    all_text = "\n".join(c["text"] for c in plan["chunks"])
    context_text = "\n".join(c["text"] for c in plan["chunks"]
                             if c["kind"] == "context")
    malformed = ["软通、飞凌、华为", "XF-AIBOX；XF-shrek"]
    leak = [m for m in malformed if m in context_text]
    check(s, "畸形并行串已从 context 移除", not leak, f"leak={leak}")
    # context 保留项目级关键信息
    must_keep = ["山东女子学院人工智能学院实验室建设项目", "山东女子学院",
                 "789.270000"]
    missing = [m for m in must_keep if m not in context_text]
    check(s, "context 保留项目名/采购单位/金额", not missing,
          f"missing={missing}; context_len={len(context_text)}")

    # ---- 对齐回填模拟 ----
    # 模型把每块内完全相同的条目折叠（POC 实测问题）
    payloads = []
    n_model_in = 0
    for ci, c in enumerate(pchunks):
        seen_pair = set()
        prods = []
        for _, _, u in c["units"]:
            pair = (u.get("brand"), u.get("spec_model"))
            if pair in seen_pair:
                continue
            seen_pair.add(pair)
            prods.append(_p(u.get("product_service_name"),
                            u.get("brand"), u.get("spec_model")))
        n_model_in += len(prods)
        r = empty_result("x"); r["products"] = prods
        payloads.append({"result": r,
                         "aligned": align_products(c["units"], prods,
                                                   chunk_tag=str(ci))})
    m = merge_chunk_results(payloads, TARGET, "", TARGET + ".html")
    final_pairs = [(p["brand"], p["spec_model"]) for p in m["products"]]
    check(s, "模型折叠后回填恢复47位置", len(m["products"]) == 47,
          f"模型实际返回去重后={n_model_in} → merge={len(m['products'])}")
    check(s, "回填多重集与原始位置一致",
          Counter(final_pairs) == Counter(expected_pairs), "")
    check(s, "回填后11个定制全保留",
          sum(1 for p in m["products"] if p["spec_model"] == "定制") == 11, "")

    # 乱序返回仍能按位置对齐
    c0 = pchunks[0]
    prods = [_p(u.get("product_service_name"), u.get("brand"),
                u.get("spec_model")) for _, _, u in c0["units"]]
    prods_rev = list(reversed(prods))
    aligned_rev = align_products(c0["units"], prods_rev, chunk_tag="rev")
    pos_pair = {pid: (p["brand"], p["spec_model"])
                for p, pid, src in aligned_rev if src == "model"}
    rev_ok = all(pos_pair.get(u["position_id"])
                 == (u.get("brand"), u.get("spec_model"))
                 for _, _, u in c0["units"])
    check(s, "乱序返回仍按位置正确对齐", rev_ok, "")
    return plan


# ================================================================ C. baseline

def test_baseline_zero_change():
    s = "C_baseline"
    rows = []
    n_legacy = n_struct = 0
    for f in sorted(BASELINE_DIR.glob("*.html")):
        html = read(f)
        plan = build_plan(html, announcement_id=f.stem)
        expect_structured = (f.stem == TARGET)
        ok_mode = (plan["mode"] == "structured") if expect_structured \
            else (plan["mode"] == "legacy")
        if plan["mode"] == "legacy":
            n_legacy += 1
            text = preprocess_text(html)
            expect_chunks = chunk_text(text, MAX_CHUNK_CHARS)
            same_text = plan["legacy_text"] == text
            same_chunks = [c["text"] for c in plan["chunks"]] == expect_chunks
            ok = ok_mode and same_text and same_chunks
        else:
            n_struct += 1
            ok = ok_mode
        rows.append({"announcement_id": f.stem, "mode": plan["mode"],
                     "pass": ok,
                     "chunks": len(plan["chunks"])})
        if not ok:
            check(s, f"{f.stem} 输入行为一致", False,
                  f"mode={plan['mode']}")
    check(s, "29 个无 split baseline 全部 legacy 且文本/分块逐字一致",
          n_legacy == 29 and all(r["pass"] for r in rows
                                 if r["announcement_id"] != TARGET),
          f"legacy={n_legacy}")
    check(s, "t20260811_27115458 唯一走 structured",
          n_struct == 1 and any(r["announcement_id"] == TARGET and r["pass"]
                                for r in rows), "")
    results["sections"]["baseline_files"] = rows


# ================================================================ D. 队列

def test_cohorts():
    s = "D_cohorts"
    prev = json.loads(STRUCT_JSON.read_text(encoding="utf-8"))
    split_ids = [r["announcement_id"] for r in prev["parallel_files"]
                 if r["split_rows"] > 0]
    refused_ids = [r["announcement_id"] for r in prev["parallel_files"]
                   if r["split_rows"] == 0]
    prev_pairs = {r["announcement_id"]: r["aligned_sub_items"]
                  for r in prev["parallel_files"]}

    split_rows = []
    all_ok = True
    for ann in split_ids:
        html = read(VAL_DIR / f"{ann}.html")
        parsed = parse_announcement(html, announcement_id=ann)
        plan = build_plan(html, announcement_id=ann)
        units = plan["units_all"]
        pids = [u[2]["position_id"] for u in units]
        pchunks = [c for c in plan["chunks"] if c["kind"] == "products"]
        sizes = [len(c["units"]) for c in pchunks]

        # 口径：strict 单元 = split 子项（=上轮 aligned_sub_items）
        #       + 同一张含 split 的产品表中的 normal 行（也需送给模型）
        n_split_subs = sum(
            len(r["sub_items"])
            for t in parsed["tables"] if t["table_type"] == "product"
            and any(x["decision"] == "split" for x in t["rows"])
            for r in t["rows"] if r["decision"] == "split")
        n_normal_in_split_tables = sum(
            1 for t in parsed["tables"] if t["table_type"] == "product"
            and any(x["decision"] == "split" for x in t["rows"])
            for r in t["rows"] if r["decision"] == "normal")
        n_strict_expect = n_split_subs + n_normal_in_split_tables
        n_raw = sum(1 for _, _, u in units if not u["strict"])

        # split 子项数必须与上轮结构回归一致（预处理器决策无漂移）
        ok_drift = n_split_subs == prev_pairs[ann]
        ok_n = len(pids) == n_strict_expect + n_raw
        # 位置唯一、每块≤10、不跨块
        ok_chunk = (len(set(pids)) == len(pids)
                    and all(n <= MAX_ITEMS_PER_CHUNK for n in sizes))
        seen = [u[2]["position_id"] for c in pchunks for u in c["units"]]
        ok_once = Counter(seen) == Counter(pids)
        # strict 单元位置带 s 编号，raw 带 raw 编号
        ok_id = all(re.match(r"^t\d+-r\d+-(s\d+|raw)$", pid) for pid in pids)
        ok = (plan["mode"] == "structured" and ok_drift and ok_n
              and ok_chunk and ok_once and ok_id)
        all_ok &= ok
        split_rows.append({"announcement_id": ann, "mode": plan["mode"],
                           "split_sub_items": n_split_subs,
                           "prev_aligned": prev_pairs[ann],
                           "normal_rows_in_split_tables": n_normal_in_split_tables,
                           "raw_units": n_raw,
                           "total_units": len(pids),
                           "product_chunks": len(pchunks), "chunk_sizes": sizes,
                           "pass": ok})
        if not ok:
            check(s, f"{ann} split 安全", False,
                  f"split_subs={n_split_subs} prev={prev_pairs[ann]} "
                  f"normal={n_normal_in_split_tables} sizes={sizes}")
    check(s, f"{len(split_ids)} 个真实并行列表全部安全 split/分块/带位置ID",
          all_ok, f"files={len(split_ids)}")

    # 门拒绝文件：无 split；keep_raw 原文在 legacy 输入中逐字可见
    refused_rows = []
    refused_ok = True
    for ann in refused_ids:
        html = read(VAL_DIR / f"{ann}.html")
        parsed = parse_announcement(html, announcement_id=ann)
        plan = build_plan(html, announcement_id=ann)
        text = preprocess_text(html)
        keep_raw = [(r["raw_columns"].get("品牌", ""),
                     r["raw_columns"].get("规格型号", ""))
                    for t in parsed["tables"] if t["table_type"] == "product"
                    for r in t["rows"] if r["decision"] == "keep_raw"]
        # 原文原样可追溯（去空格后包含）
        norm = lambda x: re.sub(r"\s+", "", x or "")
        nt = norm(text)
        trace_bad = [b[:20] for b, sp in keep_raw
                     if (b and norm(b) not in nt)
                     or (sp and norm(sp) not in nt)]
        ok = (plan["mode"] == "legacy"
              and all(r["decision"] != "split"
                      for t in parsed["tables"] for r in t["rows"])
              and not trace_bad)
        refused_ok &= ok
        refused_rows.append({"announcement_id": ann, "mode": plan["mode"],
                             "keep_raw_rows": len(keep_raw),
                             "pass": ok})
        if not ok:
            check(s, f"{ann} 拒绝/keep_raw", False, f"trace_bad={trace_bad}")
    check(s, f"{len(refused_ids)} 个门拒绝文件：不 split、keep_raw 原文原样通过",
          refused_ok, f"files={len(refused_ids)}")

    results["sections"]["split_files"] = split_rows
    results["sections"]["refused_files"] = refused_rows


# ================================================================ main

def main():
    TABLE_TEST.mkdir(parents=True, exist_ok=True)
    test_merge()
    plan = test_target_file()
    test_baseline_zero_change()
    test_cohorts()

    total = len(results["checks"])
    passed = sum(1 for c in results["checks"] if c["pass"])
    results["summary"] = {
        "total_checks": total,
        "passed": passed,
        "failed": total - passed,
        "all_pass": passed == total,
        "target_stats": plan.get("stats") if plan else None,
    }
    out = TABLE_TEST / "integration_regression_summary.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"集成回归：{passed}/{total} 通过")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
