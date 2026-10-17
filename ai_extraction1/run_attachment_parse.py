"""43 个公告附件正文解析运行器。

产物：
  parsed/<aid>/<附件名>.json          每个附件的结构化解析结果
  parsed_text/<aid>.txt               公告+附件统一中间文本
  parse_errors.csv                    解析失败记录
  price_detail_candidates.csv         报价表/产品明细关键词命中
  nested_zip_registry.csv             嵌套压缩包登记（不展开）
  attachment_parse_summary.json       汇总统计

不调用 Qwen，不修改正式 pipeline / Prompt / Gold。
"""
import csv
import json
from pathlib import Path

from ai_extraction.preprocessing.attachment_parser import (
    parse_attachment, PARSEABLE_EXTS,
)
from ai_extraction.preprocessing.cleaner import preprocess_text

BASE = Path(__file__).parent
DATA = BASE / "data" / "validation_attachment_test"
EXTRACTED = DATA / "extracted"
VAL_HTML = BASE / "data" / "validation"
PARSED_DIR = DATA / "parsed"
TEXT_DIR = DATA / "parsed_text"

KEYWORDS = ["分项报价表", "采购标的", "产品名称", "品目", "品牌",
            "规格型号", "单价", "数量", "总价", "投标报价", "报价明细"]
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}


def render_table(tb):
    """表格 → 管道分隔文本，保持行列。"""
    lines = []
    for row in tb["data"]:
        cells = ["" if c is None else str(c).replace("\n", " ") for c in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def main():
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)

    inv = list(csv.DictReader(
        (DATA / "attachment_inventory.csv").read_text(
            encoding="utf-8-sig").splitlines()))

    parse_rows = [r for r in inv if r["extension"] in PARSEABLE_EXTS]
    archive_rows = [r for r in inv if r["extension"] in ARCHIVE_EXTS]

    results = {}          # aid -> [parse rec]
    errors = []           # 解析失败
    price_cands = []      # 报价特征
    per_aid_ok = {}       # aid -> 成功解析数
    stats = {".pdf": [0, 0], ".docx": [0, 0], ".doc": [0, 0],
             ".xlsx": [0, 0], ".xls": [0, 0]}  # [成功, 失败]

    for r in parse_rows:
        aid = r["announcement_id"]
        fpath = EXTRACTED / aid / r["path_in_inner_zip"]
        rec = parse_attachment(fpath, aid, source_file=f"{aid}.html")
        results.setdefault(aid, []).append(rec)

        out_dir = PARSED_DIR / aid
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{rec['attachment_filename']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

        ok = rec["parse_status"] == "success"
        stats[rec["extension"]][0 if ok else 1] += 1
        if ok:
            per_aid_ok[aid] = per_aid_ok.get(aid, 0) + 1
        else:
            errors.append([aid, rec["attachment_filename"],
                           rec["extension"], rec["error"], rec["reason"]])
            continue

        # 关键词扫描：正文 + 逐表格
        text_hit = [k for k in KEYWORDS if k in (rec["text"] or "")]
        if text_hit:
            price_cands.append([aid, rec["attachment_filename"],
                                "|".join(text_hit), "正文"])
        for tb in rec["tables"]:
            blob = "\n".join(
                " ".join("" if c is None else str(c) for c in row)
                for row in tb["data"])
            hit = [k for k in KEYWORDS if k in blob]
            if hit:
                price_cands.append([aid, rec["attachment_filename"],
                                    "|".join(hit), tb["table_index"]])

    # 统一中间文本（公告清洗文本 + 附件）
    # 覆盖全部 43 个有附件公告：仅含嵌套包/图片的公告也生成（仅公告部分 + 说明）
    aids = sorted({r["announcement_id"] for r in inv})
    for aid in aids:
        parts = []
        html_path = VAL_HTML / f"{aid}.html"
        if html_path.exists():
            cleaned = preprocess_text(
                html_path.read_text(encoding="utf-8", errors="ignore"))
            parts.append(f"【公告】\n{cleaned}")
        for rec in results.get(aid, []):
            if rec["parse_status"] != "success":
                parts.append(f"【附件：{rec['attachment_filename']}】\n"
                             f"[解析失败: {rec['error']}]")
                continue
            parts.append(f"【附件：{rec['attachment_filename']}】")
            if rec["text"]:
                parts.append(rec["text"])
            for tb in rec["tables"]:
                label = f"【表格{tb['table_index'] + 1}】"
                if tb.get("sheet"):
                    label = f"【表格{tb['table_index'] + 1}（Sheet：{tb['sheet']}）】"
                parts.append(label)
                parts.append(render_table(tb))
        (TEXT_DIR / f"{aid}.txt").write_text(
            "\n\n".join(parts), encoding="utf-8")

    # parse_errors.csv
    with open(DATA / "parse_errors.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_filename",
                    "extension", "error", "reason"])
        w.writerows(errors)

    # price_detail_candidates.csv
    with open(DATA / "price_detail_candidates.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_filename",
                    "matched_keywords", "table_index"])
        w.writerows(price_cands)

    # nested_zip_registry.csv
    with open(DATA / "nested_zip_registry.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_filename",
                    "nested_zip_path"])
        for r in archive_rows:
            w.writerow([r["announcement_id"], r["attachment_filename"],
                        str(EXTRACTED / r["announcement_id"]
                            / r["path_in_inner_zip"])])

    # 汇总
    n_ok = sum(v[0] for v in stats.values())
    n_fail = sum(v[1] for v in stats.values())
    with_table = sum(
        1 for recs in results.values() for rec in recs
        if rec["parse_status"] == "success" and rec["tables"])
    price_files = {(r[0], r[1]) for r in price_cands}
    price_aids = {r[0] for r in price_cands}
    nested_aids = {r["announcement_id"] for r in archive_rows}
    # 嵌套包所属公告在第一层无报价特征 → 可能是进一步明细来源
    nested_maybe = sorted(nested_aids - price_aids)

    summary = {
        "announcements_with_attachments": len(aids),
        "attachments_total": len(inv),
        "parseable_candidates": len(parse_rows),
        "parsed_success": n_ok,
        "parsed_failed": n_fail,
        "pdf": {"success": stats[".pdf"][0], "failed": stats[".pdf"][1]},
        "docx": {"success": stats[".docx"][0], "failed": stats[".docx"][1]},
        "doc": {"success": stats[".doc"][0], "failed": stats[".doc"][1]},
        "xls_xlsx": {"success": stats[".xls"][0] + stats[".xlsx"][0],
                     "failed": stats[".xls"][1] + stats[".xlsx"][1]},
        "attachments_with_tables": with_table,
        "attachments_with_price_keywords": len(price_files),
        "announcements_with_price_detail": sorted(price_aids),
        "n_announcements_with_price_detail": len(price_aids),
        "nested_archives": len(archive_rows),
        "nested_zip_maybe_detail_source": nested_maybe,
        "parsed_files_per_announcement": {
            aid: per_aid_ok.get(aid, 0) for aid in aids},
    }
    (DATA / "attachment_parse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("===== 附件解析汇总 =====")
    print(f"公告数(有附件): {len(aids)}")
    print(f"附件总数: {len(inv)}")
    print(f"可解析候选(PDF/DOC/DOCX/XLS/XLSX): {len(parse_rows)}")
    print(f"解析成功: {n_ok}  失败: {n_fail}")
    print(f"PDF: {stats['.pdf'][0]}/{sum(stats['.pdf'])} 成功")
    print(f"DOCX: {stats['.docx'][0]}/{sum(stats['.docx'])} 成功")
    print(f"DOC: {stats['.doc'][0]}/{sum(stats['.doc'])} 成功")
    print(f"XLS/XLSX: {summary['xls_xlsx']['success']}/"
          f"{summary['xls_xlsx']['success'] + summary['xls_xlsx']['failed']} 成功")
    print(f"含表格附件: {with_table}")
    print(f"含报价表/产品明细关键词附件: {len(price_files)}")
    print(f"涉及公告数: {len(price_aids)}/50")
    print(f"嵌套压缩包: {len(archive_rows)}，"
          f"其中 {len(nested_maybe)} 个公告第一层未检出报价关键词，"
          f"嵌套包可能是进一步明细来源: {nested_maybe}")


if __name__ == "__main__":
    main()
