"""单文件真实 Qwen 验收：正式接入后的主流程 + 原始 V1 Prompt。

仅运行 t20260811_27115458：
  - ai_extraction.pipeline.process_html（legacy/structured 自动门控）
  - 原始 V1 Prompt（复用 run_v1_control.V1_PROMPT_ORIGINAL，逐字不另抄）
  - 当前 qwen-vl-max，temperature=0，json_object（调用参数不变）
  - 输出独立目录 data/table_test/integration_acceptance_v1/，不覆盖旧 V1/V2/POC

验收目标（结构机械可验，不靠主观）：
  品牌串入产品名称 / 品牌↔型号错位 / 型号截断 / 47 子项保留 /
  重复子项保留 / 每 chunk ≤10 / 所有 chunk 成功
"""
import json
import re
import time
from collections import Counter
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL

BASE = Path(__file__).parent
SRC = BASE / "data" / "validation" / "t20260811_27115458.html"
ANN = "t20260811_27115458"
OUT_DIR = BASE / "data" / "table_test" / "integration_acceptance_v1"

UNCLOSED_PAREN = re.compile(r"[（(][^)）]*$")
LIST_PUNCT = re.compile(r"[、；;\n]")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = SRC.read_text(encoding="utf-8", errors="ignore")
    plan = build_plan(html, announcement_id=ANN)
    expected = {u[2]["position_id"]: u[2] for u in plan["units_all"]}
    exp_pairs = Counter((u[2].get("brand"), u[2].get("spec_model"))
                        for u in plan["units_all"])

    # 原 V1 Prompt（运行时替换，仅本进程）
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    extractor = qwen_extractor.QwenExtractor()
    print("模型:", extractor.model, "| Prompt=原始V1 | temperature=0 | json_object")

    t0 = time.time()
    rec = process_html(html, ANN, extractor, file_id="", source_file=SRC.name)
    wall = round(time.time() - t0, 2)

    (OUT_DIR / f"{ANN}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    result = rec.get("result") or {}
    products = result.get("products", [])
    trace = rec.get("position_trace", [])
    trace_by_pos = {t["position"]: t for t in trace
                    if t["source"] in ("model", "structural")}

    # ---------- 机械验收 ----------
    checks = {}

    # 1) chunk
    pchunks = [c for c in plan["chunks"] if c["kind"] == "products"]
    checks["chunk_each_le_10"] = all(len(c["units"]) <= 10 for c in pchunks)
    checks["chunk_product_count_5"] = len(pchunks) == 5
    checks["chunk_sizes"] = [len(c["units"]) for c in pchunks]
    checks["chunk_planned_total"] = rec["chunks_count"]
    checks["api_calls"] = rec["api_calls"]
    checks["chunk_failures"] = rec["alignment_stats"]["chunk_failures"]
    checks["all_chunks_success"] = rec["status"] in ("success",) and \
        not rec["chunk_errors"]
    checks["status"] = rec["status"]
    checks["chunk_errors"] = rec["chunk_errors"]

    # 2) 子项保留 & 重复保留
    checks["products_total"] = len(products)
    checks["expected_total"] = len(expected)
    checks["all_47_retained"] = len(products) == 47
    pred_pairs = Counter((p.get("brand"), p.get("spec_model")) for p in products)
    checks["pair_multiset_match"] = pred_pairs == exp_pairs
    checks["custom_dingzhi"] = sum(1 for p in products
                                   if p.get("spec_model") == "定制")

    # 3) 品牌↔型号位置错位：模型绑定项必须等于该位置机械对
    misaligned, model_checked = [], 0
    for pid, u in expected.items():
        t = trace_by_pos.get(pid)
        if t and t["source"] == "model":
            model_checked += 1
            if (t["brand"], t["spec_model"]) != \
                    (u.get("brand"), u.get("spec_model")):
                misaligned.append({"position": pid,
                                   "expected": [u.get("brand"),
                                                u.get("spec_model")],
                                   "got": [t["brand"], t["spec_model"]]})
    checks["misaligned_count"] = len(misaligned)
    checks["misaligned_examples"] = misaligned[:10]
    checks["model_aligned_positions"] = model_checked
    checks["structural_backfill"] = rec["alignment_stats"]["structural_backfill"]

    # 4) 型号截断（未闭合括号/异常尾巴）
    truncated = [p.get("spec_model") for p in products
                 if p.get("spec_model")
                 and UNCLOSED_PAREN.search(str(p["spec_model"]))]
    checks["spec_truncated_count"] = len(truncated)
    checks["spec_truncated_examples"] = truncated[:5]

    # 5) 品牌串进产品名称（名称含列表分隔符且超长）
    brand_in_name = [p.get("product_service_name") for p in products
                     if p.get("product_service_name")
                     and len(str(p["product_service_name"])) > 12
                     and LIST_PUNCT.search(str(p["product_service_name"]))]
    # 名称里混入已知品牌串（如把整列品牌拼进名称）
    known_brands = {b for b, _ in exp_pairs if b}
    name_carries_brand_list = [
        p.get("product_service_name") for p in products
        if p.get("product_service_name")
        and sum(1 for b in known_brands if b in str(p["product_service_name"])) >= 3
    ]
    checks["brand_string_in_name_count"] = len(brand_in_name) + \
        len(name_carries_brand_list)
    checks["brand_string_in_name_examples"] = (brand_in_name +
                                               name_carries_brand_list)[:5]

    # 6) 项目级（context 有效性，非本次硬目标）
    checks["project"] = {
        "project_name": result.get("project_name"),
        "project_code": result.get("project_code"),
        "purchaser": result.get("purchaser"),
        "project_amount": result.get("project_amount"),
        "winning_suppliers": result.get("winning_suppliers"),
    }
    checks["elapsed_seconds"] = wall
    checks["merge_stats"] = rec.get("merge_stats")

    (OUT_DIR / "acceptance_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 控制台报告 ----------
    def line(label, ok, detail=""):
        print(f"[{'PASS' if ok else 'FAIL'}] {label}"
              + (f" — {detail}" if detail else ""))

    print("\n" + "=" * 64)
    print(f"验收 {ANN}（正式主流程 + 原 V1 Prompt + {extractor.model}）")
    print("=" * 64)
    line("所有 chunk 成功", checks["all_chunks_success"],
         f"status={rec['status']} errors={rec['chunk_errors']}")
    line("每个产品 chunk ≤10 子项", checks["chunk_each_le_10"],
         f"sizes={checks['chunk_sizes']}")
    line("47 个子项全部保留", checks["all_47_retained"],
         f"products={len(products)}")
    line("(品牌,型号)多重集与原始位置一致", checks["pair_multiset_match"])
    line("重复「定制」保留（期望11）", checks["custom_dingzhi"] == 11,
         f"got={checks['custom_dingzhi']}")
    line("无品牌↔型号位置错位", checks["misaligned_count"] == 0,
         f"misaligned={checks['misaligned_count']} "
         f"(模型对齐 {model_checked} 位置, 机械回填 "
         f"{checks['structural_backfill']})")
    line("无型号截断", checks["spec_truncated_count"] == 0,
         f"truncated={checks['spec_truncated_examples']}")
    line("无品牌串进入产品名称", checks["brand_string_in_name_count"] == 0,
         f"examples={checks['brand_string_in_name_examples']}")
    print(f"\n产品块={len(pchunks)} 块（{checks['chunk_sizes']}）；"
          f"计划总chunk={rec['chunks_count']}；实际API调用={rec['api_calls']}；"
          f"耗时={wall}s")
    print("项目级抽取:", json.dumps(checks["project"], ensure_ascii=False))
    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
