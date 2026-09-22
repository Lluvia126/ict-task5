from fastapi import APIRouter, HTTPException
from pathlib import Path

from models.schemas import ParsedDocument, ExtractionResult
from services.file_parser import parse_file, UPLOAD_DIR
from services.id_gen import next_announcement_id
from services.ai_adapter import extract as ai_extract, check_ai_service
from services.storage import save_extraction, init_db, get_extraction, list_extractions

router = APIRouter(prefix="/api", tags=["extract"])

init_db()


@router.get("/ai/health")
def ai_health():
    """检查刘的 AI 抽取服务是否可用。"""
    return check_ai_service()


@router.post("/parse/{file_id}", response_model=ParsedDocument)
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


@router.post("/extract/{file_id}", response_model=ExtractionResult)
def extract_by_file_id(file_id: str, filename: str):
    suffix = Path(filename).suffix.lower()
    path = UPLOAD_DIR / f"{file_id}{suffix}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请先上传")

    parsed = parse_file(str(path), filename)
    announcement_id = next_announcement_id()

    try:
        data = ai_extract(
            text=parsed["text"],
            announcement_id=announcement_id,
            file_id=file_id,
            source_file=filename,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 抽取失败: {e}")

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