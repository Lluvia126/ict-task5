"""50 条验证集正式抽取（机器抽取 + 运行统计，不做准确率计算）。

严格约束：
  - 直接使用正式主流程 ai_extraction.pipeline.process_html，不另写抽取逻辑
  - 运行时临时替换为原 V1 Prompt（run_v1_control.V1_PROMPT_ORIGINAL）
  - qwen-vl-max / temperature=0 / json_object / MAX_ITEMS_PER_CHUNK /
    失败沿子项边界二分恢复，全部沿用当前正式配置
  - 不修改 Prompt / qwen_extractor / html_table_preprocessor / pipeline
  - 不复制/移动/修改验证集 CSV（直接读绝对路径）
  - 不计算 P/R/F1

输出：
  data/validation_run_final/<aid>.json          每公告 record（含运行元数据）
  data/validation_run_final/run_logs/<aid>.log  原始调用日志
  data/validation_run_final/summary.json
  data/validation_run_final/summary.csv

断点续跑：已存在的 <aid>.json 默认跳过（不重复调用 Qwen），--rerun 强制重跑。
"""
import argparse
import contextlib
import csv
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.run_v1_control import V1_PROMPT_ORIGINAL

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
OUT_DIR = BASE / "data" / "validation_run_final"
PRED_DIR = OUT_DIR
LOG_DIR = OUT_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")

PRODUCT_CHUNK_MARKER = "【子项"
UNCLOSED_PAREN = re.compile(r"[（(][^)）]*$")
LIST_SEP = re.compile(r"[、；;]")
ATTACH_MARKERS = ["详见附件", "详见投标文件", "详见报价表", "详见格式文件",
                  "产品明细见附件", "见附件"]


# ------------------------------------------------------------ CSV（只读）

def read_validation_csv():
    raw = CSV_PATH.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(text.splitlines()))
    out = {}
    for r in rows:
        aid = (r.get("announcement_id") or "").strip()
        if aid:
            out[aid] = {k: (v or "").strip() for k, v in r.items()}
    return out


# ------------------------------------------------------------ 调用计数包装器

class CountingExtractor:
    """不改动 qwen_extractor 的前提下，在运行器侧统计每次 extract() 尝试。

    pipeline 只调用 extractor.extract(...)。
    """

    def __init__(self, real):
        self.real = real
        self.reset()

    def reset(self):
        self.attempts = 0
        self.success = 0
        self.api_errors = 0
        self.json_errors = 0
        self.err_401 = 0
        self.err_403 = 0
        self.product_attempts = 0
        self.product_success = 0

    def extract(self, text, announcement_id, file_id="", source_file=""):
        self.attempts += 1
        is_product = PRODUCT_CHUNK_MARKER in (text or "")
        if is_product:
            self.product_attempts += 1
        try:
            r = self.real.extract(text, announcement_id=announcement_id,
                                  file_id=file_id, source_file=source_file)
            self.success += 1
            if is_product:
                self.product_success += 1
            return r
        except Exception as e:  # noqa: BLE001 - 只做分类计数后原样抛出
            msg = str(e)
            if "JSON解析失败" in msg or "JSON 解析失败" in msg:
                self.json_errors += 1
            elif "API调用失败" in msg:
                self.api_errors += 1
                if "401" in msg:
                    self.err_401 += 1
                if "403" in msg:
                    self.err_403 += 1
            raise


class TeeLog:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def write(self, s):
        return self.f.write(s)

    def flush(self):
        self.f.flush()

    def close(self):
        self.f.close()


# ------------------------------------------------------------ 附件标记

def attachment_flags(html, csv_meta):
    reasons = []
    nr = (csv_meta.get("need_review") or "").strip()
    if nr and nr not in ("否", "false", "False", "0"):
        reasons.append(f"CSV need_review={nr}")
    csv_reason = csv_meta.get("reason") or ""
    if csv_reason:
        reasons.append(f"CSV reason={csv_reason}")
    hit = [m for m in ATTACH_MARKERS if m in html]
    if hit:
        reasons.append("HTML标记=" + "|".join(sorted(set(hit))))
    return bool(reasons), reasons, sorted(set(hit))


# ------------------------------------------------------------ 结构质量（无Gold，仅机器信号）

def structure_check(rec, html):
    """structured 记录的机器可检结构信号。无 Gold，误拆/误合并仅为疑似信号。"""
    plan = build_plan(html, announcement_id=rec["announcement_id"])
    if plan["mode"] != "structured":
        return None

    units = plan["units_all"]
    strict = [u for _, _, u in units if u["strict"]]
    expected = {u["position_id"]: (u.get("brand"), u.get("spec_model"))
                for u in strict}
    kind_of = {u["position_id"]: u["kind"] for _, _, u in units}

    trace = [t for t in rec.get("position_trace", [])
             if t["source"] in ("model", "structural")]
    got = {t["position"]: (t.get("brand"), t.get("spec_model"), t["source"])
           for t in trace}

    products = (rec.get("result", {}) or {}).get("products", [])

    # 截断：型号未闭合括号
    truncated = [str(p.get("spec_model")) for p in products
                 if p.get("spec_model")
                 and UNCLOSED_PAREN.search(str(p["spec_model"]))]

    # 重复项丢失：期望 (品牌,型号) 多重集 vs 最终多重集
    exp_ms = Counter(expected.values())
    got_ms = Counter((b, s) for b, s, _ in got.values())
    dup_lost, missing_pairs = 0, []
    for pair, n in exp_ms.items():
        gap = n - got_ms.get(pair, 0)
        if gap > 0:
            dup_lost += gap
            missing_pairs.append({"brand": pair[0], "spec_model": pair[1],
                                  "missing": gap})

    # 错位：本位置拿到的对≠本位置期望，却等于其他位置期望
    owner = {}
    for pid, pair in expected.items():
        owner.setdefault(pair, []).append(pid)
    misaligned = []
    for pid, (pair, src) in ((p, (got[p][:2], got[p][2])) for p in got):
        owners = owner.get(pair, [])
        if pid not in owners and owners:
            misaligned.append({"position": pid, "got": list(pair),
                               "belongs_to": owners})

    # 误拆（疑似信号）：split 位置被机械回填（模型未按拆分返回该位置）
    backfill_split = [t["position"] for t in trace
                      if t["source"] == "structural"
                      and kind_of.get(t["position"]) == "split"]

    # 误合并（疑似信号）：模型在结构位置返回多段品牌/多段型号（分隔符≥2段）
    false_merge = []
    for t in trace:
        if t["source"] != "model":
            continue
        bsegs = [x for x in LIST_SEP.split(str(t.get("brand") or "")) if x.strip()]
        ssegs = [x for x in LIST_SEP.split(str(t.get("spec_model") or "")) if x.strip()]
        if len(bsegs) >= 2 or len(ssegs) >= 2:
            false_merge.append({"position": t["position"],
                                "brand": t.get("brand"),
                                "spec_model": t.get("spec_model")})

    return {
        "expected_strict_positions": len(expected),
        "returned_positions": len(got),
        "truncated_specs": len(truncated),
        "truncated_examples": truncated[:5],
        "misaligned_positions": len(misaligned),
        "misaligned_examples": misaligned[:10],
        "duplicate_pairs_lost": dup_lost,
        "missing_pairs": missing_pairs[:10],
        "suspect_false_split_backfill": len(backfill_split),
        "suspect_false_split_positions": backfill_split[:10],
        "suspect_false_merge_positions": len(false_merge),
        "suspect_false_merge_examples": false_merge[:10],
        "model_bound": rec.get("alignment_stats", {}).get("model_bound"),
        "structural_backfill": rec.get("alignment_stats", {}).get(
            "structural_backfill"),
        "raw_units_in_plan": plan["stats"].get("raw_units"),
    }


def classify_mode(plan):
    """legacy / structured / keep_raw。

    structured：plan 含 split；
    keep_raw：无 split 但产品表存在 keep_raw 行（预处理器拒绝猜测）；
    其余 legacy。
    """
    if plan["mode"] == "structured":
        return "structured"
    if (plan["stats"].get("split_rows", 0) == 0
            and plan["stats"].get("keep_raw_rows", 0) > 0):
        return "keep_raw"
    return "legacy"


# ------------------------------------------------------------ 单公告

def run_one(aid, csv_meta, extractor, rerun=False):
    out_path = PRED_DIR / f"{aid}.json"
    if out_path.exists() and not rerun:
        return json.loads(out_path.read_text(encoding="utf-8")), False

    html_path = VAL_DIR / f"{aid}.html"
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    need, reasons, markers = attachment_flags(html, csv_meta)

    extractor.reset()
    tee = TeeLog(LOG_DIR / f"{aid}.log")
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(tee):
            rec = process_html(html, aid, extractor, file_id="",
                               source_file=html_path.name)
    finally:
        tee.close()
    elapsed = round(time.time() - t0, 2)

    # 计划侧统计（离线重建，不产生 API 调用）
    plan = build_plan(html, announcement_id=aid)
    planned_product_chunks = sum(1 for c in plan["chunks"]
                                 if c["kind"] == "products")
    successful_product_payloads = extractor.product_success
    # 无终态失败时：每次成功的沿子项边界二分使产品 payload 净增 1
    split_retries = max(0, successful_product_payloads
                        - planned_product_chunks)
    missing_product_roots = max(0, planned_product_chunks
                                - successful_product_payloads)

    rec.update({
        "source_file": html_path.name,
        "actual_http_attempts": extractor.attempts,
        "api_error_count": extractor.api_errors,
        "api_error_401": extractor.err_401,
        "api_error_403": extractor.err_403,
        "json_parse_failures": extractor.json_errors,
        "planned_product_chunks": planned_product_chunks,
        "split_retries": split_retries,
        "missing_product_roots": missing_product_roots,
        "run_elapsed_seconds": elapsed,
        "needs_attachment_review": need,
        "attachment_review_reason": reasons,
        "attachment_markers_in_html": markers,
        "csv_meta": csv_meta,
        "classified_mode": classify_mode(plan),
    })
    if plan["mode"] == "structured":
        rec["structure_check"] = structure_check(rec, html)

    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return rec, True


# ------------------------------------------------------------ 汇总

def pct(values, q):
    if not values:
        return 0
    xs = sorted(values)
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return round(xs[f], 2)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 2)


def aggregate(records):
    rows = []
    total = len(records)
    success = sum(1 for r in records if r.get("status") == "success")
    failed_ids = [r["announcement_id"] for r in records
                  if r.get("status") != "success"]
    mode_counter = Counter(r.get("classified_mode") for r in records)
    products_total = sum(
        len((r.get("result", {}) or {}).get("products", [])) for r in records)
    elapsed = [r.get("run_elapsed_seconds") or r.get("elapsed_seconds") or 0
               for r in records]

    structured = [r for r in records if r.get("classified_mode") == "structured"]
    sc_rows = [r.get("structure_check") or {} for r in structured]
    struct_agg = {
        "structured_files": len(structured),
        "files_with_truncation": sum(1 for s in sc_rows
                                     if s.get("truncated_specs")),
        "files_with_misalignment": sum(1 for s in sc_rows
                                       if s.get("misaligned_positions")),
        "files_with_duplicate_loss": sum(1 for s in sc_rows
                                         if s.get("duplicate_pairs_lost")),
        "files_with_suspect_false_split": sum(
            1 for s in sc_rows if s.get("suspect_false_split_backfill")),
        "files_with_suspect_false_merge": sum(
            1 for s in sc_rows if s.get("suspect_false_merge_positions")),
        "truncated_specs_total": sum(s.get("truncated_specs", 0)
                                     for s in sc_rows),
        "misaligned_positions_total": sum(s.get("misaligned_positions", 0)
                                          for s in sc_rows),
        "duplicate_pairs_lost_total": sum(s.get("duplicate_pairs_lost", 0)
                                          for s in sc_rows),
        "suspect_false_split_total": sum(
            s.get("suspect_false_split_backfill", 0) for s in sc_rows),
        "suspect_false_merge_total": sum(
            s.get("suspect_false_merge_positions", 0) for s in sc_rows),
        "note": "无完整Gold：误拆/误合并为机器疑似信号，待人工复核确认",
    }

    # CSV 交叉检查
    need_ids = [r["announcement_id"] for r in records
                if (r.get("csv_meta", {}).get("need_review") or "").strip()
                not in ("", "否", "false", "False", "0")]
    attach_flagged = [r["announcement_id"] for r in records
                      if r.get("needs_attachment_review")]
    csv_attach = [r["announcement_id"] for r in records
                  if "附件" in (r.get("csv_meta", {}).get("bucket", "")
                               + r.get("csv_meta", {}).get("reason", ""))]
    cross = {
        "csv_need_review_count": len(need_ids),
        "csv_need_review_success": sum(
            1 for r in records if r["announcement_id"] in need_ids
            and r.get("status") == "success"),
        "csv_marked_attachment_count": len(csv_attach),
        "needs_attachment_review_count": len(attach_flagged),
        "csv_attachment_flagged_count": len(set(csv_attach)
                                            & set(attach_flagged)),
        "html_only_attachment_count": len(
            set(attach_flagged) - set(need_ids)),
        "bucket_distribution": dict(Counter(
            r.get("csv_meta", {}).get("bucket", "") for r in records)),
    }

    summary = {
        "note": "50条验证集机器抽取+运行统计；非完整Gold，不计算正式P/R/F1",
        "model": "qwen-vl-max",
        "prompt": "V1_PROMPT_ORIGINAL（运行时替换，未修改文件）",
        "total_files": total,
        "success": success,
        "failed": total - success,
        "failed_ids": failed_ids,
        "api_error_total": sum(r.get("api_error_count", 0) for r in records),
        "api_error_401_total": sum(r.get("api_error_401", 0) for r in records),
        "api_error_403_total": sum(r.get("api_error_403", 0) for r in records),
        "json_parse_failures_total": sum(
            r.get("json_parse_failures", 0) for r in records),
        "split_retries_total": sum(r.get("split_retries", 0) for r in records),
        "files_with_split_retry": sum(
            1 for r in records if r.get("split_retries")),
        "chunks_total": sum(r.get("chunks_count", 0) for r in records),
        "api_calls_attempts_total": sum(
            r.get("actual_http_attempts", 0) for r in records),
        "api_calls_success_total": sum(
            r.get("api_calls", 0) for r in records),
        "products_total": products_total,
        "avg_products_per_file": round(products_total / total, 2) if total else 0,
        "total_elapsed_seconds": round(sum(elapsed), 2),
        "avg_elapsed_seconds": round(statistics.mean(elapsed), 2) if elapsed else 0,
        "p50_elapsed_seconds": pct(elapsed, 0.50),
        "p95_elapsed_seconds": pct(elapsed, 0.95),
        "mode_legacy": mode_counter.get("legacy", 0),
        "mode_structured": mode_counter.get("structured", 0),
        "mode_keep_raw": mode_counter.get("keep_raw", 0),
        "needs_attachment_review": len(attach_flagged),
        "structure": struct_agg,
        "csv_cross_check": cross,
    }

    cols = ["announcement_id", "source_file", "status", "classified_mode",
            "products", "chunks", "api_calls", "actual_http_attempts",
            "api_errors", "err_401", "err_403", "json_errors",
            "split_retries", "elapsed_seconds", "needs_attachment_review",
            "truncated", "misaligned", "duplicate_lost",
            "suspect_false_split", "suspect_false_merge",
            "bucket", "need_review"]
    for r in records:
        s = r.get("structure_check") or {}
        rows.append({
            "announcement_id": r["announcement_id"],
            "source_file": r.get("source_file"),
            "status": r.get("status"),
            "classified_mode": r.get("classified_mode"),
            "products": len((r.get("result", {}) or {}).get("products", [])),
            "chunks": r.get("chunks_count"),
            "api_calls": r.get("api_calls"),
            "actual_http_attempts": r.get("actual_http_attempts"),
            "api_errors": r.get("api_error_count", 0),
            "err_401": r.get("api_error_401", 0),
            "err_403": r.get("api_error_403", 0),
            "json_errors": r.get("json_parse_failures", 0),
            "split_retries": r.get("split_retries", 0),
            "elapsed_seconds": r.get("run_elapsed_seconds")
                              or r.get("elapsed_seconds"),
            "needs_attachment_review": r.get("needs_attachment_review"),
            "truncated": s.get("truncated_specs", ""),
            "misaligned": s.get("misaligned_positions", ""),
            "duplicate_lost": s.get("duplicate_pairs_lost", ""),
            "suspect_false_split": s.get("suspect_false_split_backfill", ""),
            "suspect_false_merge": s.get("suspect_false_merge_positions", ""),
            "bucket": r.get("csv_meta", {}).get("bucket", ""),
            "need_review": r.get("csv_meta", {}).get("need_review", ""),
        })
    return summary, rows, cols


def write_outputs(summary, rows, cols):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(OUT_DIR / "summary.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def print_report(s):
    st = s["structure"]
    print("\n" + "=" * 60)
    print("===== 50条验证集正式抽取 =====")
    print(f"总数：{s['total_files']}")
    print(f"成功：{s['success']}")
    print(f"失败：{s['failed']}  {s['failed_ids'] if s['failed_ids'] else ''}")
    print(f"API错误：{s['api_error_total']}")
    print(f"401：{s['api_error_401_total']}")
    print(f"403：{s['api_error_403_total']}")
    print(f"JSON错误：{s['json_parse_failures_total']}")
    print(f"二分恢复：{s['split_retries_total']}"
          f"（涉及 {s['files_with_split_retry']} 个公告）")
    print(f"products总数：{s['products_total']}")
    print(f"平均products/文件：{s['avg_products_per_file']}")
    print(f"chunk总数：{s['chunks_total']}")
    print(f"Qwen调用尝试总数：{s['api_calls_attempts_total']}"
          f"（成功 {s['api_calls_success_total']}）")
    print(f"总耗时：{s['total_elapsed_seconds']}s")
    print(f"平均耗时：{s['avg_elapsed_seconds']}s")
    print(f"P50：{s['p50_elapsed_seconds']}s")
    print(f"P95：{s['p95_elapsed_seconds']}s")
    print(f"legacy：{s['mode_legacy']}")
    print(f"structured：{s['mode_structured']}")
    print(f"keep_raw：{s['mode_keep_raw']}")
    print(f"needs_attachment_review：{s['needs_attachment_review']}")
    print("结构异常（structured 文件机器信号）：")
    print(f"  截断：{st['truncated_specs_total']}（{st['files_with_truncation']} 文件）")
    print(f"  错位：{st['misaligned_positions_total']}（{st['files_with_misalignment']} 文件）")
    print(f"  重复丢失：{st['duplicate_pairs_lost_total']}（{st['files_with_duplicate_loss']} 文件）")
    print(f"  误拆(疑似)：{st['suspect_false_split_total']}（{st['files_with_suspect_false_split']} 文件）")
    print(f"  误合并(疑似)：{st['suspect_false_merge_total']}（{st['files_with_suspect_false_merge']} 文件）")
    c = s["csv_cross_check"]
    print("CSV 交叉检查：")
    print(f"  need_review=true：{c['csv_need_review_count']}，"
          f"其中AI成功：{c['csv_need_review_success']}")
    print(f"  CSV标记见附件：{c['csv_marked_attachment_count']}，"
          f"被置needs_attachment_review：{c['csv_attachment_flagged_count']}")
    print(f"  输出目录：{OUT_DIR}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true",
                    help="已存在结果也强制重跑")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="不调用 Qwen，只用已有 JSON 重新汇总")
    args = ap.parse_args()

    csv_meta = read_validation_csv()
    assert len(csv_meta) == 50, f"CSV 记录数={len(csv_meta)}，期望50"
    aids = sorted(csv_meta)
    missing_html = [a for a in aids if not (VAL_DIR / f"{a}.html").exists()]
    assert not missing_html, f"HTML 缺失：{missing_html}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.aggregate_only:
        qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
        extractor = CountingExtractor(qwen_extractor.QwenExtractor())
        print("模型: qwen-vl-max | 原V1 Prompt | temp=0 | 正式 pipeline")

    for i, aid in enumerate(aids, 1):
        if args.aggregate_only:
            break
        rec, ran = run_one(aid, csv_meta[aid], extractor, rerun=args.rerun)
        tag = "运行" if ran else "跳过(已有)"
        n = len((rec.get("result", {}) or {}).get("products", []))
        print(f"[{i:>2}/50] {tag} {aid} mode={rec.get('classified_mode')} "
              f"status={rec.get('status')} products={n} "
              f"json_err={rec.get('json_parse_failures')} "
              f"split_retries={rec.get('split_retries')}")

    records = []
    for aid in aids:
        p = PRED_DIR / f"{aid}.json"
        if p.exists():
            r = json.loads(p.read_text(encoding="utf-8"))
            r.setdefault("csv_meta", csv_meta[aid])
            records.append(r)
        else:
            print("缺少结果文件：", aid)
    summary, rows, cols = aggregate(records)
    write_outputs(summary, rows, cols)
    print_report(summary)


if __name__ == "__main__":
    main()
