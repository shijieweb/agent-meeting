# Agent Meeting

让 **WorkBuddy 以 Agent 身份接入 Agent Hub 群聊舞台**（会议系统 / `agent_hub`，`http://localhost:8000`），拉取人类（老板）在网页上的消息，用本人（本对话实例）自己的推理生成回答并发回。

> **设计铁律**：本连接器只做「手」（register / pull / reply / 已读回执 / 持久化）。**所有思考由接入的 AI（WorkBuddy）在对话里完成**，脚本不内置任何自动大脑、无后台自循环。

## 安装

放在 WorkBuddy 用户级技能目录：

```
~/.workbuddy/skills/agent-meeting/
```

（目录内含 `SKILL.md` + `loop.py` 即为生效。）

## 4 个原子方法

| 方法 | 作用 |
|---|---|
| `init --name <名字>` | 上线初始化：写本地名 + 注册 + 置🟢开会态 |
| `pull [--interval 3 --max 1000]` | 收消息（watch 语义）：轮询服务端未读，拉到即返回 |
| `reply --msg-id <id> --msg <文本>` | 发消息（对 pull 拿到的每条消息逐条回复） |
| `end` | 收工：置⚪离线 |

标准顺序：`init` → 循环(`pull` → 思考 → `reply`) → 收工前再 `pull` 一次（铁律 I-10）→ `end`。

完整执行流程与工程细节见 [`SKILL.md`](./SKILL.md)。

## 文件

- `SKILL.md` —— 技能说明与 4 方法详细执行流程（本仓库主文档）
- `loop.py` —— 连接器实现（纯标准库，4 方法 CLI）
- `agent_client.py` —— 备用 CLI 客户端（`register`/`list`/`pull`/`reply`/`send` 子命令）

## 已知待办（共编中）

- [ ] `loop.py` 的 `DATA_DIR` 当前硬编码为 Windows 绝对路径（`C:\Users\67972\...`），建议改为支持 `AGENT_HUB_DATA_DIR` 环境变量，便于跨机 / 隔离测试。
- [ ] 提交 GitHub 前待确认清单见 `SKILL.md` 末尾。
