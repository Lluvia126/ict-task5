"""公告 + 附件 成对处理 API。"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from pathlib import Path

from models.schemas import PairUploadResponse, ParsedPair, ExtractionResult
from services.file_parser import (
    save_upload, parse_file, parse_zip_structured, read_html_raw, UPLOAD_DIR,
)
from services.pair_store import (
    init_db, save_pair, get_pair, list_pairs, set_pair_announcement,
)
from services.id_gen import next_announcement_id
from services.ai_adapter import extract as ai_extract
from services.export_service import enrich_extraction
from services import graph_sync
from services.storage import save_extraction

router = APIRouter(prefix="/api", tags=["pair"])

init_db()


def _merge_texts(html_text: str, attachment_text: str) -> str:
    """
    附件优先，附件段已带 ===== 第一层附件：文件名 ===== 标记。
    训练格式严格一致，不引入自定义标记。
    """
    html_text = (html_text or "").strip()
    attachment_text = (attachment_text or "").strip()
    if not attachment_text and not html_text:
        return ""
    if not attachment_text:
        return html_text
    if not html_text:
        return attachment_text
    return f"{attachment_text}\n\n{html_text}"


@router.post("/upload-pair", response_model=PairUploadResponse)
async def upload_pair(
    html_file: UploadFile = File(..., description="公告 HTML"),
    zip_file: UploadFile = File(None, description="附件 ZIP（可选）"),
):
    if not html_file.filename:
        raise HTTPException(status_code=400, detail="HTML 文件名不能为空")

    html_content = await html_file.read()
    if not html_content:
        raise HTTPException(status_code=400, detail="HTML 内容为空")
    html_info = save_upload(html_file.filename, html_content)

    zip_file_id = ""
    zip_filename = ""
    if zip_file and zip_file.filename:
        zip_content = await zip_file.read()
        if zip_content:
            zip_info = save_upload(zip_file.filename, zip_content)
            zip_file_id = zip_info["file_id"]
            zip_filename = zip_file.filename

    pair_id = f"PAIR_{html_info['file_id'][:16]}"
    save_pair(
        pair_id=pair_id,
        html_file_id=html_info["file_id"],
        html_filename=html_file.filename,
        zip_file_id=zip_file_id,
        zip_filename=zip_filename,
    )

    return PairUploadResponse(
        pair_id=pair_id,
        html_file_id=html_info["file_id"],
        html_filename=html_file.filename,
        zip_file_id=zip_file_id,
        zip_filename=zip_filename,
    )


@router.get("/pairs")
def api_list_pairs(limit: int = 100):
    return {"count": len(list_pairs(limit)), "items": list_pairs(limit)}


@router.post("/parse-pair/{pair_id}", response_model=ParsedPair)
def parse_pair(pair_id: str):
    pair = get_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail="配对不存在")

    html_file_id = pair["html_file_id"]
    html_filename = pair["html_filename"]
    zip_file_id = pair["zip_file_id"]
    zip_filename = pair["zip_filename"]

    # HTML：同时读纯文本和原始 HTML
    html_path = UPLOAD_DIR / f"{html_file_id}{Path(html_filename).suffix.lower()}"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML 文件不存在")
    html_result = parse_file(str(html_path), html_filename)
    html_text = html_result.get("text", "")       # 纯文本，前端展示
    html_raw = read_html_raw(str(html_path))      # 原始 HTML，传给 AI
    html_tables = html_result.get("tables", [])

    # ZIP 按附件分组（filename 升序，训练一致）
    attachments = []
    zip_path = None
    if zip_file_id and zip_filename:
        zip_path = UPLOAD_DIR / f"{zip_file_id}{Path(zip_filename).suffix.lower()}"
        if zip_path.exists():
            attachments = parse_zip_structured(str(zip_path))

    # 附件段拼接：训练格式 ===== 第一层附件：文件名 =====
    attachment_text = "\n\n".join(
        f"===== 第一层附件：{a['filename']} =====\n{a.get('text', '')}"
        for a in attachments
    )

    merged_text = _merge_texts(html_text, attachment_text)

    return ParsedPair(
        pair_id=pair_id,
        html_text=html_text,
        html_raw=html_raw,
        html_tables=html_tables,
        attachment_text=attachment_text,
        merged_text=merged_text,
        tables=html_tables + [t for a in attachments for t in a.get("tables", [])],
        attachments=attachments,
        html_file_id=html_file_id,
        zip_file_id=zip_file_id,
        metadata={
            "html_saved": str(html_path),
            "zip_saved": str(zip_path) if zip_path else "",
        },
    )


@router.post("/extract-pair/{pair_id}", response_model=ExtractionResult)
def extract_pair(pair_id: str):
    pair = get_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail="配对不存在")

    parsed = parse_pair(pair_id)
    if not parsed.merged_text.strip() and not parsed.attachments:
        raise HTTPException(status_code=400, detail="合并文本为空，无法抽取")

    announcement_id = next_announcement_id()
    source_file = pair["html_filename"]
    if pair["zip_filename"]:
        source_file += f" + {pair['zip_filename']}"

    try:
        data = ai_extract(
            text=parsed.merged_text,
            announcement_id=announcement_id,
            file_id=pair["html_file_id"],
            source_file=source_file,
            html_text=parsed.html_raw,          # 原始 HTML（含标签）
            html_tables=parsed.html_tables,
            attachments=parsed.attachments,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 抽取失败: {e}")

    # 补齐样例表所需的派生字段（公告标识 / 框架协议 / 标段），不重新抽取
    data = enrich_extraction(
        data,
        filename=pair["html_filename"],
        text=parsed.merged_text,
        html_raw=parsed.html_raw,
    )

    # 同步到图谱（失败不阻断抽取）
    data.update(graph_sync.attach(data))

    save_extraction(data)
    set_pair_announcement(pair_id, data.get("announcement_id", ""))
    return ExtractionResult(**data)