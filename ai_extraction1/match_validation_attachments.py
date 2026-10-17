"""50 条验证集 announcement_id ↔ 官方附件 ZIP 自动匹配（只读中央目录，不解压、不读正文）。

匹配规则（按优先级）：
  1. ZIP 内文件名直接等于 <announcement_id>.zip（filename_exact）
  2. ZIP 内文件名包含 announcement_id（filename_contains，含边界检查）
  3. ZIP 内目录路径包含 announcement_id（dir_contains，含边界检查）
  仅边界被突破的前缀/子串命中（如 aid 是更长 ID 的前缀）→ uncertain

输出：data/validation_attachment_test/attachment_mapping.csv
不解压 ZIP，不读取附件内容，不调用 Qwen，不修改任何正式代码。
"""
import csv
import re
import zipfile
from pathlib import Path

CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")
ZIP_PATH = Path(r"E:\项目数据与统计\赛题五.基准测试数据_file.zip")
OUT_DIR = Path(__file__).parent / "data" / "validation_attachment_test"
OUT_CSV = OUT_DIR / "attachment_mapping.csv"


def boundary_ok(name, aid):
    """aid 在 name 中出现位置的后一个字符不是字母/数字（防止前缀误配）。"""
    start = 0
    while True:
        i = name.find(aid, start)
        if i < 0:
            return False
        nxt = name[i + len(aid):i + len(aid) + 1]
        if not nxt or not re.match(r"[A-Za-z0-9]", nxt):
            return True
        start = i + 1


def main():
    rows = list(csv.DictReader(CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    aids = [r["announcement_id"].strip() for r in rows]
    assert len(aids) == 50 and len(set(aids)) == 50

    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()  # 只读中央目录
    print(f"ZIP 条目总数: {len(names)}，验证集公告: {len(aids)}")

    out_rows = []
    stat = {"matched": 0, "not_found": 0, "multiple_candidates": 0, "uncertain": 0}
    detail = {"not_found": [], "multiple_candidates": [], "uncertain": []}

    for aid in aids:
        # 1. 文件名精确等于 <aid>.zip
        exact = [n for n in names if n.rsplit("/", 1)[-1] == f"{aid}.zip"]
        if len(exact) == 1:
            n = exact[0]
            fn = n.rsplit("/", 1)[-1]
            out_rows.append([aid, n, fn, ".zip", "filename_exact", "matched"])
            stat["matched"] += 1
            continue
        if len(exact) > 1:
            for n in exact:
                fn = n.rsplit("/", 1)[-1]
                out_rows.append([aid, n, fn, Path(fn).suffix,
                                 "filename_exact", "multiple_candidates"])
            stat["multiple_candidates"] += 1
            detail["multiple_candidates"].append((aid, exact))
            continue

        # 2/3. 边界安全的包含匹配（文件名 / 目录路径）
        hits = [n for n in names if boundary_ok(n, aid)]
        if len(hits) == 1:
            n = hits[0]
            fn = n.rsplit("/", 1)[-1]
            method = "filename_contains" if aid in fn else "dir_contains"
            out_rows.append([aid, n, fn, Path(fn).suffix, method, "matched"])
            stat["matched"] += 1
        elif len(hits) > 1:
            for n in hits:
                fn = n.rsplit("/", 1)[-1]
                method = "filename_contains" if aid in fn else "dir_contains"
                out_rows.append([aid, n, fn, Path(fn).suffix,
                                 method, "multiple_candidates"])
            stat["multiple_candidates"] += 1
            detail["multiple_candidates"].append((aid, hits))
        else:
            # 仅子串但边界被突破（疑似前缀误配）→ uncertain
            loose = [n for n in names if aid in n]
            if loose:
                for n in loose:
                    fn = n.rsplit("/", 1)[-1]
                    out_rows.append([aid, n, fn, Path(fn).suffix,
                                     "substring_boundary_break", "uncertain"])
                stat["uncertain"] += 1
                detail["uncertain"].append((aid, loose))
            else:
                out_rows.append([aid, "", "", "", "none", "not_found"])
                stat["not_found"] += 1
                detail["not_found"].append((aid, []))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_path_in_zip",
                    "attachment_filename", "extension",
                    "match_method", "match_status"])
        w.writerows(out_rows)

    print("\n===== 附件匹配统计 =====")
    print(f"总公告: 50")
    print(f"matched: {stat['matched']}")
    print(f"not_found: {stat['not_found']}")
    print(f"multiple_candidates: {stat['multiple_candidates']}")
    print(f"uncertain: {stat['uncertain']}")

    for key in ("not_found", "multiple_candidates", "uncertain"):
        print(f"\n--- {key} ---")
        if not detail[key]:
            print("  无")
        for aid, cands in detail[key]:
            print(f"  {aid}")
            for c in cands:
                print(f"      候选: {c}")

    print(f"\nCSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
