"""
AI 抽取适配层。
调用刘的 HTTP 服务 POST /extract。
"""

import httpx
from services.config import AI_SERVICE_URL


def extract(text: str, announcement_id: str, file_id: str = "", source_file: str = "") -> dict:
    """
    调用刘的 AI 抽取服务。
    输入：文本 + 三个透传字段
    输出：约定的 JSON 结构
    """
    payload = {
        "text": text,
        "announcement_id": announcement_id,
        "file_id": file_id,
        "source_file": source_file,
    }

    url = f"{AI_SERVICE_URL}/extract"

    try:
        resp = httpx.post(url, json=payload, timeout=60.0)
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise RuntimeError(f"AI 抽取服务超时: {url}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"AI 抽取服务返回错误 {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise RuntimeError(f"无法连接 AI 抽取服务 {url}: {e}")

    return resp.json()


def check_ai_service() -> dict:
    """检查 AI 服务健康状态，方便调试。"""
    try:
        resp = httpx.get(f"{AI_SERVICE_URL}/health", timeout=5.0)
        return {"ok": resp.status_code == 200, "status": resp.status_code, "body": resp.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}