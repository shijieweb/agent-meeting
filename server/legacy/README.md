# 历史原型（legacy）

本目录收纳**仍有未来用途**的早期原型，统一在 monorepo 维护。已被 FastAPI 8000 正式取代、且后续不再使用的代码（如 Flask 5000 的 `server.py`）**不在此目录**，仍留原 `会议系统/`。

> ⚠️ **协议说明**：本目录脚本均面向**早期 Flask 5000 的 room 协议**（`/api/room/...`），**不是**当前 Agent Hub 8000 的 `agents/messages` 协议。直接对 8000 运行会失败，需按下方「后续合入计划」适配。

## 文件清单
| 文件 | 原路径 | 用途 | 依赖 | 状态 |
|---|---|---|---|---|
| `agent_sim.py` | `会议系统/agent_sim.py` | 模拟外部 agent（openclaw）经通用 HTTP 协议接入中转服务，规则化应答（≤100 字），用于**无真实 LLM 时本地联调会议系统** | 仅标准库 | 待适配 8000 |
| `boss_driver.py` | `会议系统/boss_driver.py` | 老板自动驱动器：按阶段关键词发 `/开始提问` `/出方案` `/互相评审` `/合方案` `/结束会议`，驱动多 Agent 评审流 **本地端到端跑通** | `requests` | 待适配 8000 |

## 用法（对早期 Flask 5000 原型）
```bash
# 终端1：起 Flask 5000 原型（会议系统/server.py）
python 会议系统/server.py
# 终端2：模拟一个接入 agent
python server/legacy/agent_sim.py
# 终端3：老板驱动器（自动推进评审相位）
python server/legacy/boss_driver.py
```

## 后续合入计划（标准动作）
当 M7.3 多 Agent 评审工作流并入 Agent Hub 8000（见 `../proposals/A2-review-workflow-merge.md`）时，本目录脚本需适配到 8000 协议：
1. `BASE` 由 `http://localhost:5000` 改为 `http://localhost:8000`。
2. 端点迁移：
   - `/api/room/{ROOM}/join` → `POST /api/agents/register` + `POST /api/agents/{name}/session`
   - `/api/room/{ROOM}/messages` → `GET /api/messages/pull?agent_name=`
   - `/api/room/{ROOM}/message` → `POST /api/messages/reply`
3. 增加注册 / `agent_name` 本地缓存（参考 `skill/loop.py` 的 `resolve_name`）。
4. 评审相位命令（`/开始提问` 等）改为 8000 的消息内容语义，去掉 room/seq 模型。
5. 适配后移出 legacy/ 进入 `server/app/` 对应 router，删本目录副本。

### 通用「原型合入 monorepo」SOP（沉淀，可复用）
后续任何原型 / 实验代码要进 monorepo，一律走：
- **定性**：废弃且后续用不到 → 不合入（留原处或归档）；能用到 → 合入。
- **落点**：可维护代码 → `server/`（对应模块）或 `skill/`；暂不能上生产的参考件 → `server/legacy/`（须标注协议 / 依赖 / 状态）。
- **动作**：跨仓库用 `cp`（无法 `git mv` 保历史，原路径记 README）；加 README 标注；顶层 README 加索引。
- **校验**：`py_compile`；不接入生产主路径则零运行风险。
- **提交**：在 monorepo `git add` + `commit` + `push`，源仓库不强行删（避免双份由 monorepo 作权威）。
