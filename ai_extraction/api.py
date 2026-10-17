"""FastAPI HTTP API：POST /extract 调用 QwenExtractor，GET /health 健康检查。

不修改 QwenExtractor 核心抽取逻辑。抽取失败时 extract() 抛出异常，
由 FastAPI 转为 500，详细错误已由 extract() 打印到控制台（保留报错溯源）。
"""
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .extractors.qwen_extractor import QwenExtractor


class ExtractRequest(BaseModel):
    text: str
    announcement_id: str
    file_id: str = ""
    source_file: str = ""


app = FastAPI(title="招投标信息抽取 API")

_extractor = None


def get_extractor() -> QwenExtractor:
    global _extractor
    if _extractor is None:
        _extractor = QwenExtractor()
    return _extractor


@app.get("/health")
def health():
    return {
        "status": "ok",
        "qwen_api_key_configured": bool(os.environ.get("QWEN_API_KEY", "").strip()),
        "model": os.environ.get("QWEN_MODEL", "qwen-vl-max"),
    }


@app.post("/extract")
def extract_data(request: ExtractRequest):
    try:
        extractor = get_extractor()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI 服务未就绪：%s" % exc)
    return extractor.extract(
        request.text,
        request.announcement_id,
        request.file_id,
        request.source_file,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ai_extraction.api:app", host="0.0.0.0", port=8000)
