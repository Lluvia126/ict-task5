"""FastAPI HTTP API：POST /extract 调用 QwenExtractor，GET /health 健康检查。

不修改 QwenExtractor 核心抽取逻辑。抽取失败时 extract() 抛出异常，
由 FastAPI 转为 500，详细错误已由 extract() 打印到控制台（保留报错溯源）。
"""
from fastapi import FastAPI
from pydantic import BaseModel

from .extractors.qwen_extractor import QwenExtractor


class ExtractRequest(BaseModel):
    text: str
    announcement_id: str
    file_id: str = ""
    source_file: str = ""


app = FastAPI(title="招投标信息抽取 API")
extractor = QwenExtractor()  # 从环境变量读 Key；缺 Key 启动即报错


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract")
def extract_data(request: ExtractRequest):
    return extractor.extract(
        request.text,
        request.announcement_id,
        request.file_id,
        request.source_file,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ai_extraction.api:app", host="0.0.0.0", port=8000)
