import os

try:                       # 必须最先执行：security 等模块在 import 时就读环境变量
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from routers import upload, extract, analysis, pair, export
from services import security
from services.graph_queries import GraphUnavailable

app = FastAPI(
    title="行业数据智能分析引擎",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)

# 跨域白名单：公网部署时用 ALLOWED_ORIGINS 指定前端地址（逗号分隔），
# 只有配置为 "*" 时才放开所有来源（本地联调），此时不再允许携带凭证。
CORS_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=CORS_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # 前后端不同域名时，浏览器默认读不到下载文件名，需要显式暴露
    expose_headers=["Content-Disposition"],
)

app.include_router(upload.router)
app.include_router(extract.router)
app.include_router(analysis.router)
app.include_router(pair.router)
app.include_router(export.router)


@app.get("/", include_in_schema=False)
def service_card():
    """根路径：给公网访问者一个明确回应（便于连通性测试）。"""
    return {
        "service": "行业数据智能分析引擎 - 后端 API",
        "version": app.version,
        "status": "ok",
        "docs": "/docs",
        "health": "/api/health",
        "hint": "前端页面请访问前端部署地址；本地址提供 API 与接口文档。",
    }


@app.exception_handler(GraphUnavailable)
async def graph_unavailable_handler(request, exc):
    """Neo4j 不可用时给出明确提示，而不是 500。"""
    return JSONResponse(
        status_code=503,
        content={
            "detail": "图谱服务不可用（Neo4j）：%s" % exc,
            "hint": "docker start neo4j 后重试；可用 GET /api/analysis/graph/status 查看连接状态",
        },
    )


@app.get("/api/health")
def health():
    """探活接口（免登录、只读），顺带返回当前安全配置摘要（不含口令）。"""
    return {
        "status": "ok",
        "service": "procurement-engine",
        "security": security.describe(),
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
    )


def _fix_file_schema(node):
    """递归把 contentMediaType 改成 format: binary。"""
    if isinstance(node, dict):
        if node.get("contentMediaType") == "application/octet-stream":
            node.pop("contentMediaType", None)
            node["format"] = "binary"
        for v in node.values():
            _fix_file_schema(v)
    elif isinstance(node, list):
        for v in node:
            _fix_file_schema(v)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.0.2",
        routes=app.routes,
    )
    _fix_file_schema(schema)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi