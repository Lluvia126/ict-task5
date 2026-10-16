# 后端镜像：FastAPI + 抽样结果/导出 + 图谱同步（Neo4j 由 compose 单独起）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# curl 用于容器内健康检查；tzdata 让日志时间正确
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# 国内网络直连 PyPI 极慢，默认走清华源；海外机器可覆盖：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple -t ict-task5-backend .
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# 容器内监听 0.0.0.0，但 compose 不对外发布端口，只有同网络的 cloudflared 能访问
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]