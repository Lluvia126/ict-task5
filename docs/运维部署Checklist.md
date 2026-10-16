# 部署施工 Checklist（运维照着勾，从买机器到装好）

> 目标：云主机 24/7 承载后端，评委通过 `https://api.你的域名` 访问；前端在 Cloudflare Pages。
> 全过程约 1~2 小时。每一步都有【验收】标准，勾不上就别往下走。

---

## 0. 开工前必须先拿到的 6 样东西

- [ ] 云主机登录方式：IP = `________`、用户 = `________`、密码/密钥 = `________`
- [ ] 域名：`________`（已注册 + **实名认证已通过**）
- [ ] Cloudflare 账号（域名已接入，后台状态显示 **Active**）
- [ ] 前端 Pages 域名：`https://________`（用于后端 CORS 白名单）
- [ ] `QWEN_API_KEY`：`________`（AI 服务也部署到云上时需要，来自前端同学）
- [ ] 后端源码包：`dist\ict-task5-backend-<日期>.zip`（本机 `D:\ICT\procurement-engine\ict-task5\dist\`）

---

## 1. 买机器

- [ ] 厂商：腾讯云 / 阿里云 **学生机**（腾讯云校园计划 / 阿里云飞天加速计划）
- [ ] 配置：**2 核 2G**、40G 系统盘、**Ubuntu 22.04 或 24.04 LTS 64 位**
- [ ] 地域：任意（走隧道**不需要备案**，选就近即可）
- [ ] 时长：买 1~2 个月（评审结束可退订）
- [ ] 记录：公网 IP、登录口令/密钥

【验收】本地能 SSH 登录：`ssh ubuntu@<IP>`（腾讯云可能是 `root@`）

---

## 2. 云控制台：安全组只开 SSH

- [ ] 入站规则**只保留 22（SSH）**
- [ ] 确认**没有** 8000 / 8001 / 7474 / 7687 的入站规则

【验收】用在线端口扫描（或本机 `nmap -Pn <IP>`）扫描，只有 22 开放。
> 隧道是**出站**连接，不需要任何 Web 入站端口；开放 8000 反而会让同网段的人绕过隧道直连。

---

## 3. 域名 + Cloudflare 隧道（免备案的关键）

- [ ] 域名实名认证通过（国内注册商 1~2 天，**越早越好**）
- [ ] 在注册商处把 NS 改成 Cloudflare 给的两个地址 → 后台显示 **Active**
- [ ] Cloudflare → **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared** → 名称 `caizhi`
- [ ] 复制 **token**：`________________________________`（等同密钥，别发群、别提交 Git）
- [ ] 同页面加 **Public Hostname**：`api.你的域名` → 类型 `HTTP` → URL `127.0.0.1:8000`
- [ ] 保存

【验收】第 7 步做完后 `curl -s https://api.你的域名/api/health` 返回 JSON

---

## 4. 云主机基础环境

- [ ] `sudo apt update && sudo apt -y upgrade`
- [ ] `sudo apt -y install python3 python3-venv python3-pip unzip curl`
- [ ] pip 换清华源（国内必做，否则装依赖极慢）
      `python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`
- [ ] 装 Neo4j（apt 方式，不依赖 Docker Hub）：
```bash
curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
echo "deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest" | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt update && sudo apt -y install neo4j
sudo neo4j-admin dbms set-initial-password '<强密码>'
```
- [ ] 限制内存（2G 机器必做，否则 Neo4j 会被 OOM kill）：编辑 `/etc/neo4j/neo4j.conf`
```ini
server.memory.heap.initial_size=256m
server.memory.heap.max_size=512m
server.memory.pagecache.size=256m
```
- [ ] `sudo systemctl enable --now neo4j && sudo systemctl restart neo4j`

【验收】`systemctl is-active neo4j` → `active`；`python3 -V` → **≥ 3.10**（代码用到 `list | None` 语法）

---

## 5. 部署后端

- [ ] 上传源码包（本机执行）：`scp dist\ict-task5-backend-<日期>.zip ubuntu@<IP>:/opt/ict-task5/`
      若目录不存在先建：`sudo mkdir -p /opt/ict-task5 && sudo chown $USER /opt/ict-task5`
- [ ] 解压：`cd /opt/ict-task5 && unzip ict-task5-backend-*.zip`
- [ ] 建虚拟环境并装依赖：
```bash
python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt
```
- [ ] `cp .env.example .env && nano .env`，按【附录 A】逐项填写
- [ ] 手工前台启动验证：
```bash
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

【验收】另开一个 SSH 窗口：`curl -s localhost:8000/api/health`
必须看到 `"status":"ok"` 且 `"admin_token_configured":true`

---

## 6. 后端做成 systemd 服务（开机自启）

- [ ] 新建 `/etc/systemd/system/ict-task5.service`：
```ini
[Unit]
Description=ICT Task5 backend (FastAPI)
After=network-online.target neo4j.service
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/ict-task5
ExecStart=/opt/ict-task5/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable --now ict-task5`
- [ ] （可选，AI 同机部署）再建一个 `ict-task5-ai.service`，只改两处：
      `ExecStart=.../uvicorn ai_extraction.api:app --host 127.0.0.1 --port 8001`

【验收】`systemctl status ict-task5 --no-pager` → `active (running)`；
重启机器后 `curl -s localhost:8000/api/health` 仍然 200

---

## 7. Cloudflare 隧道常驻

- [ ] 安装并注册成服务：
```bash
curl -L -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo install -m 755 cloudflared /usr/local/bin/cloudflared && rm cloudflared
sudo cloudflared service install <第3步的token>
sudo systemctl enable --now cloudflared
```

【验收】`systemctl status cloudflared --no-pager` → `active`；
`curl -s https://api.你的域名/api/health` → 200（手机 4G 打开 `https://api.你的域名/docs` 也能看到接口文档）

---

## 8. 数据迁移（把已解析入库的数据搬过去）

- [ ] 原后端机器上备份：`.venv\Scripts\python.exe -m scripts.backup_data`
- [ ] 传数据（本机执行）：
```bash
scp data/backups/<时间戳>/results.db ubuntu@<IP>:/opt/ict-task5/data/
scp data/backups/<时间戳>/pairs.db   ubuntu@<IP>:/opt/ict-task5/data/
scp -r data/backups/<时间戳>/uploads ubuntu@<IP>:/opt/ict-task5/data/
```
- [ ] 云主机上重建图谱：`cd /opt/ict-task5 && .venv/bin/python -m scripts.import_to_neo4j`

【验收】`curl -s localhost:8000/api/analysis/graph/status` → `"in_sync":true`；
`curl -s localhost:8000/api/extractions | grep -o announcement_id | wc -l` 条数与原机器一致

---

## 9. 上线自检（本机 Windows 执行）

- [ ] 一键自检全绿：
```powershell
powershell -ExecutionPolicy Bypass -File scripts\public_ready_check.ps1 `
  -ApiUrl https://api.你的域名 -FrontendOrigin https://前端Pages域名
```
- [ ] 手机 4G（非校园网）打开前端页面：能查询、能看图谱、能下载
- [ ] 导出的 CSV 用 Excel 打开**不乱码**（UTF-8 BOM 生效）
- [ ] 无口令删除被拒：`curl -X DELETE https://api.你的域名/api/extractions` → **401**
- [ ] 配额生效：连续抽取超过上限 → **429**

---

## 10. 交付与回滚

- [ ] 把以下信息写进《运维信息表》（**只离线保存，不要提交 Git、不要发群**）：
      IP / 域名 / 隧道 token / `ADMIN_TOKEN` / Neo4j 密码 / SSH 口令
- [ ] 云主机做一次快照或镜像备份（控制台一键）
- [ ] 记录部署时间与执行人：`________`
- [ ] 回滚方案：前端 `VITE_API_BASE_URL` 指回旧地址 + 启动旧后端即可；
      云主机侧 `systemctl stop ict-task5 cloudflared` 即下线
- [ ] 告知前端：API 地址已切换为 `https://api.你的域名`，需要重新 build

---

## 附录 A：`.env` 填写对照表

| 变量 | 填什么 | 从哪来 |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | 固定 |
| `NEO4J_USER` | `neo4j` | 固定 |
| `NEO4J_PASSWORD` | 第 4 步设的强密码 | 自己设 |
| `AI_SERVICE_URL` | 同机 AI → `http://127.0.0.1:8001`；留在前端那台 → 它的局域网 IP | 二选一 |
| `QWEN_API_KEY` | 前端同学提供 | 前端 |
| `QWEN_MODEL` | 默认 `qwen-vl-max` | 与前端确认 |
| `ADMIN_TOKEN` | 随机串：`python3 -c "import secrets;print(secrets.token_urlsafe(24))"` | 生成 |
| `PUBLIC_MODE` | `1` | 固定 |
| `ALLOW_ANON_UPLOAD` / `ALLOW_ANON_EXTRACT` | `1`（评委要自助测试任务三）；`0` = 仅管理员 | 按需 |
| `QUOTA_*` | 默认 10 / 20 / 300 | 可调 |
| `MAX_UPLOAD_MB` | `100` | 固定 |
| `ALLOWED_ORIGINS` | `https://前端Pages域名` | 前端 |
| `GRAPH_SYNC_MODE` | `auto` | 固定 |
| `TUNNEL_TOKEN` | 第 3 步的 token（用 docker compose 时才需要） | Cloudflare |

## 附录 B：常见问题速查

| 现象 | 原因 / 处理 |
|---|---|
| `docker pull` 卡住 / TLS handshake timeout | Docker Hub 国内不可达 → 配镜像加速器（`https://docker.m.daocloud.io` 实测可用）或改用 apt 装 Neo4j |
| `pip install` 很慢 | 没配清华源（第 4 步） |
| Neo4j 容器/服务被 OOM kill | 2G 内存必须限堆（第 4 步 conf） |
| 访问域名 502 / 1033 | 隧道 Public Hostname 没配或指向错（应为 `127.0.0.1:8000`） |
| 破坏性接口返回 503 | `.env` 里 `ADMIN_TOKEN` 为空，公网模式的**故意**安全兜底 → 填上口令重启 |
| 抽取接口 500 | `AI_SERVICE_URL` 不对 / AI 服务那台没开机 → 改 `.env` 重启后端 |
| 抽取出 429 | 配额用完（每 IP 每天 20 次、全局 300 次）→ 管理员带 `X-Admin-Token` 不受限 |
| 前端报 CORS 错误 | `ALLOWED_ORIGINS` 没写前端域名（要带 `https://`，不要带结尾斜杠） |