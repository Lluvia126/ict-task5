"""任务三：提取结果下载接口。

按《输出标准样例表.xlsx》组织为 总表 / 标的表 / 投标人表 三个视图，
列名与样例表逐字一致、包含全部蓝色字段。
支持 CSV（UTF-8 带 BOM）、JSON、XLSX 三种格式。
数据全部来自已入库的结构化抽取结果，不重新抽取。
"""

from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook

from services.export_service import (
    build_views,
    csv_bytes,
    enrich_extraction,
    export_filename,
    merge_views,
    views_to_csv,
)
from services.file_parser import UPLOAD_DIR, parse_file, read_html_raw
from services.format_rules import VIEW_ALIASES, VIEW_ORDER, VIEWS, detect_notice_id
from services.pair_store import get_pair
from services.storage import (
    get_extraction,
    get_extraction_by_file_id,
    list_extractions,
    save_extraction,
)

router = APIRouter(prefix="/api", tags=["export"])

VIEW_HELP = "视图：all（默认，三个视图）| summary/总表 | items/标的表 | bidders/投标人表"
FORMAT_HELP = "输出格式：csv（默认，UTF-8 带 BOM）| xlsx | json"


# ========== 校验 ==========

def _check_view(view: str) -> str:
    text = (view or "all").strip()
    if text == "all":
        return "all"
    if text not in VIEW_ALIASES:
        raise HTTPException(
            status_code=400,
            detail="不支持的 view=%s，可选：all/summary/items/bidders（或 总表/标的表/投标人表）" % view,
        )
    return text


def _check_format(fmt: str) -> str:
    text = (fmt or "csv").strip().lower()
    if text not in ("csv", "json", "xlsx"):
        raise HTTPException(
            status_code=400,
            detail="不支持的 format=%s，可选：csv/json/xlsx" % fmt,
        )
    return text


# ========== 响应构造 ==========

def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content=csv_bytes(text),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(filename),
        },
    )


def _xlsx_bytes(views: dict, view: str) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    keys = VIEW_ORDER if view == "all" else [VIEW_ALIASES[view]]
    for key in keys:
        sheet_name, columns = VIEWS[key]
        ws = wb.create_sheet(title=sheet_name[:31])
        ws.append(columns)
        for row in views.get(key, [])[1:]:
            ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(filename),
        },
    )


def _json_payload(views: dict, view: str) -> dict:
    keys = VIEW_ORDER if view == "all" else [VIEW_ALIASES[view]]
    tables = {}
    for key in keys:
        rows = views.get(key)
        if not rows:
            continue
        # 无表头时返回原始行，便于前端直接渲染三表
        tables[VIEWS[key][0]] = rows[0:] if view == "all" else rows[1:]
    return {
        "view": view,
        "columns": {VIEWS[k][0]: VIEWS[k][1] for k in keys},
        "tables": tables,
    }


# ========== 数据源解析 ==========

def _complete_from_source(pair: dict, data: dict) -> dict:
    """老记录缺分标段信息时，用配对原文按规则补全并回写缓存（不调用 AI 抽取）。"""
    if data.get("packages") or not pair.get("html_file_id"):
        return data
    suffix = Path(pair.get("html_filename") or "").suffix.lower()
    path = UPLOAD_DIR / f"{pair['html_file_id']}{suffix}"
    if not path.exists():
        return data
    try:
        parsed = parse_file(str(path), pair.get("html_filename") or path.name)
        enriched = enrich_extraction(
            data,
            filename=pair.get("html_filename") or "",
            text=parsed.get("text", ""),
            html_raw=read_html_raw(str(path)),
        )
    except Exception:
        return data
    if enriched != data:
        try:
            save_extraction(enriched)
        except Exception:
            pass
    return enriched


def _resolve_extraction(pair_id: str) -> dict:
    pair = get_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail="配对不存在")
    data = None
    announcement_id = (pair.get("announcement_id") or "").strip()
    if announcement_id:
        data = get_extraction(announcement_id)
    if not data:
        data = get_extraction_by_file_id(pair.get("html_file_id") or "")
    if not data:
        raise HTTPException(
            status_code=404,
            detail="该配对还没有抽取结果，请先调用 POST /api/extract-pair/%s" % pair_id,
        )
    return _complete_from_source(pair, data)


# ========== 单条下载 ==========

@router.get("/export/{pair_id}", summary="下载单条提取结果（三表，样例表格式）")
def export_pair(
    pair_id: str,
    view: str = Query("all", description=VIEW_HELP),
    fmt: str = Query("csv", alias="format", description=FORMAT_HELP),
):
    view = _check_view(view)
    fmt = _check_format(fmt)

    data = _resolve_extraction(pair_id)
    views = build_views(data)
    notice_id = data.get("notice_id") or detect_notice_id(data.get("source_file"))

    if fmt == "json":
        payload = _json_payload(views, view)
        payload.update({"pair_id": pair_id, "announcement_id": data.get("announcement_id")})
        return payload

    if fmt == "xlsx":
        content = _xlsx_bytes(views, view)
        filename = export_filename(notice_id, fallback=pair_id, view=view).replace(".csv", ".xlsx")
        return _xlsx_response(content, filename)

    # csv
    text = views_to_csv(views, view)
    filename = export_filename(notice_id, fallback=pair_id, view=view)
    return _csv_response(text, filename)


# ========== 全量下载 ==========

@router.get("/export", summary="下载全部提取结果（官方数据集提取结果格式）")
def export_all(
    view: str = Query("all", description=VIEW_HELP),
    fmt: str = Query("csv", alias="format", description=FORMAT_HELP),
    limit: int = Query(1000, ge=1, le=10000),
):
    view = _check_view(view)
    fmt = _check_format(fmt)

    records = list_extractions(limit)
    if not records:
        raise HTTPException(status_code=404, detail="暂无抽取结果")

    views = merge_views([build_views(d) for d in records])

    if fmt == "json":
        payload = _json_payload(views, view)
        payload["count"] = len(records)
        return payload

    if fmt == "xlsx":
        content = _xlsx_bytes(views, view)
        filename = export_filename("官方数据集提取结果", view=view).replace(".csv", ".xlsx")
        return _xlsx_response(content, filename)

    # csv
    text = views_to_csv(views, view)
    filename = export_filename("官方数据集提取结果", view=view)
    return _csv_response(text, filename)