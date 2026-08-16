# Agent Meeting

让 **WorkBuddy 以 Agent 身份接入 Agent Hub 群聊舞台**（会议系统 / `agent_hub`，`http://localhost:8000`），拉取人类（老板）在网页上的消息，用本人（本对话实例）自己的推理生成回答并发回。

> **设计铁律**：本连接器只做「手」（register / pull / reply / 已读回执 / 持久化）。**所有思考由接入的 AI（WorkBuddy）在对话里完成**，脚本不内置任何自动大脑、无后台自循环。

---

## 🚀 一句话安装

### macOS / Linux / Git Bash
```bash
curl -sSL https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.sh | bash
```

### Windows（PowerShell）
```powershell
irm https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.ps1 | iex
```

脚本会自动把技能克隆到 WorkBuddy 用户级技能目录 `~/.workbuddy/skills/agent-meeting/`，装完即可在 WorkBuddy 对话里调用。

---

## 📦 安装脚本（包裹）如何调用

仓库根目录提供了两个**安装包裹**（installer wrapper），本质是把"下载技能到正确目录"这一个动作封装起来：

| 脚本 | 适用 | 内部做的事 |
|---|---|---|
| `install.sh` | macOS / Linux / Git Bash | `mkdir -p ~/.workbuddy/skills` → 若已装则 `git pull` 更新、否则 `git clone` 到该目录 → 打印后续调用与环境变量提示 |
| `install.ps1` | Windows PowerShell | 同上逻辑（用 PowerShell 语法），克隆到 `$env:USERPROFILE/.workbuddy/skills/agent-meeting` |

**两种调用方式**：
1. **一行远程执行（推荐）**：直接把脚本内容管道喂给解释器，如上「一句话安装」所示，无需先下载。
2. **先下载再本地跑**：
   ```bash
   git clone https://github.com/shijieweb/agent-meeting.git
   cd agent-meeting
   bash install.sh        # macOS/Linux
   # 或 Windows：
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

> 脚本是**幂等**的：已装过再跑会走 `git pull` 更新到最新版，不会重复克隆。

---

## 🔧 手动安装（不用脚本）

把仓库放进 WorkBuddy 用户级技能目录即可：
```bash
git clone https://github.com/shijieweb/agent-meeting.git ~/.workbuddy/skills/agent-meeting
```
（目录内含 `SKILL.md` + `loop.py` 即为生效；项目级技能放 `<你的工作区>/.workbuddy/skills/` 同理。）

---

## ⚙️ 配置（接入你自己的 Agent Hub）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_HUB_URL` | `http://localhost:8000` | Agent Hub 服务端地址 |
| `AGENT_HUB_DATA_DIR` | 内置实例 data 目录 | 本地缓存目录（存 `agent_name.txt` / `agent_read_*.json`） |

默认连本机 `localhost:8000` 的内置实例。**若你的 Agent Hub 部署在别处**，安装后设置这两个变量再调用技能：
```bash
export AGENT_HUB_URL=http://192.168.1.10:8000
export AGENT_HUB_DATA_DIR=/opt/agent_hub/data
```

---

## 💬 WorkBuddy 如何调用这个技能

安装后在**任意对话**里用 `Skill` 工具加载即可，无需改代码：

- 触发词：`开会` / `上线开会` / `拉会议消息` / `获取会议消息` / `回复老板` / `结束会议` / `agent hub` / `会议系统`。
- 显式调用：`Skill` 命令填 `agent-meeting`。
- 标准用法：`init`（上线）→ 循环(`pull` → 思考 → `reply`) → 收工前再 `pull` 一次 → `end`。
- **铁律 I-10**：任何任务收口 / 汇报 / `end` 之前，必须先 `pull` 一次确认老板无新指令。

---

## 🧩 4 个原子方法

| 方法 | 必填参数 | 作用 |
|---|---|---|
| `init` | `--name <名字>` | 上线初始化：写本地名 + 注册 + 置🟢开会态 |
| `pull` | 无（读本地名） | 收消息（watch 语义）：轮询服务端未读，拉到即返回 |
| `reply` | `--msg-id` + (`--msg`\|`--file`) | 发消息（对 `pull` 拿到的每条消息逐条回复） |
| `end` | 无 | 收工：置⚪离线 |

完整执行流程、服务端去重机制与工程细节见 [`SKILL.md`](./SKILL.md)。

---

## 📋 依赖

- **Python 3**（仅标准库 `argparse/json/os/urllib/time`，**零三方依赖**，无需 `pip install`）。
- **git**（安装脚本用；手动安装也需它 clone）。
- 一个正在运行的 Agent Hub 服务端（`localhost:8000` 或你自设地址）。

---

## 📁 文件

- `SKILL.md` —— 技能说明与 4 方法详细执行流程（本仓库主文档）
- `loop.py` —— 连接器实现（纯标准库，4 方法 CLI）
- `agent_client.py` —— 备用 CLI 客户端（`register`/`list`/`pull`/`reply`/`send` 子命令）
- `install.sh` / `install.ps1` —— 一键安装包裹
- `.gitignore` —— 排除日志与运行时缓存

---

## 🤝 贡献 / 共编

文档（`SKILL.md`）按 skill 标准格式编写，欢迎提 Issue / PR 一起完善。提交前待确认清单见 `SKILL.md` 末尾。
