"""Qwen V2 Baseline 运行脚本。

与 V1 (run_baseline.py) 完全相同的流程，仅输出目录不同。
复用 V2 Prompt + qwen_extractor.py（已修改好的）。
"""
import json
import time
from pathlib import Path

import ai_extraction.run_baseline as rb

# 覆盖输出目录
rb.OUTPUT_DIR = Path(__file__).parent / "data" / "baseline_output_v2"


def main():
    OUTPUT_DIR = rb.OUTPUT_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from ai_extraction.preprocessing.cleaner import preprocess_text
    from ai_extraction.preprocessing.chunker import chunk_text
    from ai_extraction.extractors.qwen_extractor import QwenExtractor

    INPUT_DIR = rb.INPUT_DIR
    html_files = sorted(INPUT_DIR.glob("*.html"))
    print(f"找到 {len(html_files)} 个 HTML 文件")
    print(f"输出目录: {OUTPUT_DIR}")

    extractor = QwenExtractor()

    records = []
    total_t0 = time.time()
    for i, f in enumerate(html_files, 1):
        print(f"\n[{i}/{len(html_files)}] 处理: {f.name}")
        rec = rb.process_file(f, extractor)
        out_path = OUTPUT_DIR / f"{rec['announcement_id']}.json"
        with open(out_path, "w", encoding="utf-8") as out_f:
            json.dump(rec, out_f, ensure_ascii=False, indent=2)
        slim = {k: v for k, v in rec.items() if k != "raw_responses"}
        records.append(slim)
        n_prod = len(rec["result"]["products"]) if rec["result"] else 0
        print(f"  -> {rec['status']} | {rec['elapsed_seconds']}s | "
              f"chunks={rec['chunks_count']} | products={n_prod}"
              + (f" | error={rec['error']}" if rec["error"] else ""))

    total_elapsed = round(time.time() - total_t0, 2)

    total = len(records)
    success = sum(1 for r in records if r["status"] == "success" and r["result"])
    failed = total - success
    json_fail = sum(1 for r in records if r["error"] and "JSON" in str(r["error"]))
    empty_count = sum(
        1 for r in records
        if r["status"] == "success" and r["result"] and rb._is_empty_result(r["result"])
    )
    total_products = sum(
        len(r["result"]["products"]) for r in records if r["result"]
    )

    summary = {
        "version": "V2",
        "total_samples": total,
        "success_count": success,
        "failed_count": failed,
        "json_parse_failures": json_fail,
        "empty_result_count": empty_count,
        "total_elapsed_seconds": total_elapsed,
        "avg_elapsed_seconds": round(total_elapsed / total, 2) if total else 0,
        "total_products": total_products,
        "avg_products_per_file": round(total_products / total, 2) if total else 0,
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "per_file": [
            {
                "file_name": r["file_name"],
                "announcement_id": r["announcement_id"],
                "status": r["status"],
                "elapsed_seconds": r["elapsed_seconds"],
                "chunks_count": r["chunks_count"],
                "products": len(r["result"]["products"]) if r["result"] else 0,
                "error": r["error"],
            }
            for r in records
        ],
    }

    summary_path = OUTPUT_DIR / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("V2 Baseline 完成")
    print(f"  总样本: {total}")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")
    print(f"  JSON 解析失败: {json_fail}")
    print(f"  空结果: {empty_count}")
    print(f"  总 products: {total_products}")
    print(f"  平均 products/文件: {summary['avg_products_per_file']}")
    print(f"  总耗时: {total_elapsed}s")
    print(f"  平均耗时: {summary['avg_elapsed_seconds']}s/文件")
    print(f"  结果目录: {OUTPUT_DIR}")

    # Gold Set 覆盖检查
    print("\n" + "=" * 60)
    print("Gold Set 覆盖检查")
    gold_csv = Path(__file__).parent / "data" / "gold" / "goldset_manual_check.csv"
    import csv
    with open(gold_csv, encoding="utf-8-sig", newline="") as gf:
        gold_rows = list(csv.reader(gf))
    gold_header = gold_rows[0]
    gold_data = gold_rows[1:]
    aid_col = gold_header.index("announcement_id")
    gold_aids = set(r[aid_col] for r in gold_data)

    v2_aids = set(r["announcement_id"] for r in records)
    input_aids = set(f.stem for f in html_files)

    covered_in_v2 = gold_aids & v2_aids
    covered_in_input = gold_aids & input_aids

    print(f"  Gold 中 announcement_id 数: {len(gold_aids)}")
    print(f"  Gold 中产品记录数: {len(gold_data)}")
    print(f"  在 baseline_input 中找到: {len(covered_in_input)} / {len(gold_aids)}")
    print(f"  在 baseline_output_v2 中找到: {len(covered_in_v2)} / {len(gold_aids)}")

    not_in_input = gold_aids - input_aids
    if not_in_input:
        print(f"  未在 input 中找到的: {not_in_input}")


if __name__ == "__main__":
    main()
