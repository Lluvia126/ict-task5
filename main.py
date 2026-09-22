from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import upload, extract

app = FastAPI(title="行业数据智能分析引擎", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(extract.router)

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "procurement-engine"}