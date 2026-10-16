"""备份抽取数据：SQLite（热备）+ 上传文件 + 官方三表 CSV。

    .venv\\Scripts\\python.exe -m scripts.backup_data
    -> data/backups/20261016_2140/{results.db, pairs.db, uploads/, extractions.csv, ...}

SQLite 用官方 backup API，保证备份过程中写入也一致；建议配"任务计划程序"每天执行，
并在正式数据（官方约 1000 条）入库后立刻执行一次。
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.export_service import csv_bytes, merge_views, rows_to_csv, build_views  # noqa: E402
from services.format_rules import VIEWS  # noqa: E402
from services.storage import DB_PATH, list_extractions  # noqa: E402


def _backup_sqlite(src: Path, dst: Path) -> bool:
    if not src.exists():
        print("  跳过（不存在）:", src)
        return False
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)          # 热备，避免复制到写了一半的文件
    target.close()
    source.close()
    print("  已备份:", src, "->", dst, "(%.1f KB)" % (dst.stat().st_size / 1024))
    return True


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path("data/backups") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print("备份目录:", out_dir)

    # 1) 数据库
    for name in ("results.db", "pairs.db"):
        _backup_sqlite(Path(DB_PATH).parent / name, out_dir / name)

    # 2) 上传的原始文件（公告 HTML / 附件 ZIP / PDF）
    uploads = Path("data/uploads")
    if uploads.exists():
        shutil.copytree(uploads, out_dir / "uploads", dirs_exist_ok=True)
        count = len([p for p in (out_dir / "uploads").iterdir() if p.is_file()])
        print("  已备份上传文件:", count, "个")

    # 3) 抽取结果导出成样例表格式的 CSV（三张表各自一份，便于直接给评委/留档）
    records = list_extractions(limit=100000)
    if records:
        views = merge_views([build_views(record) for record in records])
        for key, rows in views.items():
            path = out_dir / ("%s.csv" % VIEWS[key][0])
            path.write_bytes(csv_bytes(rows_to_csv(rows)))
            print("  已导出:", path.name, len(rows) - 1, "行")
    else:
        print("  当前没有抽取结果，跳过 CSV 导出")
    print("  抽取记录数:", len(records))
    print("完成。建议把整个备份目录一起留档（或只留 results.db + CSV）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())