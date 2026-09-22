import json
import sqlite3
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