from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from routers import upload, extract, analysis, pair, export
from services.graph_queries import GraphUnavailable

app = FastAPI(
    title="行业数据智能分析引擎",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(extract.router)
app.include_router(analysis.router)
app.include_router(pair.router)
app.include_router(export.router)


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
    return {"status": "ok", "service": "procurement-engine"}


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