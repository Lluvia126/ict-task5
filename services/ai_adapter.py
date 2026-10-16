"""AI 抽取适配层。调用刘的 HTTP 服务 POST /extract。

后端不感知 EXTRACT_BACKEND，始终发完整 payload：
- V4.1（qwen）路径：AI 侧用 text
- LoRA 路径：AI 侧用 html_text + html_tables + attachments
"""

import httpx
from services.config import AI_SERVICE_URL

REQUEST_TIMEOUT = 300.0


def extract(
    text: str,
    announcement_id: str,
    file_id: str = "",
    source_file: str = "",
    html_text: str = "",
    html_tables: list | None = None,
    attachments: list | None = None,
) -> dict:
    """调用 AI 抽取服务。传结构化 tables，不截断。"""
    payload = {
        "announcement_id": announcement_id,
        "file_id": file_id,
        "source_file": source_file,
        "text": text,
        "html_text": html_text,
        "html_tables": html_tables or [],
        "attachments": attachments or [],
    }

    url = f"{AI_SERVICE_URL}/extract"

    try:
        resp = httpx.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise RuntimeError(f"AI 抽取服务超时（{REQUEST_TIMEOUT}s）: {url}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"AI 抽取服务返回错误 {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise RuntimeError(f"无法连接 AI 抽取服务 {url}: {e}")

    return resp.json()


def check_ai_service() -> dict:
    try:
        resp = httpx.get(f"{AI_SERVICE_URL}/health", timeout=5.0)
        return {"ok": resp.status_code == 200, "status": resp.status_code, "body": resp.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}