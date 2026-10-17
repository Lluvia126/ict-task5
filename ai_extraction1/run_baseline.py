"""第一轮官方数据 Qwen Baseline 运行脚本。

输入: ai_extraction/data/baseline_input/*.html (30 个官方 HTML)
输出: ai_extraction/data/baseline_output/

正式接入 HTML 表格预处理后，主流程统一走 ai_extraction.pipeline：
  - 无 split 证据的公告：legacy 路径，与历史 V1 逐字一致
    （preprocess_text 清洗 + chunk_text 分块 + 全字段去重 merge）
  - 含 split 行的公告：structured 路径（结构位置 ID + ≤10 子项/块 +
    位置优先 merge）
  - 当前 Prompt / Qwen 配置不变

单文件失败不影响整体。
"""
import json
from pathlib import Path

from ai_extraction.extractors.qwen_extractor import QwenExtractor
from ai_extraction.pipeline import legacy_merge, process_html
from ai_extraction.preprocessing.cleaner import preprocess_text

INPUT_DIR = Path(__file__).parent / "data" / "baseline_input"
OUTPUT_DIR = Path(__file__).parent / "data" / "baseline_output"


def _merge_chunk_results(chunk_results, announcement_id, file_id, source_file):
    """历史合并逻辑（products 按全字段去重）。

    保留函数名供 run_ab_test 等既有实验脚本导入；实际委托
    pipeline.legacy_merge，行为与历史实现完全一致。
    """
    return legacy_merge(chunk_results, announcement_id, file_id, source_file)


def _is_empty_result(result):
    """判断结果是否为空 (项目级全 null 且 products 为空)。"""
    if result["products"]:
        return False
    for f in ("project_name", "project_code", "purchaser", "project_amount",
              "winning_suppliers", "bid_participants"):
        if result[f] not in (None, [], ""):
            return False
    return True


def process_file(file_path, extractor):
    """处理单个 HTML 文件, 返回记录 dict。

    统一委托 ai_extraction.pipeline.process_html：
    legacy 路径（无 split）与历史行为逐字一致；含 split 的公告走
    结构化路径。本函数仅保留「清洗后文本为空」的前置短路与
    file_name 字段，保证输出记录结构不变。
    """
    file_name = file_path.name
    announcement_id = file_path.stem  # e.g. t20260812_27123466
    source_file = file_name

    try:
        raw_html = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {
            "file_name": file_name,
            "announcement_id": announcement_id,
            "source_file": source_file,
            "mode": None,
            "chunks_count": 0,
            "api_calls": 0,
            "elapsed_seconds": 0.0,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "result": None,
            "raw_responses": [],
            "chunk_errors": [],
            "plan_stats": None,
            "alignment_stats": None,
        }

    if not preprocess_text(raw_html).strip():
        return {
            "file_name": file_name,
            "announcement_id": announcement_id,
            "source_file": source_file,
            "mode": None,
            "chunks_count": 0,
            "api_calls": 0,
            "elapsed_seconds": 0.0,
            "status": "failed",
            "error": "清洗后文本为空",
            "result": None,
            "raw_responses": [],
            "chunk_errors": [],
            "plan_stats": None,
            "alignment_stats": None,
        }

    record = process_html(raw_html, announcement_id, extractor,
                          file_id="", source_file=source_file)
    record["file_name"] = file_name
    return record


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    html_files = sorted(INPUT_DIR.glob("*.html"))
    print(f"找到 {len(html_files)} 个 HTML 文件")

    extractor = QwenExtractor()

    records = []
    total_t0 = time.time()
    for i, f in enumerate(html_files, 1):
        print(f"\n[{i}/{len(html_files)}] 处理: {f.name}")
        rec = process_file(f, extractor)
        # 保存单文件结果
        out_path = OUTPUT_DIR / f"{rec['announcement_id']}.json"
        with open(out_path, "w", encoding="utf-8") as out_f:
            json.dump(rec, out_f, ensure_ascii=False, indent=2)
        # 精简后存入 records (不存 raw_responses, 避免 summary 文件过大)
        slim = {k: v for k, v in rec.items() if k != "raw_responses"}
        records.append(slim)
        print(f"  -> {rec['status']} | {rec['elapsed_seconds']}s | "
              f"chunks={rec['chunks_count']} | "
              f"products={len(rec['result']['products']) if rec['result'] else 0}"
              + (f" | error={rec['error']}" if rec['error'] else ""))

    total_elapsed = round(time.time() - total_t0, 2)

    # 统计
    total = len(records)
    success = sum(1 for r in records if r["status"] == "success" and r["result"])
    failed = total - success
    json_fail = sum(1 for r in records if r["error"] and "JSON" in str(r["error"]))
    # 空结果: 成功但所有字段为空
    empty_count = sum(
        1 for r in records
        if r["status"] == "success" and r["result"] and _is_empty_result(r["result"])
    )

    summary = {
        "total_samples": total,
        "success_count": success,
        "failed_count": failed,
        "json_parse_failures": json_fail,
        "empty_result_count": empty_count,
        "total_elapsed_seconds": total_elapsed,
        "avg_elapsed_seconds": round(total_elapsed / total, 2) if total else 0,
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "per_file": [
            {
                "file_name": r["file_name"],
                "announcement_id": r["announcement_id"],
                "status": r["status"],
                "elapsed_seconds": r["elapsed_seconds"],
                "chunks_count": r["chunks_count"],
                "error": r["error"],
            }
            for r in records
        ],
    }

    summary_path = OUTPUT_DIR / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("Baseline 完成")
    print(f"  总样本: {total}")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")
    print(f"  JSON 解析失败: {json_fail}")
    print(f"  空结果: {empty_count}")
    print(f"  总耗时: {total_elapsed}s")
    print(f"  平均耗时: {summary['avg_elapsed_seconds']}s/文件")
    print(f"  结果目录: {OUTPUT_DIR}")
    print(f"  汇总: {summary_path}")


if __name__ == "__main__":
    main()
