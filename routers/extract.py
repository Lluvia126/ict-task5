from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path

from models.schemas import ParsedDocument, ExtractionResult
from services.file_parser import (
    parse_file, parse_zip_structured, read_html_raw, UPLOAD_DIR,
)
from services.id_gen import next_announcement_id
from services.ai_adapter import extract as ai_extract, check_ai_service
from services.export_service import enrich_extraction
from services import graph_sync
from services.security import (
    ALLOW_ANON_EXTRACT,
    QUOTA_EXTRACT_GLOBAL_DAY,
    QUOTA_EXTRACT_PER_IP_DAY,
    QUOTA_PARSE_PER_IP_DAY,
    protect,
)
from services.storage import (
    save_extraction,
    init_db,
    get_extraction,
    list_extractions,
    delete_extraction,
    clear_extractions,
    list_announcement_ids,
)

router = APIRouter(prefix="/api", tags=["extract"])

init_db()


@router.get("/ai/health")
def ai_health():
    """检查 AI 抽取服务是否可用。"""
    return check_ai_service()


@router.post("/parse/{file_id}", response_model=ParsedDocument,
             dependencies=[Depends(protect("parse", True, per_ip_day=QUOTA_PARSE_PER_IP_DAY))])
def parse_by_file_id(file_id: str, filename: str):
    suffix = Path(filename).suffix.lower()
    path = UPLOAD_DIR / f"{file_id}{suffix}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请先上传")
    result = parse_file(str(path), filename)
    return ParsedDocument(
        file_id=file_id,
        filename=filename,
        file_type=suffix.lstrip("."),
        text=result["text"],
        tables=result.get("tables", []),
        metadata={"saved_path": str(path)},
    )


@router.post("/extract/{file_id}", response_model=ExtractionResult,
             dependencies=[Depends(protect(
                 "extract",
                 allow_anon=ALLOW_ANON_EXTRACT,
                 per_ip_day=QUOTA_EXTRACT_PER_IP_DAY,
                 global_day=QUOTA_EXTRACT_GLOBAL_DAY,
             ))])
def extract_by_file_id(file_id: str, filename: str):
    suffix = Path(filename).suffix.lower()
    path = UPLOAD_DIR / f"{file_id}{suffix}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请先上传")

    parsed = parse_file(str(path), filename)
    announcement_id = next_announcement_id()

    # 按文件类型组装结构化字段
    html_text = ""          # 传给 AI：HTML 时是原始 HTML，其他为空
    html_tables = []
    attachments = []

    suffix_lower = suffix.lstrip(".")
    if suffix_lower in ("html", "htm"):
        html_text = read_html_raw(str(path))       # 原始 HTML（含标签）
        html_tables = parsed.get("tables", [])
    elif suffix_lower == "zip":
        attachments = parse_zip_structured(str(path))
    else:
        # 单文件 PDF/DOCX/XLSX 当附件处理
        attachments = [{
            "filename": filename,
            "tables": parsed.get("tables", []),
        }]

    try:
        data = ai_extract(
            text=parsed.get("text", ""),
            announcement_id=announcement_id,
            file_id=file_id,
            source_file=filename,
            html_text=html_text,
            html_tables=html_tables,
            attachments=attachments,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 抽取失败: {e}")

    # 补齐样例表所需的派生字段（公告标识 / 框架协议 / 标段），不重新抽取
    data = enrich_extraction(
        data,
        filename=filename,
        text=parsed.get("text", ""),
        html_raw=html_text,
    )

    # 同步到图谱（失败不阻断抽取，状态记在记录上，可用 /api/analysis/graph/status 查）
    data.update(graph_sync.attach(data))

    save_extraction(data)
    return ExtractionResult(**data)


@router.get("/extractions", response_model=list[ExtractionResult])
def get_all_extractions(limit: int = 100):
    return [ExtractionResult(**d) for d in list_extractions(limit)]


@router.get("/extractions/{announcement_id}", response_model=ExtractionResult)
def get_one_extraction(announcement_id: str):
    data = get_extraction(announcement_id)
    if not data:
        raise HTTPException(status_code=404, detail="未找到该公告抽取结果")
    return ExtractionResult(**data)


@router.delete("/extractions/{announcement_id}",
               dependencies=[Depends(protect("delete", allow_anon=False))])
def api_delete_extraction(announcement_id: str):
    """删除单条抽取结果，并同步删除它在图谱中的关系。"""
    deleted = delete_extraction(announcement_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="未找到该公告抽取结果")
    graph = graph_sync.remove_record(announcement_id)
    return {
        "status": "deleted",
        "announcement_id": announcement_id,
        "deleted_count": deleted,
        "graph": graph,
    }


@router.delete("/extractions",
               dependencies=[Depends(protect("clear", allow_anon=False))])
def api_clear_extractions():
    """清空所有抽取结果（只清数据，不删表和库），并同步清掉图谱里的对应关系。"""
    ids = list_announcement_ids()
    deleted = clear_extractions()
    graph = graph_sync.remove_records(ids)
    return {
        "status": "cleared",
        "deleted_count": deleted,
        "graph": graph,
    }