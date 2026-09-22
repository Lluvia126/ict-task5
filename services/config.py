import os
from dotenv import load_dotenv

load_dotenv()

# AI 抽取服务（刘的 HTTP 服务）
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8000")

# 下面几项暂时用不到，先留占位，后面配 .env 再启用
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")