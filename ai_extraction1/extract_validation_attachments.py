"""50 条验证集附件定向处理：只读 43 个内部附件包目录 + 定向解压 + 统计。

约束：
  - 不解压整个 13GB ZIP
  - 只处理 match_status=matched 的 43 个 announcement_id
  - 不读取附件正文，不调用 Qwen，不修改正式代码
"""
import csv
import json
import os
import zipfile
from io import BytesIO
from pathlib import Path

BASE = Path(__file__).parent
MAPPING_CSV = BASE / "data" / "validation_attachment_test" / "attachment_mapping.csv"
OUT_DIR = BASE / "data" / "validation_attachment_test"
EXTRACT_DIR = OUT_DIR / "extracted"
ZIP_BIG = Path(r"E:\项目数据与统计\赛题五.基准测试数据_file.zip")
OUT_CSV = OUT_DIR / "attachment_inventory.csv"
OUT_JSON = OUT_DIR / "attachment_extraction_summary.json"

# 文件类型分类
EXTRACT_CANDIDATES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".html", ".htm"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}


def safe_extract_path(entry_name, target_dir):
    """检查 ZIP 条目解压后是否仍在 target_dir 内，防止路径穿越。"""
    target = Path(target_dir).resolve()
    # 取 basename 或仅保留相对路径（去掉最开头的 ../ 等）
    parts = Path(entry_name).parts
    # 去掉任何 .. 开头或绝对路径前缀
    safe_parts = [p for p in parts if p != ".." and p != ""]
    final = target.joinpath(*safe_parts)
    try:
        final.relative_to(target)
        return final
    except ValueError:
        return None


def classify(ext):
    ext = ext.lower()
    if ext in EXTRACT_CANDIDATES:
        return "extract_candidate"
    if ext in IMG_EXTS:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "nested_archive"
    if ext == "":
        return "no_extension"
    return "other"


def main():
    # 1. 读取 mapping
    rows = list(csv.DictReader(MAPPING_CSV.read_text(encoding="utf-8-sig").splitlines()))
    matched = [r for r in rows if r.get("match_status") == "matched"]
    aids = sorted({r["announcement_id"] for r in matched})
    assert len(aids) == 43, f"matched 应为 43，实际 {len(aids)}"
    print(f"待处理 matched 公告: {len(aids)}")

    inventory = []   # 每个内部文件一行
    summary_by_aid = {}
    failed_aids = []
    nested_zip_aids = []
    unknown_aids = []

    with zipfile.ZipFile(ZIP_BIG) as zbig:
        for aid in aids:
            inner_zip_name = f"{aid}.zip"
            inner_dir = EXTRACT_DIR / aid
            inner_dir.mkdir(parents=True, exist_ok=True)

            try:
                # 读取内部 ZIP 的中央目录（不解压大ZIP到磁盘）
                data = zbig.read(inner_zip_name)
            except Exception as e:
                print(f"  {aid} 读取内部 ZIP 失败: {e}")
                failed_aids.append({"announcement_id": aid, "error": str(e)})
                continue

            counts = {"total": 0, "pdf": 0, "doc_docx": 0, "xls_xlsx": 0,
                      "html": 0, "image": 0, "nested_archive": 0,
                      "no_extension": 0, "other": 0, "extracted": 0}
            has_nested = False
            has_unknown = False

            with zipfile.ZipFile(BytesIO(data)) as zin:
                names = zin.namelist()
                for entry in names:
                    # 只处理文件（跳过目录条目）
                    if entry.endswith("/"):
                        continue
                    counts["total"] += 1
                    basename = Path(entry).name
                    ext = Path(entry).suffix.lower()
                    kind = classify(ext)

                    if ext == ".pdf":
                        counts["pdf"] += 1
                    elif ext in (".doc", ".docx"):
                        counts["doc_docx"] += 1
                    elif ext in (".xls", ".xlsx"):
                        counts["xls_xlsx"] += 1
                    elif ext in (".html", ".htm"):
                        counts["html"] += 1
                    elif kind == "image":
                        counts["image"] += 1
                    elif kind == "nested_archive":
                        counts["nested_archive"] += 1
                        has_nested = True
                    elif kind == "no_extension":
                        counts["no_extension"] += 1
                        has_unknown = True
                    else:
                        counts["other"] += 1
                        has_unknown = True

                    # 文件大小（从内部 ZIP info 获取）
                    try:
                        info = zin.getinfo(entry)
                        fsize = info.file_size
                    except Exception:
                        fsize = -1

                    # 定向解压（安全检查）
                    dest = safe_extract_path(entry, inner_dir)
                    extracted_ok = False
                    if dest:
                        try:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            if not dest.exists():
                                with zin.open(entry) as src, open(dest, "wb") as dst:
                                    dst.write(src.read())
                                extracted_ok = True
                                counts["extracted"] += 1
                            else:
                                extracted_ok = "skipped_exists"
                        except Exception as e:
                            extracted_ok = f"error:{e}"
                    else:
                        extracted_ok = "unsafe_path_skipped"

                    inventory.append({
                        "announcement_id": aid,
                        "attachment_filename": basename,
                        "extension": ext,
                        "file_size": fsize,
                        "nested_archive": kind == "nested_archive",
                        "extract_candidate": kind == "extract_candidate",
                        "file_kind": kind,
                        "path_in_inner_zip": entry,
                        "extracted": extracted_ok,
                    })

            if has_nested:
                nested_zip_aids.append(aid)
            if has_unknown:
                unknown_aids.append(aid)

            summary_by_aid[aid] = counts
            print(f"  {aid}: 内部文件 {counts['total']}, 解压 {counts['extracted']}, "
                  f"PDF={counts['pdf']}, DOC={counts['doc_docx']}, XLS={counts['xls_xlsx']}, "
                  f"HTML={counts['html']}, 图片={counts['image']}, 嵌套ZIP={counts['nested_archive']}, "
                  f"无扩展名={counts['no_extension']}")

    # 写 inventory.csv
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_filename", "extension",
                    "file_size", "nested_archive", "extract_candidate",
                    "file_kind", "path_in_inner_zip", "extracted"])
        for r in inventory:
            w.writerow([r["announcement_id"], r["attachment_filename"],
                        r["extension"], r["file_size"],
                        r["nested_archive"], r["extract_candidate"],
                        r["file_kind"], r["path_in_inner_zip"],
                        r["extracted"]])

    # 汇总
    total = sum(s["total"] for s in summary_by_aid.values())
    extracted_total = sum(s["extracted"] for s in summary_by_aid.values())
    summary = {
        "matched_announcements": len(aids),
        "successfully_processed": len(aids) - len(failed_aids),
        "failed": len(failed_aids),
        "total_files_in_attachments": total,
        "files_extracted": extracted_total,
        "pdf": sum(s["pdf"] for s in summary_by_aid.values()),
        "doc_docx": sum(s["doc_docx"] for s in summary_by_aid.values()),
        "xls_xlsx": sum(s["xls_xlsx"] for s in summary_by_aid.values()),
        "html": sum(s["html"] for s in summary_by_aid.values()),
        "image": sum(s["image"] for s in summary_by_aid.values()),
        "nested_archive": sum(s["nested_archive"] for s in summary_by_aid.values()),
        "no_extension": sum(s["no_extension"] for s in summary_by_aid.values()),
        "other": sum(s["other"] for s in summary_by_aid.values()),
        "failed_aids": failed_aids,
        "nested_zip_aids": nested_zip_aids,
        "unknown_aids": unknown_aids,
        "per_announcement": summary_by_aid,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n===== 附件定向处理汇总 =====")
    print(f"matched 公告: {len(aids)}")
    print(f"成功处理: {summary['successfully_processed']}")
    print(f"失败: {summary['failed']}")
    print(f"内部文件总数: {total}")
    print(f"成功解压数: {extracted_total}")
    print(f"PDF: {summary['pdf']}")
    print(f"DOC/DOCX: {summary['doc_docx']}")
    print(f"XLS/XLSX: {summary['xls_xlsx']}")
    print(f"HTML: {summary['html']}")
    print(f"图片: {summary['image']}")
    print(f"嵌套 ZIP: {summary['nested_archive']}")
    print(f"无扩展名: {summary['no_extension']}")
    print(f"其他: {summary['other']}")
    print(f"\n解压失败: {failed_aids or '无'}")
    print(f"存在嵌套 ZIP: {nested_zip_aids or '无'}")
    print(f"存在无法识别文件: {unknown_aids or '无'}")
    print(f"\nCSV: {OUT_CSV}\nJSON: {OUT_JSON}")


if __name__ == "__main__":
    main()
