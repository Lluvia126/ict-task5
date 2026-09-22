from fastapi import APIRouter, UploadFile, File, HTTPException
from models.schemas import UploadResponse
from services.file_parser import save_upload

router = APIRouter(prefix="/api", tags=["upload"])

@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    info = save_upload(file.filename, content)
    return UploadResponse(**info, status="uploaded")