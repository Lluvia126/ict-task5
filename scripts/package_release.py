"""打包后端提交版源码：scripts/package_release.py

    .venv\\Scripts\\python.exe -m scripts.package_release
    -> dist/ict-task5-backend-YYYYMMDD.zip

排除：.git / .venv / __pycache__ / 数据库与上传数据 / 大体积样本文件 / .env（只留 .env.example）
包含：后端源码、依赖清单、数据库初始化说明、导出接口、部署与安全文档、示例数据。
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".idea", ".vscode", ".agents", ".codex", ".aws",
    "dist", "logs", "node_modules",
    "data/backups", "data/uploads", "data/results", "data/.tmp",
    "test_data/official_samples",          # 约 109MB 样本文件，单独交付
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".pyd", ".db", ".sqlite3", ".log", ".zip", ".rar", ".7z"}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db", "data/counter.json", ".sec_check.txt"}

DELIVERY_NOTE = """# 后端交付说明（{date}）

## 一、目录结构
```
main.py                  FastAPI 入口（含 CORS 白名单、Neo4j 异常处理）
routers/                 接口层：upload / extract / pair / analysis / export
services/                业务层：
  file_parser.py         多格式解析（HTML/PDF/DOCX/XLSX/CSV/ZIP/老式 doc-xls）
  ai_adapter.py          调用 AI 抽取服务（地址见 .env 的 AI_SERVICE_URL）
  storage.py             抽取结果存储（SQLite，data/results.db）+ 图谱同步状态
  pair_store.py          公告-附件配对（data/pairs.db）
  format_rules.py        输出格式规则：样例表列名/顺序、万元换算、UUID、框架协议与分标段识别
  export_service.py      三表视图构建 + CSV 导出（UTF-8 带 BOM）
  graph_db.py            Neo4j 访问层（关系带 announcement_ids 来源标记）
  graph_queries.py       五大业务查询（按当前有效公告范围过滤）
  graph_sync.py          图谱同步与一致性状态
  security.py            公网防护：管理口令 / 配额 / 上传限制 / 访问留痕
ai_extraction/           AI 抽取服务（Qwen 路线，可独立启动）
scripts/                 运维脚本（见下）
static/swagger/          离线 Swagger UI 静态资源（/docs 用）
docs/                    部署与安全说明
示例数据/                输出标准样例表参考文件（官方要求格式）
requirements.txt         依赖清单
.env.example             环境变量模板（复制成 .env 再填值）
```

## 二、环境与依赖
- Python 3.12（开发环境为 Anaconda 3.12.4）
- `python -m venv .venv` → `.venv\\Scripts\\python.exe -m pip install -r requirements.txt`
- 外部依赖：Neo4j（`docker start neo4j`，bolt://localhost:7687）、AI 抽取服务（局域网/本机 HTTP）

## 三、启动
```powershell
copy .env.example .env          # 填 QWEN_API_KEY / AI_SERVICE_URL / ADMIN_TOKEN 等
docker start neo4j
.venv\\Scripts\\python.exe -m uvicorn ai_extraction.api:app --host 127.0.0.1 --port 8001
.venv\\Scripts\\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```
接口文档：<http://127.0.0.1:8000/docs>

## 四、数据库脚本
不需要手工建表：首次启动时自动创建并升级
- `data/results.db`：`extractions`（抽取结果）、`graph_state`（图谱同步状态）、`quotas`（调用配额）
- `data/pairs.db`：`pairs`（公告-附件配对，含 announcement_id 关联）
- Neo4j 约束由 `services/graph_db.init_constraints()` 建立；图谱可用
  `POST /api/analysis/graph/sync?mode=rebuild` 或 `python -m scripts.import_to_neo4j` 全量重建

## 五、导出接口（对齐《输出标准样例表》）
| 接口 | 说明 |
|---|---|
| `GET /api/export/{{pair_id}}` | 下载该配对的提取结果（总表/标的表/投标人表），UTF-8 带 BOM |
| `GET /api/export/{{pair_id}}?view=summary\\|items\\|bidders` | 单表下载（表头即列名，便于脚本打分） |
| `GET /api/export/{{pair_id}}?format=json` | JSON 形式，供前端渲染 |
| `GET /api/export` | 导出全部抽取结果（官方数据集提取结果） |

## 六、运维脚本
```powershell
.venv\\Scripts\\python.exe -m scripts.import_to_neo4j   # 按抽取结果全量重建图谱
.venv\\Scripts\\python.exe -m scripts.backup_data        # 备份数据库/上传文件/三表 CSV
.venv\\Scripts\\python.exe -m scripts.package_release    # 重新打这个源码包
```

## 七、安全（详见 docs/公网部署与安全说明.md）
- 破坏性接口（删除、清空、图谱重建）需请求头 `X-Admin-Token`
- 上传/抽取有类型、大小与每日配额限制；抽取另有全局每日上限，控制 AI 费用
- 只监听 127.0.0.1，数据库与 AI 服务不暴露公网；跨域白名单可配

## 八、本次未包含
- `.env`（含密钥，需自行配置）、`data/*.db`（运行期数据）、
  `data/uploads/`、官方基准样本（约 109MB，单独交付）、Git 历史。
"""


def _skip(rel: Path) -> bool:
    """判断是否跳过。支持嵌套目录（如 frontend/node_modules/...）。"""
    posix = rel.as_posix()
    if rel.name in EXCLUDE_FILES or posix in EXCLUDE_FILES or rel.suffix.lower() in EXCLUDE_SUFFIX:
        return True

    simple = {d for d in EXCLUDE_DIRS if "/" not in d}
    prefixed = {d for d in EXCLUDE_DIRS if "/" in d}

    # 任意一层目录命中即跳过（node_modules / dist / __pycache__ ...）
    if any(part in simple for part in rel.parts[:-1]):
        return True
    # 多段路径按前缀匹配（data/backups、test_data/official_samples ...）
    if any(posix == d or posix.startswith(d + "/") for d in prefixed):
        return True
    return False


def main() -> int:
    DIST.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    zip_path = DIST / ("ict-task5-backend-%s.zip" % stamp)

    added, skipped = [], 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if _skip(rel):
                skipped += 1
                continue
            bundle.write(path, rel.as_posix())
            added.append(rel.as_posix())
        bundle.writestr(
            "交付说明.md",
            DELIVERY_NOTE.format(date=datetime.now().strftime("%Y-%m-%d")),
        )

    print("打包完成:", zip_path, "(%.1f MB)" % (zip_path.stat().st_size / 1024 / 1024))
    print("文件数:", len(added) + 1, "| 跳过:", skipped)
    print("包含的源码目录:")
    for top in sorted({item.split("/")[0] for item in added}):
        print("  -", top)
    print("校验：.env 是否被打包 ->", any(item == ".env" for item in added))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())