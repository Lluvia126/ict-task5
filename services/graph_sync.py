"""图谱同步：把 SQLite 里的抽取结果投影到 Neo4j，并回答"图谱是否已更新"。

同步策略（**同步执行**，接口返回时图谱已经更新完，不需要轮询/等待）：
- 抽取入库成功 → 增量写入该条记录（`sync_record`）
- 删除单条公告   → 精确摘掉该记录贡献的关系（`remove_record`）
- 清空抽取记录   → 批量摘掉全部记录的关系（`remove_records`）
- 全量重建       → 清空图谱后按 SQLite 重建，可清除历史演示数据（`rebuild_all`）
- 增量补差       → 只补缺失、只删多余的（`sync_all`）

失败不阻断主流程：抽取接口照常返回结果，但会在记录上标记
`graph_synced=false / graph_sync_error`，并通过 `GET /api/analysis/graph/status`
给出 `in_sync`、`missing_in_graph`、`stale_in_graph`，可直接用于前端展示与排查。
"""

from __future__ import annotations

import os
from datetime import datetime

from services import graph_db, storage

# auto（默认，抽取/删除时自动同步）| off（关闭自动同步，只保留手动接口）
GRAPH_SYNC_MODE = os.getenv("GRAPH_SYNC_MODE", "auto").strip().lower()
_OFF_VALUES = ("off", "false", "0", "disabled", "none")


def sync_enabled() -> bool:
    return GRAPH_SYNC_MODE not in _OFF_VALUES


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fail(aid: str, error: str) -> dict:
    state = storage.get_graph_state()
    pending = list(dict.fromkeys(list(state.get("pending") or []) + [aid]))
    storage.update_graph_state(
        last_error=error, last_error_at=_now(), pending=pending
    )
    return {"announcement_id": aid, "synced": False, "error": error}


def attach(record: dict) -> dict:
    """抽取后调用：同步图谱并返回可直接写进抽取结果的标记字段。"""
    result = sync_record(record)
    return {
        "graph_synced": bool(result.get("synced")),
        "graph_sync_error": result.get("error") or result.get("skipped") or None,
    }


def sync_record(record: dict) -> dict:
    """把一条抽取结果增量写入图谱。"""
    aid = str((record or {}).get("announcement_id") or "").strip()
    if not sync_enabled():
        return {"announcement_id": aid, "synced": False,
                "skipped": "GRAPH_SYNC_MODE=%s，已关闭自动同步" % GRAPH_SYNC_MODE}
    try:
        stats = graph_db.import_extraction(record)
    except Exception as exc:
        return _fail(aid, "%s: %s" % (type(exc).__name__, exc))

    state = storage.get_graph_state()
    pending = [x for x in (state.get("pending") or []) if x != aid]
    storage.update_graph_state(
        last_sync_at=_now(), last_mode="incremental", last_records=1,
        last_error=None, pending=pending,
    )
    result = {"announcement_id": aid, "synced": True}
    result.update(stats)
    return result


def remove_record(announcement_id: str) -> dict:
    """删除公告后调用：把它在图谱里的关系一并删掉。"""
    aid = str(announcement_id or "").strip()
    if not sync_enabled() or not aid:
        return {"announcement_id": aid, "synced": False, "skipped": True}
    try:
        stats = graph_db.remove_extraction(aid)
    except Exception as exc:
        return _fail(aid, "%s: %s" % (type(exc).__name__, exc))

    state = storage.get_graph_state()
    pending = [x for x in (state.get("pending") or []) if x != aid]
    storage.update_graph_state(
        last_sync_at=_now(), last_mode="remove", last_records=1,
        last_error=None, pending=pending,
    )
    result = {"announcement_id": aid, "synced": True}
    result.update(stats)
    return result


def remove_records(announcement_ids) -> dict:
    """批量删除（清空抽取记录时调用）。"""
    ids = [str(x).strip() for x in (announcement_ids or []) if str(x).strip()]
    if not sync_enabled() or not ids:
        return {"synced": False, "records": 0, "skipped": True}
    try:
        stats = graph_db.remove_extractions(ids)
    except Exception as exc:
        return _fail(",".join(ids[:3]), "%s: %s" % (type(exc).__name__, exc))
    storage.update_graph_state(
        last_sync_at=_now(), last_mode="remove-all", last_records=len(ids),
        last_error=None, pending=[],
    )
    result = {"synced": True, "records": len(ids)}
    result.update(stats)
    return result


def rebuild_all(records=None) -> dict:
    """全量重建：清空图谱后按当前 SQLite 抽取结果重建（会清除历史演示数据）。"""
    if records is None:
        records = storage.list_extractions(limit=100000)
    try:
        cleared = graph_db.clear_graph()
        graph_db.init_constraints()
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    ok, failed = 0, []
    for record in records:
        try:
            graph_db.import_extraction(record)
            ok += 1
        except Exception as exc:
            failed.append({
                "announcement_id": record.get("announcement_id"),
                "error": "%s: %s" % (type(exc).__name__, exc),
            })

    payload = {
        "ok": not failed,
        "mode": "rebuild",
        "records": len(records),
        "imported": ok,
        "failed": failed,
        "cleared_nodes": cleared.get("removed_nodes", 0),
    }
    storage.update_graph_state(
        last_sync_at=_now(), last_mode="rebuild", last_records=ok,
        last_error=None if not failed else "some records failed", pending=[
            f["announcement_id"] for f in failed if f.get("announcement_id")
        ],
    )
    payload.update(status())
    return payload


def sync_all() -> dict:
    """增量补差：图谱缺的补上、SQLite 已删的摘掉。"""
    if not sync_enabled():
        return {"ok": False, "skipped": "GRAPH_SYNC_MODE=%s" % GRAPH_SYNC_MODE}
    current = status()
    if not current["neo4j"].get("connected"):
        return {"ok": False, "error": current["neo4j"].get("error"),
                "hint": "Neo4j 未连接，请先启动数据库（docker start neo4j）"}

    imported, removed, failed = [], [], []
    for aid in current["missing_in_graph"]:
        record = storage.get_extraction(aid)
        if not record:
            continue
        try:
            graph_db.import_extraction(record)
            imported.append(aid)
        except Exception as exc:
            failed.append({"announcement_id": aid,
                           "error": "%s: %s" % (type(exc).__name__, exc)})
    for aid in current["stale_in_graph"]:
        try:
            graph_db.remove_extraction(aid)
            removed.append(aid)
        except Exception as exc:
            failed.append({"announcement_id": aid,
                           "error": "%s: %s" % (type(exc).__name__, exc)})

    storage.update_graph_state(
        last_sync_at=_now(), last_mode="incremental-all",
        last_records=len(imported), last_error=None if not failed else "some records failed",
        pending=[f["announcement_id"] for f in failed if f.get("announcement_id")],
    )
    payload = {
        "ok": not failed,
        "mode": "incremental",
        "imported": imported,
        "removed": removed,
        "failed": failed,
    }
    payload.update(status())
    return payload


def status() -> dict:
    """图谱与 SQLite 的一致性状态：in_sync / missing_in_graph / stale_in_graph。"""
    state = storage.get_graph_state()
    sqlite_ids = storage.list_announcement_ids()
    payload = {
        "sync_enabled": sync_enabled(),
        "sync_mode": GRAPH_SYNC_MODE,
        "neo4j": graph_db.check_connection(),
        "sqlite": {"extractions": len(sqlite_ids), "announcement_ids": sqlite_ids},
        "graph": {},
        "missing_in_graph": [],
        "stale_in_graph": [],
        "in_sync": False,
        "last_sync": {
            "at": state.get("last_sync_at"),
            "mode": state.get("last_mode"),
            "records": state.get("last_records"),
        },
        "pending": state.get("pending") or [],
        "last_error": state.get("last_error"),
        "last_error_at": state.get("last_error_at"),
    }
    if not payload["neo4j"].get("connected"):
        return payload

    try:
        stats = graph_db.graph_stats()
    except Exception as exc:
        payload["neo4j"]["error"] = "%s: %s" % (type(exc).__name__, exc)
        return payload

    graph_ids = stats.pop("announcement_ids", [])
    payload["graph"] = stats
    payload["missing_in_graph"] = sorted(set(sqlite_ids) - set(graph_ids))
    payload["stale_in_graph"] = sorted(set(graph_ids) - set(sqlite_ids))
    payload["in_sync"] = not payload["missing_in_graph"] and not payload["stale_in_graph"]
    return payload