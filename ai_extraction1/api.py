"""FastAPI HTTP API：POST /extract 调用 QwenExtractor，GET /health 健康检查。

不修改 QwenExtractor 核心抽取逻辑。抽取失败时 extract() 抛出异常，
由 FastAPI 转为 500，详细错误已由 extract() 打印到控制台（保留报错溯源）。

两条输入装配路径（按 backend 选择，请求体 backend 优先于环境变量 EXTRACT_BACKEND）：
  - qwen（默认）：直接使用后端拼好的 request.text，行为与改动前完全一致。
  - lora：使用 LoRA 训练口径装配（build_lora_input），
          现场对 html_text 跑 preprocess_text、对 attachments 内的表跑 v2.2 筛选并 render，
          输出与训练 user 逐字节一致。
"""
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import config
from .extractors.qwen_extractor import QwenExtractor
from .preprocessing.build_lora_input import build_lora_input


class ExtractRequest(BaseModel):
    text: str
    announcement_id: str
    file_id: str = ""
    source_file: str = ""
    # 以下为可选字段：仅 lora 路径使用
    backend: Optional[str] = None          # "qwen" | "lora"，不传则用 EXTRACT_BACKEND
    html_text: str = ""                    # 清洗前正文；lora 路径对其跑 preprocess_text
    html_tables: list = []                 # 默认忽略
    attachments: list = []                 # [{"filename": str, "tables": [{"table_index": int, "data": [[...]]}]}]


app = FastAPI(title="招投标信息抽取 API")

# 延迟初始化：没配 QWEN_API_KEY 时服务仍能启动，
# /health 会明确报告 key 是否配置，/extract 返回 503 并说明原因，
# 避免"服务起不来但看不出原因"。
_extractor = None


def get_extractor() -> QwenExtractor:
    global _extractor
    if _extractor is None:
        _extractor = QwenExtractor()
    return _extractor


def _resolve_user_input(request: ExtractRequest) -> str:
    """按 backend 选择输入装配路径（请求体 backend 优先于环境变量）。"""
    backend = request.backend or config.EXTRACT_BACKEND
    if backend == "lora":
        return build_lora_input(request.model_dump())
    return request.text


@app.get("/health")
def health():
    return {
        "status": "ok",
        "qwen_api_key_configured": bool((config.QWEN_API_KEY or "").strip()),
        "model": config.QWEN_MODEL,
        "backend": config.EXTRACT_BACKEND,
    }


@app.post("/extract")
def extract_data(request: ExtractRequest):
    try:
        extractor = get_extractor()
    except Exception as exc:          # 未配置 Key 等
        raise HTTPException(status_code=503, detail="AI 服务未就绪：%s" % exc)
    user_input = _resolve_user_input(request)
    return extractor.extract(
        user_input,
        request.announcement_id,
        request.file_id,
        request.source_file,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ai_extraction.api:app", host="0.0.0.0", port=8000)
