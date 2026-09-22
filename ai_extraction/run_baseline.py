"""第一轮官方数据 Qwen Baseline 运行脚本。

输入: ai_extraction/data/baseline_input/*.html (30 个官方 HTML)
输出: ai_extraction/data/baseline_output/

严格复用现有模块:
  - preprocess_text   (HTML 清洗)
  - chunk_text        (分块)
  - QwenExtractor     (抽取)
  - 当前 Prompt / Qwen 配置

不修改 ai_extraction 核心业务逻辑, 仅新增本脚本。
单文件失败不影响整体。
"""
import json
import os
import time
from pathlib import Path

from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.chunker import chunk_text
from ai_extraction.extractors.qwen_extractor import QwenExtractor

INPUT_DIR = Path(__file__).parent / "data" / "baseline_input"
OUTPUT_DIR = Path(__file__).parent / "data" / "baseline_output"


def _merge_chunk_results(chunk_results, announcement_id, file_id, source_file):
    """合并多个 chunk 的抽取结果。

    - 项目级标量字段: 取第一个非 null
    - 列表字段: 合并去重 (保持顺序)
    - products: 合并 (按全字段去重)
    """
    from ai_extraction.config import empty_result
    merged = empty_result(announcement_id, file_id, source_file)

    win_set, bid_set = [], []
    products = []
    for r in chunk_results:
        if r is None:
            continue
        # 项目级标量
        for f in ("project_name", "project_code", "purchaser", "project_amount"):
            if merged[f] is None and r.get(f) is not None:
                merged[f] = r[f]
        # 列表字段去重
        for s in r.get("winning_suppliers", []):
            if s not in win_set:
                win_set.append(s)
        for s in r.get("bid_participants", []):
            if s not in bid_set:
                bid_set.append(s)
        # products 按全字段去重
        for p in r.get("products", []):
            if p not in products:
                products.append(p)

    merged["winning_suppliers"] = win_set
    merged["bid_participants"] = bid_set
    merged["products"] = products
    return merged


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
    """处理单个 HTML 文件, 返回记录 dict。"""
    file_name = file_path.name
    announcement_id = file_path.stem  # e.g. t20260812_27123466
    source_file = file_name

    record = {
        "file_name": file_name,
        "announcement_id": announcement_id,
        "source_file": source_file,
        "chunks_count": 0,
        "elapsed_seconds": 0.0,
        "status": "success",
        "error": None,
        "result": None,
        "raw_responses": [],
        "chunk_errors": [],
    }

    t0 = time.time()
    try:
        # 1) 读文件
        raw_html = file_path.read_text(encoding="utf-8", errors="ignore")
        # 2) 清洗
        text = preprocess_text(raw_html)
        if not text.strip():
            record["status"] = "failed"
            record["error"] = "清洗后文本为空"
            return record
        # 3) 分块
        chunks = chunk_text(text)
        record["chunks_count"] = len(chunks)

        # 4) 逐块抽取
        chunk_results = []
        for idx, chunk in enumerate(chunks):
            try:
                result = extractor.extract(
                    chunk,
                    announcement_id=f"{announcement_id}_c{idx}",
                    file_id="",
                    source_file=source_file,
                )
                chunk_results.append(result)
                record["raw_responses"].append(result)
            except Exception as e:
                record["chunk_errors"].append(f"chunk[{idx}]: {e}")

        # 5) 合并
        if chunk_results:
            record["result"] = _merge_chunk_results(
                chunk_results, announcement_id, "", source_file
            )
        else:
            record["status"] = "failed"
            record["error"] = "所有 chunk 抽取均失败" if record["chunk_errors"] else "无有效 chunk"
    except Exception as e:
        record["status"] = "failed"
        record["error"] = f"{type(e).__name__}: {e}"
    finally:
        record["elapsed_seconds"] = round(time.time() - t0, 2)

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
