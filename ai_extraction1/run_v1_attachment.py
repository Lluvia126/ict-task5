"""50 条验证集「HTML + 第一层附件」增强抽取实验。

输入构造：
  - 43 个有附件公告：直接使用 parsed_text/<aid>.txt
    （已含【公告】清洗正文 + 【附件：…】+【表格N】，附件表格为管道分隔二维文本）
  - 7 个无附件公告：【公告】+ HTML 清洗正文（等价于 HTML-only）

主流程完全复用 ai_extraction.pipeline.process_html：
  文本清洗 → html_table_preprocessor → 结构化分块 → 原 V1 Prompt（运行时替换）
  → qwen-vl-max / temperature=0 / json_object → position-aware merge
  （本实验输入为纯文本，预处理器找不到 HTML 表格，实际走 legacy 分块，
    属于"当前正式主流程"对该输入的自然行为，不修改任何正式代码）

输出：data/validation_attachment_test/run_v1_attachment/
  <aid>.json / run_logs/<aid>.log / summary.json / summary.csv
断点续跑：已存在的 <aid>.json 跳过。
"""
import contextlib
import csv
import json
import sys
import time
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL
from ai_extraction.run_validation_50 import CountingExtractor, TeeLog

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
PARSED_TEXT = BASE / "data" / "validation_attachment_test" / "parsed_text"
OUT_DIR = BASE / "data" / "validation_attachment_test" / "run_v1_attachment"
LOG_DIR = OUT_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")


def build_input(aid):
    """统一输入：优先 parsed_text（含附件），否则仅公告。"""
    pt = PARSED_TEXT / f"{aid}.txt"
    if pt.exists():
        return pt.read_text(encoding="utf-8"), "html+attachments"
    html = (VAL_DIR / f"{aid}.html").read_text(encoding="utf-8", errors="ignore")
    return "【公告】\n" + preprocess_text(html), "html_only"


def run_one(aid, extractor, rerun=False):
    out_path = OUT_DIR / f"{aid}.json"
    if out_path.exists() and not rerun:
        return json.loads(out_path.read_text(encoding="utf-8")), False

    text, input_kind = build_input(aid)
    extractor.reset()
    tee = TeeLog(LOG_DIR / f"{aid}.log")
    t0 = time.time()
    status = "success"
    try:
        with contextlib.redirect_stdout(tee):
            rec = process_html(text, aid, extractor, file_id="",
                               source_file=f"{aid}.html")
    except Exception as e:  # noqa: BLE001 - 单公告失败不中断整批
        status = "failed"
        rec = {"announcement_id": aid, "status": "failed",
               "error": f"{type(e).__name__}: {e}", "result": None}
    finally:
        tee.close()
    elapsed = round(time.time() - t0, 2)

    plan = build_plan(text, announcement_id=aid)
    planned_product_chunks = sum(1 for c in plan["chunks"]
                                 if c["kind"] == "product")
    n_products = len((rec.get("result") or {}).get("products", []) or [])

    rec.update({
        "status": status,
        "input_kind": input_kind,
        "mode": "legacy" if plan["mode"] == "legacy" else plan["mode"],
        "input_chars": len(text),
        "products_count": n_products,
        "chunks_total": len(plan["chunks"]),
        "planned_product_chunks": planned_product_chunks,
        "api_calls_attempts": extractor.attempts,
        "api_calls_success": extractor.success,
        "json_errors": extractor.json_errors,
        "api_errors": extractor.api_errors,
        "split_retries": max(0, extractor.product_success
                             - planned_product_chunks),
        "elapsed_seconds": elapsed,
    })
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return rec, True


def main():
    rerun = "--rerun" in sys.argv
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    print(f"[run_v1_attachment] Prompt=V1原模型: {qwen_extractor.QWEN_MODEL}",
          flush=True)

    rows = list(csv.DictReader(
        CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    aids = [r["announcement_id"].strip() for r in rows]
    assert len(aids) == 50

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())

    records = []
    for i, aid in enumerate(aids, 1):
        rec, fresh = run_one(aid, extractor, rerun=rerun)
        records.append(rec)
        tag = "RUN" if fresh else "SKIP"
        print(f"[{i}/50] {tag} {aid} status={rec.get('status')} "
              f"products={rec.get('products_count')} "
              f"chunks={rec.get('chunks_total')} "
              f"api={rec.get('api_calls_success')}/{rec.get('api_calls_attempts')} "
              f"json_err={rec.get('json_errors')} "
              f"elapsed={rec.get('elapsed_seconds')}s", flush=True)

    n_ok = sum(1 for r in records if r.get("status") == "success")
    summary = {
        "experiment": "HTML+第一层附件 vs HTML-only(validation_run_final)",
        "model": "qwen-vl-max",
        "prompt": "V1_PROMPT_ORIGINAL（运行时替换，未修改文件）",
        "total": len(records),
        "success": n_ok,
        "failed": len(records) - n_ok,
        "input_kind": {"html+attachments": sum(1 for r in records
                                               if r.get("input_kind") == "html+attachments"),
                       "html_only": sum(1 for r in records
                                        if r.get("input_kind") == "html_only")},
        "products_total": sum(r.get("products_count", 0) for r in records),
        "chunks_total": sum(r.get("chunks_total", 0) for r in records),
        "api_calls_attempts": sum(r.get("api_calls_attempts", 0) for r in records),
        "api_calls_success": sum(r.get("api_calls_success", 0) for r in records),
        "json_errors": sum(r.get("json_errors", 0) for r in records),
        "api_errors": sum(r.get("api_errors", 0) for r in records),
        "split_retries": sum(r.get("split_retries", 0) for r in records),
        "elapsed_total_s": round(sum(r.get("elapsed_seconds", 0)
                                     for r in records), 1),
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(OUT_DIR / "summary.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "status", "input_kind", "products",
                    "chunks", "api_calls", "json_errors", "split_retries",
                    "elapsed_seconds"])
        for r in records:
            w.writerow([r["announcement_id"], r.get("status"),
                        r.get("input_kind"), r.get("products_count"),
                        r.get("chunks_total"), r.get("api_calls_success"),
                        r.get("json_errors"), r.get("split_retries"),
                        r.get("elapsed_seconds")])

    print("\n===== 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
