"""公网部署的接口防护：管理口令 + 调用配额 + 上传限制 + 访问留痕。

面向评委的**免登录**只开放"读"接口，以及受配额限制的上传/抽取；破坏性接口
（删除、清空、图谱重建）一律要求管理口令（请求头 X-Admin-Token）。数据库
（Neo4j bolt:7687 / 7474）与 AI 服务只监听本机，不经隧道暴露。

配置见 .env（或环境变量）：
    ADMIN_TOKEN                 管理口令；留空 = 本地开发不校验
    PUBLIC_MODE=1               公网模式：没配 ADMIN_TOKEN 时管理接口直接关闭（fail-closed）
    ALLOW_ANON_UPLOAD / _EXTRACT 是否允许匿名上传/抽取（评委自助测试任务三需要 1）
    QUOTA_*                     配额：每 IP 每天的次数 / 抽取的全局每日上限（控 AI 费用）
    MAX_UPLOAD_MB               上传大小上限
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Callable, Optional

from fastapi import Header, HTTPException, Request

try:
    from dotenv import load_dotenv
    load_dotenv()          # 兜底：直接 import 本模块时也先读 .env
except ImportError:
    pass

from services import storage

# ---------------- 配置 ----------------

def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "disabled", "")


ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
PUBLIC_MODE = _flag("PUBLIC_MODE", "0")
ALLOW_ANON_UPLOAD = _flag("ALLOW_ANON_UPLOAD", "1")
ALLOW_ANON_EXTRACT = _flag("ALLOW_ANON_EXTRACT", "1")
# 是否允许匿名删除/清空（前端没有登录态；设为 1 时前端按钮可直接用，
# 但公网开放期间任何人也能调用 —— 请配合备份使用，或改回 0 走 X-Admin-Token）
ALLOW_ANON_DELETE = _flag("ALLOW_ANON_DELETE", "0")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
ALLOWED_UPLOAD_SUFFIX = {
    ".html", ".htm", ".zip", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".csv", ".txt", ".png", ".jpg", ".jpeg",
}

QUOTA_UPLOAD_PER_IP_DAY = int(os.getenv("QUOTA_UPLOAD_PER_IP_DAY", "10"))
QUOTA_EXTRACT_PER_IP_DAY = int(os.getenv("QUOTA_EXTRACT_PER_IP_DAY", "20"))
QUOTA_EXTRACT_GLOBAL_DAY = int(os.getenv("QUOTA_EXTRACT_GLOBAL_PER_DAY", "300"))
QUOTA_PARSE_PER_IP_DAY = int(os.getenv("QUOTA_PARSE_PER_IP_DAY", "100"))
QUOTA_DELETE_PER_IP_DAY = int(os.getenv("QUOTA_DELETE_PER_IP_DAY", "50"))

# ---------------- 日志（留痕，便于溯源与费用核算） ----------------

logger = logging.getLogger("security")
if not logger.handlers:
    Path("logs").mkdir(exist_ok=True)
    _file = logging.FileHandler("logs/access.log", encoding="utf-8")
    _file.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_file)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


def _log(bucket: str, request: Request, ip: str, result: str) -> None:
    logger.info(
        "bucket=%s result=%s ip=%s method=%s path=%s ua=%s",
        bucket, result, ip, request.method, request.url.path,
        (request.headers.get("user-agent") or "-")[:80],
    )


# ---------------- 工具 ----------------

def client_ip(request: Request) -> str:
    """取真实来源 IP：隧道会填 CF-Connecting-IP / X-Forwarded-For。"""
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_admin(token: Optional[str]) -> bool:
    return bool(ADMIN_TOKEN) and bool(token) and secrets.compare_digest(token.strip(), ADMIN_TOKEN)


def _reject_no_token():
    if PUBLIC_MODE and not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="公网模式下未配置 ADMIN_TOKEN，管理接口已关闭",
        )
    raise HTTPException(
        status_code=401,
        detail="该接口需要管理员口令：请在请求头带 X-Admin-Token",
    )


def check_upload(filename: str, size: int) -> None:
    """上传前校验类型与大小（在读取内容之后调用）。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIX:
        raise HTTPException(status_code=400, detail="不支持的文件类型：%s" % (suffix or "未知"))
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，上限 %d MB" % MAX_UPLOAD_MB)


def _precheck_content_length(request: Request) -> None:
    """读内存之前先按 Content-Length 拦超大请求，避免被超大上传打爆。"""
    raw = request.headers.get("content-length")
    if raw and raw.isdigit():
        if int(raw) > (MAX_UPLOAD_MB + 1) * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件过大，上限 %d MB" % MAX_UPLOAD_MB)


# ---------------- 依赖工厂 ----------------

def protect(
    bucket: str,
    allow_anon: bool = True,
    per_ip_day: Optional[int] = None,
    global_day: Optional[int] = None,
    check_size: bool = False,
) -> Callable:
    """返回一个 FastAPI 依赖。

    - 带正确 X-Admin-Token：直通，不计配额
    - allow_anon=False：必须口令（用于删除/清空/图谱重建等破坏性接口）
    - allow_anon=True ：按 per_ip_day / global_day 配额放行（控制 AI 费用）
    """

    def dependency(
        request: Request,
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    ) -> bool:
        ip = client_ip(request)

        if is_admin(x_admin_token):
            _log(bucket, request, ip, "admin")
            return True

        if not allow_anon:
            if not ADMIN_TOKEN and not PUBLIC_MODE:
                # 本地开发：没配 ADMIN_TOKEN 且非公网模式，不校验（方便本机调试）
                _log(bucket, request, ip, "open-local")
                return True
            _log(bucket, request, ip, "denied")
            _reject_no_token()

        if check_size:
            _precheck_content_length(request)

        if per_ip_day:
            quota = storage.hit_quota("%s:ip" % bucket, ip, "day", per_ip_day)
            if not quota["allowed"]:
                _log(bucket, request, ip, "quota-ip")
                raise HTTPException(
                    status_code=429,
                    detail="调用次数已达上限（每 IP 每天 %d 次），请明天再试" % per_ip_day,
                )
        if global_day:
            quota = storage.hit_quota("%s:all" % bucket, "all", "day", global_day)
            if not quota["allowed"]:
                _log(bucket, request, ip, "quota-global")
                raise HTTPException(
                    status_code=429,
                    detail="今日全局额度已用完（%d 次），请明天再试" % global_day,
                )

        _log(bucket, request, ip, "ok")
        return True

    return dependency


def describe() -> dict:
    """当前安全配置摘要（供 /api/health 或自检使用，不泄露口令）。"""
    return {
        "public_mode": PUBLIC_MODE,
        "admin_token_configured": bool(ADMIN_TOKEN),
        "allow_anon_upload": ALLOW_ANON_UPLOAD,
        "allow_anon_extract": ALLOW_ANON_EXTRACT,
        "allow_anon_delete": ALLOW_ANON_DELETE,
        "quota_upload_per_ip_per_day": QUOTA_UPLOAD_PER_IP_DAY,
        "quota_extract_per_ip_per_day": QUOTA_EXTRACT_PER_IP_DAY,
        "quota_extract_global_per_day": QUOTA_EXTRACT_GLOBAL_DAY,
        "max_upload_mb": MAX_UPLOAD_MB,
    }