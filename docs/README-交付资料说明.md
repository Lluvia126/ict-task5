# 本文件夹说明（后端部署交付件）

> 给运维/同学看的独立资料包，**不需要访问 GitHub**。
> 来源仓库：`D:\ICT\procurement-engine\ict-task5`（分支 backend-dev，最新提交 a314cd8 + 本次 checklist）

## 一、文件清单

| 文件 | 用途 |
|---|---|
| **运维部署Checklist.md** | ★先看这个。从"买机器 → 装好"的逐步勾选清单，含每步命令与验收标准、常见问题速查 |
| **云主机部署手册.md** | 详细部署手册（方案 C：学生云主机 24/7）：买机器、Neo4j、systemd、Cloudflare 隧道、数据迁移、Docker 版、11 个坑 |
| **公网部署与安全说明.md** | 公网方案总说明：架构图、内网穿透方案对比、**接口安全策略**（管理口令/配额/上传限制）、数据保留与备份、前端配合点、演示前检查清单 |
| Dockerfile | 后端容器镜像定义（python:3.12-slim，默认走清华 PyPI 源，可用 `--build-arg PIP_INDEX_URL=` 覆盖） |
| docker-compose.yml | 一键起 `neo4j + backend + cloudflared`（可选 `ai`）：`docker compose up -d` / 含 AI：`docker compose --profile ai up -d` |
| .dockerignore | （**隐藏文件**）保证 `.env`、`data/`、`.venv` 不进镜像 |
| requirements.txt | Python 依赖清单（Dockerfile 要用它） |
| ict-task5-backend-20261016.zip | 后端源码包（含上述所有文件 + 源码 + 脚本 + 交付说明.md），传到云主机解压即可用 |

## 二、怎么用

**要给云主机部署（运维）**：
1. 打开 `运维部署Checklist.md`，从第 0 步开始逐项勾选（每步都有命令和验收标准）
2. 遇到细节疑问查 `云主机部署手册.md`
3. 需要判断"哪些接口对外、口令怎么配"看 `公网部署与安全说明.md` 第 3 节
4. 源码包 `ict-task5-backend-*.zip` 直接上传到云主机 `/opt/ict-task5/` 解压

**要用 Docker 部署（可选）**：`Dockerfile` + `docker-compose.yml` + `.dockerignore` + `requirements.txt` 一起放进源码目录根，然后 `docker compose up -d`
> 国内务必先配镜像加速器，否则拉 `python:3.12-slim` 会超时（手册第 9 节有配置）

## 三、开工前需要凑齐的信息

- [ ] 云主机 IP / SSH 账号密码
- [ ] 域名（已实名认证 + NS 已接入 Cloudflare 且 Active）
- [ ] Cloudflare 隧道 token + Public Hostname 指向 `127.0.0.1:8000`
- [ ] 前端 Pages 域名（填进后端 `ALLOWED_ORIGINS`）
- [ ] `QWEN_API_KEY`（AI 服务同机部署时需要，来自前端同学）

## 四、注意事项

- 隧道 token、`ADMIN_TOKEN`、Neo4j 密码都属于密钥：**只离线保存，不要提交 Git、不要发群**
- `.dockerignore` 是隐藏文件（资源管理器需勾选"显示隐藏文件"），别漏拷
- 这份资料是快照：如仓库有更新，重新从 `docs/` 和仓库根目录拷一份即可