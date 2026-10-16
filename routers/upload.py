from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from models.schemas import UploadResponse
from services.file_parser import save_upload
from services.security import (
    ALLOW_ANON_UPLOAD,
    QUOTA_UPLOAD_PER_IP_DAY,
    check_upload,
    protect,
)

router = APIRouter(prefix="/api", tags=["upload"])

# 上传：默认允许评委匿名上传（任务三自助上传），但按 IP 限次、限制类型与大小；
# 设 ALLOW_ANON_UPLOAD=0 则改为仅管理员可用（需要请求头 X-Admin-Token）。
upload_guard = Depends(protect(
    "upload",
    allow_anon=ALLOW_ANON_UPLOAD,
    per_ip_day=QUOTA_UPLOAD_PER_IP_DAY,
    check_size=True,
))


@router.post("/upload", response_model=UploadResponse, dependencies=[upload_guard])
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    content = await file.read()
    check_upload(file.filename, len(content))
    info = save_upload(file.filename, content)
    return UploadResponse(**info, status="uploaded")