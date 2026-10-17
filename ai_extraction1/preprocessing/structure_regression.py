"""HTML 表格预处理器 —— 全量结构回归测试（不调用 Qwen）。

三部分：
  A. 并行列表队列（画像口径 22 个）：逐文件 parse_announcement，
     核验 split 是否真正触发、等长、对齐子项、机械自检。
  B. rowspan 队列（53 个）：核验二维网格展开（rowspan/colspan 区域、
     源单元格数量、内容保留），以及是否误拆/误删/误复制/误合并。
  C. 30 个 baseline 文件零行为变化：原始 V1 文本输入（cleaner 文本）
     中出现的每个产品表单元格 / split 切段都必须可追溯；
     无 split 行 = 结构未变化，有 split 行 = 仅证据驱动的变化。

输出：
  data/table_test/structure_regression_summary.json
  data/table_test/structure_regression_review.csv
"""
import csv
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

from ai_extraction.preprocessing.html_table_preprocessor import (
    build_grid, cell_text, parse_announcement,
)
from ai_extraction.preprocessing.cleaner import preprocess_text

ROOT = Path(__file__).resolve().parents[1]
VAL_DIR = ROOT / "data" / "validation"
BASELINE_DIR = ROOT / "data" / "baseline_input"
OUT_DIR = ROOT / "data" / "table_test"


# ================================================================ 队列重建

def _split_count(s):
    s = (s or "").strip()
    best = 1 if s else 0
    for d in ("；", ";", "、", "\n"):
        n = len([p for p in s.split(d) if p.strip()])
        if n > best:
            best = n
    return best


def _header(table):
    rows = table.find_all("tr")
    if not rows:
        return []
    return [cell_text(c) for c in rows[0].find_all(["th", "td"])]


def _col(hdr, *keys):
    for i, h in enumerate(hdr):
        if any(k in h for k in keys):
            return i
    return None


def build_cohorts():
    """复刻全量画像口径，返回 (parallel_ids, rowspan_ids)。"""
    parallel, rowspan = [], []
    for f in sorted(VAL_DIR.glob("*.html")):
        soup = BeautifulSoup(f.read_text(encoding="utf-8", errors="ignore"),
                             "html.parser")
        tables = soup.find_all("table")
        is_parallel = False
        is_rowspan = False
        for t in tables:
            if t.find(attrs={"rowspan": True}):
                is_rowspan = True
            hdr = _header(t)
            ci_b = _col(hdr, "品牌")
            ci_s = _col(hdr, "规格型号", "规格/型号", "型号规格")
            if ci_b is not None and ci_s is not None:
                for tr in t.find_all("tr")[1:]:
                    cells = tr.find_all(["th", "td"])
                    if max(ci_b, ci_s) >= len(cells):
                        continue
                    nb = _split_count(cell_text(cells[ci_b]))
                    ns = _split_count(cell_text(cells[ci_s]))
                    if nb >= 2 and nb == ns:
                        is_parallel = True
        if is_parallel:
            parallel.append(f.stem)
        if is_rowspan:
            rowspan.append(f.stem)
    return parallel, rowspan


# ================================================================ 机械自检

def _norm(s):
    return re.sub(r"\s+", "", s or "")


def grid_integrity(table):
    """二维网格完整性检查（rowspan/colspan 展开的纯结构核验）。

    返回 issue 列表，每项 (issue, detail)：
      source_count_mismatch: 源单元格数 != HTML 中 td/th 数
      region_mismatch      : 某源单元格在网格中的覆盖区 != rowspan*colspan 矩形
      span_count_mismatch  : 覆盖位置数 != rowspan*colspan
    """
    issues = []
    grid, ncols, _rs, _cs = build_grid(table)

    # 源元素清单（按 build_grid 的遍历顺序）
    source_elements = []
    for tr in table.find_all("tr"):
        source_elements.extend(tr.find_all(["th", "td"]))

    # origin -> 源 dict
    origins = {}
    for row in grid:
        for d in row:
            if d is not None:
                origins.setdefault(d["origin"], d)

    if len(origins) != len(source_elements):
        issues.append(("source_count_mismatch",
                       f"origins={len(origins)} html_cells={len(source_elements)}"))

    for (r0, c0), d in origins.items():
        rs, cs = d["rowspan"], d["colspan"]
        positions = []
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell is not None and cell["origin"] == (r0, c0):
                    positions.append((r, c))
        if len(positions) != rs * cs:
            issues.append(("span_count_mismatch",
                           f"origin={(r0, c0)} expect={rs * cs} got={len(positions)}"))
            continue
        expect = {(r, c) for r in range(r0, r0 + rs) for c in range(c0, c0 + cs)}
        if set(positions) != expect:
            issues.append(("region_mismatch",
                           f"origin={(r0, c0)} got={sorted(positions)}"))
    return issues


def row_mechanical_checks(trec):
    """对一个解析后产品表的所有行做内容级机械核验。

    返回 (issues, n_aligned_pairs, n_duplicates_preserved)
    issue 字段：
      fabricated      : split 子项 brand/spec 在原单元格中找不到
      order_violation : 子项在原文中的位置非单调递增
      merged_rows     : split 子项数 != 列表段数（发生去重/合并）
      raw_lost        : keep_raw 行原始单元格为空
    """
    issues = []
    n_pairs = n_dup = 0
    for r in trec["rows"]:
        d = r["decision"]
        brand_raw = r["raw_columns"].get("品牌", "")
        spec_raw = r["raw_columns"].get("规格型号", "")
        if d == "split":
            last_b = last_s = -1
            n_b = r.get("brand_count") or 0
            n_s = r.get("spec_count") or 0
            if len(r["sub_items"]) != max(n_b, n_s):
                issues.append((r["row_index"], "merged_rows",
                               f"sub_items={len(r['sub_items'])} parts={max(n_b, n_s)}"))
            for it in r["sub_items"]:
                b = it.get("brand") or ""
                s = it.get("spec_model") or ""
                pb = brand_raw.find(b, last_b + 1 if last_b >= 0 else 0)
                ps = spec_raw.find(s, last_s + 1 if last_s >= 0 else 0)
                if pb < 0 or ps < 0:
                    issues.append((r["row_index"], "fabricated",
                                   f"brand={b!r} spec={s!r}"))
                if pb <= last_b or ps <= last_s:
                    issues.append((r["row_index"], "order_violation",
                                   f"pb={pb} last_b={last_b} ps={ps} last_s={last_s}"))
                last_b, last_s = pb, ps
            n_pairs += len(r["sub_items"])
        elif d == "keep_raw":
            if not brand_raw or not spec_raw:
                issues.append((r["row_index"], "raw_lost",
                               f"brand_empty={not brand_raw} spec_empty={not spec_raw}"))
        # 同位重复统计
        seen = {}
        for it in r["sub_items"]:
            key = (it.get("brand") or "", it.get("spec_model") or "")
            if key in seen:
                n_dup += 1
            seen[key] = seen.get(key, 0) + 1
    return issues, n_pairs, n_dup


# ================================================================ 文件处理

def process_file(ann, check_grid=False):
    """处理单个公告，返回 (file_record, review_rows)。"""
    src = VAL_DIR / f"{ann}.html"
    html = src.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_announcement(html, announcement_id=ann)

    review = []  # (table_index, row_index, decision, issue, original, processed)
    n_split_rows = n_normal_rows = n_keep_raw = 0
    n_pairs = n_dup = 0
    grid_issue_count = 0

    for t in parsed["tables"]:
        if t["table_type"] == "product":
            issues, pairs, dup = row_mechanical_checks(t)
            n_pairs += pairs
            n_dup += dup
            for ri, issue, detail in issues:
                r = next(x for x in t["rows"] if x["row_index"] == ri)
                review.append((
                    t["table_index"], ri, r["decision"], f"{issue}: {detail}",
                    (r["raw_columns"].get("品牌", "") + " ‖ "
                     + r["raw_columns"].get("规格型号", ""))[:500],
                    json.dumps(r["sub_items"], ensure_ascii=False)[:500],
                ))
        if check_grid:
            raw_table = BeautifulSoup(html, "html.parser").find_all("table")[t["table_index"]]
            for issue, detail in grid_integrity(raw_table):
                grid_issue_count += 1
                review.append((t["table_index"], "", t["table_type"],
                               f"{issue}: {detail}", "(grid structure)", ""))

        for r in t["rows"]:
            if r["decision"] == "split":
                n_split_rows += 1
            elif r["decision"] == "normal":
                n_normal_rows += 1
            elif r["decision"] == "keep_raw":
                n_keep_raw += 1

    rec = {
        "announcement_id": ann,
        "n_product_tables": parsed["stats"]["n_product_tables"],
        "n_service_tables": parsed["stats"]["n_service_tables"],
        "split_rows": n_split_rows,
        "normal_rows": n_normal_rows,
        "keep_raw_rows": n_keep_raw,
        "aligned_sub_items": n_pairs,
        "duplicates_preserved": n_dup,
        "grid_issues": grid_issue_count,
        "review_issue_count": len(review),
        "mechanical_ok": len(review) == 0,
    }
    return rec, review


# ================================================================ Baseline 零变化

def baseline_zero_change():
    """30 个 baseline 文件：结构变化与可追溯性检查。"""
    results = []
    review = []
    for f in sorted(BASELINE_DIR.glob("*.html")):
        html = f.read_text(encoding="utf-8", errors="ignore")
        original_text = preprocess_text(html)
        norm_orig = _norm(original_text)
        parsed = parse_announcement(html, announcement_id=f.stem)

        n_split = 0
        traceability_bad = []
        for t in parsed["tables"]:
            if t["table_type"] != "product":
                continue
            for r in t["rows"]:
                if r["decision"] == "split":
                    n_split += 1
                    for it in r["sub_items"]:
                        for piece in (it.get("brand"), it.get("spec_model")):
                            if piece and _norm(piece) not in norm_orig:
                                traceability_bad.append(
                                    f"row{r['row_index']} 切段不可追溯: {piece}")
                # 每个原始产品单元格（去空格后）必须能在原始文本中找到
                for h, v in r["raw_columns"].items():
                    if v and _norm(v) not in norm_orig:
                        traceability_bad.append(
                            f"row{r['row_index']} 列「{h}」单元格不可追溯")

        changed = n_split > 0
        needs_review = bool(traceability_bad)
        results.append({
            "announcement_id": f.stem,
            "file_name": f.name,
            "structure_changed": changed,
            "split_rows": n_split,
            "normal_rows": sum(1 for t in parsed["tables"]
                               for r in t["rows"] if r["decision"] == "normal"),
            "keep_raw_rows": sum(1 for t in parsed["tables"]
                                 for r in t["rows"] if r["decision"] == "keep_raw"),
            "needs_review": needs_review,
            "traceability_issues": traceability_bad[:10],
        })
        if needs_review:
            review.append((f.stem, traceability_bad))
    return results, review


# ================================================================ 主流程

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("重建队列（扫描 validation）...")
    parallel_ids, rowspan_ids = build_cohorts()
    print(f"并行列表队列={len(parallel_ids)}  rowspan队列={len(rowspan_ids)}  "
          f"交集={len(set(parallel_ids) & set(rowspan_ids))}")

    all_review = []  # 统一 review 行

    # ---- A. 并行列表
    parallel_recs = []
    for i, ann in enumerate(parallel_ids, 1):
        rec, review = process_file(ann)
        parallel_recs.append(rec)
        for row in review:
            all_review.append((ann,) + row)
        print(f"[parallel {i}/{len(parallel_ids)}] {ann}: split_rows={rec['split_rows']} "
              f"aligned={rec['aligned_sub_items']} keep_raw={rec['keep_raw_rows']} "
              f"mechanical_ok={rec['mechanical_ok']}")

    # ---- B. rowspan
    rowspan_recs = []
    for i, ann in enumerate(rowspan_ids, 1):
        rec, review = process_file(ann, check_grid=True)
        rowspan_recs.append(rec)
        for row in review:
            all_review.append((ann,) + row)
        print(f"[rowspan {i}/{len(rowspan_ids)}] {ann}: split={rec['split_rows']} "
              f"normal={rec['normal_rows']} keep_raw={rec['keep_raw_rows']} "
              f"grid_issues={rec['grid_issues']} ok={rec['mechanical_ok']}")

    # ---- C. baseline 零变化
    print("\n30 baseline 零行为变化检查...")
    baseline_recs, baseline_review = baseline_zero_change()
    for ann, issues in baseline_review:
        for msg in issues:
            all_review.append((ann, "", "", "baseline", msg, "", ""))

    # ---- 汇总
    union_struct_ids = set(parallel_ids) | set(rowspan_ids)
    union_recs = {r["announcement_id"]: r
                  for r in parallel_recs + rowspan_recs}
    agg = {
        "parallel_total": len(parallel_ids),
        "parallel_success": sum(1 for r in parallel_recs
                                if r["split_rows"] > 0 and r["mechanical_ok"]),
        "parallel_refused_by_gate": sum(1 for r in parallel_recs
                                        if r["split_rows"] == 0),
        "rowspan_total": len(rowspan_ids),
        "rowspan_success": sum(1 for r in rowspan_recs if r["mechanical_ok"]),
        "unique_struct_files": len(union_struct_ids),
        "files_with_split": sum(1 for r in union_recs.values()
                                if r["split_rows"] > 0),
        "files_normal_only": sum(1 for r in union_recs.values()
                                 if r["split_rows"] == 0 and r["normal_rows"] > 0),
        "files_keep_raw_present": sum(1 for r in union_recs.values()
                                      if r["keep_raw_rows"] > 0),
        "mechanical_failure_files": sum(1 for r in union_recs.values()
                                        if not r["mechanical_ok"]),
        "exception_files": 0,
        "suspected_wrong_split": sum(1 for r in union_recs.values()
                                     for _ in range(0)),  # 见 review 分类
        "suspected_wrong_merge": sum(r["split_rows"] for r in []),
        "suspected_misalignment": 0,
        "total_aligned_pairs": sum(r["aligned_sub_items"] for r in union_recs.values()),
        "total_duplicates_preserved": sum(r["duplicates_preserved"]
                                          for r in union_recs.values()),
        "baseline_total": len(baseline_recs),
        "baseline_unchanged": sum(1 for r in baseline_recs
                                  if not r["structure_changed"]),
        "baseline_changed": sum(1 for r in baseline_recs
                                if r["structure_changed"]),
        "baseline_needs_review": sum(1 for r in baseline_recs if r["needs_review"]),
        "total_review_issues": len(all_review),
    }

    # review 行分类统计（疑似误拆/误合并/错配）
    issue_kinds = {}
    for row in all_review:
        kind = str(row[4]).split(":", 1)[0]
        issue_kinds[kind] = issue_kinds.get(kind, 0) + 1
    agg["suspected_wrong_split"] = issue_kinds.get("fabricated", 0)
    agg["suspected_wrong_merge"] = issue_kinds.get("merged_rows", 0)
    agg["suspected_misalignment"] = (issue_kinds.get("order_violation", 0)
                                     + issue_kinds.get("region_mismatch", 0)
                                     + issue_kinds.get("span_count_mismatch", 0))
    agg["issue_kinds_breakdown"] = issue_kinds

    summary = {
        "aggregate": agg,
        "parallel_files": parallel_recs,
        "rowspan_files": rowspan_recs,
        "baseline_files": baseline_recs,
    }
    (OUT_DIR / "structure_regression_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- review CSV（无问题也生成，仅表头 = 空表，0异常）
    csv_path = OUT_DIR / "structure_regression_review.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "table_index", "row_index",
                    "decision", "issue", "original_text", "processed_text"])
        for row in all_review:
            w.writerow(row)

    # ---- 控制台结论
    print("\n" + "=" * 60)
    print("===== 结构回归测试 =====")
    print(f"并行列表：{agg['parallel_success']} / {agg['parallel_total']}"
          f"（证据门拒绝 {agg['parallel_refused_by_gate']}）")
    print(f"rowspan：{agg['rowspan_success']} / {agg['rowspan_total']}")
    print(f"机械错误：{agg['mechanical_failure_files']} 文件 / {agg['total_review_issues']} 条")
    print(f"疑似错配：{agg['suspected_misalignment']}")
    print(f"疑似误拆：{agg['suspected_wrong_split']}")
    print(f"疑似误合并：{agg['suspected_wrong_merge']}")
    print("\n===== 30条Baseline零行为变化 =====")
    print(f"未变化：{agg['baseline_unchanged']} / 30")
    print(f"发生变化：{agg['baseline_changed']} / 30")
    print(f"需要人工复核：{agg['baseline_needs_review']}")
    print(f"\n输出: {OUT_DIR}")


if __name__ == "__main__":
    main()
