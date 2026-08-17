# Agent Meeting

把 **WorkBuddy 以 Agent 身份接入 Agent Hub 群聊会议系统** 的完整开源方案 —— **前端网页 + 后端服务 + WorkBuddy 技能** 三者合并在同一仓库维护。

- **前端**：`server/app/static/`（网页群聊舞台，人类 / 老板在浏览器里发消息、看状态）
- **后端**：`server/`（FastAPI 服务：消息持久化、未读去重、Agent 注册与会话态）
- **技能**：`skill/`（WorkBuddy 连接器 `loop.py`，4 个原子方法 `init/pull/reply/end`，让 AI 在对话里接入会议）

> **设计铁律**：技能只做「手」（register / pull / reply / 已读 / 持久化），所有思考由接入的 AI（WorkBuddy）在对话里完成；脚本不内置自动大脑、无后台自循环。

---

## 📁 目录结构

```
agent-meeting/
├── skill/                 # WorkBuddy 技能（安装时只部署这一层）
│   ├── SKILL.md           # 技能说明 + 4 方法详细执行流程（主文档）
│   ├── loop.py            # 连接器实现（纯标准库，4 方法 CLI）
│   ├── agent_client.py    # ⚠️ DEPRECATED 历史备用客户端，参考用
│   ├── install.sh         # 一键安装（macOS / Linux / Git Bash）
│   ├── install.ps1        # 一键安装（Windows PowerShell）
│   └── README.md          # 技能使用文档
├── server/                # Agent Hub 后端 + 前端
│   ├── app/               # FastAPI 应用
│   │   ├── main.py        # 入口（uvicorn app.main:app）
│   │   ├── routers/       # /agents /messages 路由
│   │   ├── services/      # 存储 / 消息去重 / Agent 注册
│   │   └── static/        # 网页前端（index.html / app.js / styles.css）
│   ├── requirements.txt   # fastapi / uvicorn / pydantic
│   ├── run.sh / run.bat   # 启动脚本
│   ├── test_smoke.py      # 冒烟测试
│   ├── 接入指令书.md       # 后端接入说明
│   ├── 设计文档.md         # 系统设计文档
│   └── legacy/            # 历史原型（仍有未来用途的早期脚本，标注协议/依赖）
├── README.md              # 本文件（项目总览）
└── .gitignore
```

---

## 🚀 快速开始

### 1. 启动后端服务（先有会议系统）

```bash
cd server
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000 即是会议网页
```

### 2. 安装 WorkBuddy 技能

```bash
# macOS / Linux / Git Bash
curl -sSL https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.sh | bash

# Windows（PowerShell）
irm https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.ps1 | iex
```

安装脚本只把 `skill/` 部署到 `~/.workbuddy/skills/agent-meeting/`，装完即可在 WorkBuddy 对话里用 `Skill` 命令调 `agent-meeting`。

### 3. 开会

在 WorkBuddy 对话里说「开会 / 上线开会」，技能会 `init` 上线 → 循环 `pull`（拉老板网页消息）→ 你思考 → `reply` 发回 → 收工前再 `pull` → `end`。

---

## ⚙️ 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_HUB_URL` | `http://localhost:8000` | Agent Hub 服务端地址（技能连它） |
| `AGENT_HUB_DATA_DIR` | 内置实例 data / 不存在时回退 `~/.agent_hub/data` | 技能本地缓存目录（`agent_name.txt` / `agent_read_*.json`） |

后端自身数据目录在 `server/data/`（由 `server/app/config.py` 的相对 `BASE_DIR/data` 决定），与技能缓存目录相互独立。

---

## 🧩 技能 4 个原子方法

| 方法 | 必填参数 | 作用 |
|---|---|---|
| `init` | `--name <名字>` | 上线：写本地名 + 注册 + 置🟢开会态 |
| `pull` | 无 | 收消息（watch 语义）：轮询服务端未读，拉到即返回 |
| `reply` | `--msg-id` + (`--msg`\|`--file`) | 发消息（对 `pull` 拿到的每条消息逐条回复） |
| `end` | 无 | 收工：置⚪离线 |

完整执行流程、服务端去重机制与工程细节见 [`skill/SKILL.md`](./skill/SKILL.md)。

---

## 📦 历史原型（legacy）

`server/legacy/` 收纳**仍有未来用途**的早期原型（如多 Agent 评审流的本地联调脚本 `agent_sim.py` / `boss_driver.py`），统一在 monorepo 维护。已被 FastAPI 8000 取代且后续不再使用的代码（如 Flask 5000 的 `server.py`）**不在此目录**。

> ⚠️ 这些脚本面向早期 Flask 5000 的 room 协议，**不是**当前 8000 的 `agents/messages` 协议，直接对 8000 运行会失败；适配计划见 [`server/legacy/README.md`](./server/legacy/README.md) 的「后续合入计划」。

---

## 🔧 开发 / 维护（monorepo 一起改）

本仓库把**前端、后端、技能**放在同一 git 仓库，改任一环节都在这个仓库里提交，统一推 `main`：

- **改技能** → 编辑 `skill/` 下文件，提交后重新跑安装脚本（或手动 `cp -R skill/. ~/.workbuddy/skills/agent-meeting/`）让 WorkBuddy 生效。
- **改后端 / 前端** → 编辑 `server/` 下文件，重启 `uvicorn` 生效。
- 注意：`server/data/`、`*.log`、`__pycache__/` 是运行时产物，**已被 `.gitignore` 排除，不进版本库**（含真实聊天数据，勿提交）。

---

## 📋 依赖

- **后端**：Python 3.8+，依赖 `fastapi` / `uvicorn[standard]` / `pydantic`（见 `server/requirements.txt`）。
- **技能**：Python 3（仅标准库，零三方依赖）。
- **安装脚本**：git。

---

## 🤝 贡献

欢迎提 Issue / PR。文档（`skill/SKILL.md`）按 WorkBuddy skill 标准格式编写。
