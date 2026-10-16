"""公告-附件 配对元数据存储。"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/pairs.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pairs (
            pair_id TEXT PRIMARY KEY,
            html_file_id TEXT,
            html_filename TEXT,
            zip_file_id TEXT,
            zip_filename TEXT,
            announcement_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _ensure_column(conn, "pairs", "announcement_id", "TEXT")
    conn.commit()
    conn.close()


def _ensure_column(conn, table: str, column: str, ddl: str):
    """老库缺列时自动补列（SQLite 的轻量迁移）。"""
    cols = [row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)]
    if column not in cols:
        conn.execute('ALTER TABLE "%s" ADD COLUMN %s %s' % (table, column, ddl))


def set_pair_announcement(pair_id: str, announcement_id: str):
    """记录配对对应的抽取结果编号，便于导出时定位。"""
    if not pair_id or not announcement_id:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE pairs SET announcement_id = ? WHERE pair_id = ?",
                 (announcement_id, pair_id))
    conn.commit()
    conn.close()


def save_pair(pair_id: str, html_file_id: str, html_filename: str,
              zip_file_id: str = "", zip_filename: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO pairs
        (pair_id, html_file_id, html_filename, zip_file_id, zip_filename)
        VALUES (?, ?, ?, ?, ?)
    """, (pair_id, html_file_id, html_filename, zip_file_id, zip_filename))
    conn.commit()
    conn.close()


def get_pair(pair_id: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM pairs WHERE pair_id = ?", (pair_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_pairs(limit: int = 100) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM pairs ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]