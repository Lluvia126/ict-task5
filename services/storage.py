import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/results.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extractions (
            announcement_id TEXT PRIMARY KEY,
            file_id TEXT,
            source_file TEXT,
            project_name TEXT,
            project_code TEXT,
            purchaser TEXT,
            winning_suppliers TEXT,
            bid_participants TEXT,
            project_amount REAL,
            products TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graph_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotas (
            bucket TEXT,
            key TEXT,
            window TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (bucket, key, window)
        )
    """)
    conn.execute("DELETE FROM quotas WHERE window < ?", (_day(-7),))
    conn.commit()
    conn.close()


def save_extraction(data: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO extractions
        (announcement_id, file_id, source_file, project_name, project_code,
         purchaser, winning_suppliers, bid_participants, project_amount,
         products, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("announcement_id"),
        data.get("file_id"),
        data.get("source_file"),
        data.get("project_name"),
        data.get("project_code"),
        data.get("purchaser"),
        json.dumps(data.get("winning_suppliers", []), ensure_ascii=False),
        json.dumps(data.get("bid_participants", []), ensure_ascii=False),
        data.get("project_amount"),
        json.dumps(data.get("products", []), ensure_ascii=False),
        json.dumps(data, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


def get_extraction(announcement_id: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM extractions WHERE announcement_id = ?",
        (announcement_id,)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return json.loads(row["raw_json"])
    return None


def list_extractions(limit: int = 100) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT raw_json FROM extractions ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return [json.loads(r["raw_json"]) for r in rows]


def delete_extraction(announcement_id: str) -> int:
    """
    删除单条抽取结果。
    products 等关联数据存在 raw_json 里，随主记录一起删除。
    返回删除的记录数（0 或 1）。
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "DELETE FROM extractions WHERE announcement_id = ?",
        (announcement_id,)
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def clear_extractions() -> int:
    """
    清空所有抽取结果。
    只 DELETE FROM，不删表和库文件。
    返回删除的记录数。
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM extractions")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def get_extraction_by_file_id(file_id: str) -> Optional[dict]:
    """按上传文件 id 反查抽取结果（配对与结果没存关联时的兜底）。"""
    if not file_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT raw_json FROM extractions WHERE file_id = ? ORDER BY created_at DESC LIMIT 1",
        (file_id,),
    )
    row = cur.fetchone()
    conn.close()
    return json.loads(row["raw_json"]) if row else None


def list_announcement_ids() -> list:
    """当前有效（已入库）的公告编号列表，用于图谱范围过滤与一致性比对。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT announcement_id FROM extractions ORDER BY announcement_id")
    rows = [r[0] for r in cur.fetchall() if r[0]]
    conn.close()
    return rows


def get_graph_state() -> dict:
    """读取图谱同步状态（last_sync / pending / last_error）。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT value FROM graph_state WHERE key = 'sync'")
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return {}


def update_graph_state(patch: dict = None, **kwargs) -> dict:
    """合并写入图谱同步状态（支持传 dict 或关键字参数）。"""
    merged = dict(patch or {})
    merged.update(kwargs)
    state = get_graph_state()
    state.update(merged)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO graph_state (key, value, updated_at) VALUES ('sync', ?, CURRENT_TIMESTAMP)",
        (json.dumps(state, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()
    return state


# ---------------- 调用配额（公网防护，见 services/security.py） ----------------

def _day(offset: int = 0) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def hit_quota(bucket: str, key: str, kind: str = "day", limit: int = 0) -> dict:
    """计数 +1 并判断是否超限；kind=day/hour，用于按 IP / 全局限制调用次数。"""
    now = datetime.now()
    window = now.strftime("%Y-%m-%dT%H") if kind == "hour" else now.strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO quotas (bucket, key, window, count) VALUES (?, ?, ?, 1)
           ON CONFLICT(bucket, key, window) DO UPDATE SET count = count + 1""",
        (bucket, key, window),
    )
    row = conn.execute(
        "SELECT count FROM quotas WHERE bucket = ? AND key = ? AND window = ?",
        (bucket, key, window),
    ).fetchone()
    conn.commit()
    conn.close()
    count = row[0] if row else 0
    return {"allowed": count <= limit, "count": count, "limit": limit, "window": window}


def quota_usage(bucket: str) -> list:
    """当前窗口内的配额使用情况（自检/排查用）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT bucket, key, window, count FROM quotas WHERE bucket = ? ORDER BY count DESC",
        (bucket,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
