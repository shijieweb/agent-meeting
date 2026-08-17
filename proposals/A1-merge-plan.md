# A1 合并计划 · 历史原型归入 monorepo/server/legacy

> 状态：**已拍板（2026-08-17）** — 老板规则：废弃且后续用不到 → 不合入；能用到 → 合入，且须说明「如何合入」+「后续合入计划」。
> 关联决策卡：A1 推荐「并入 legacy/ + 标注废弃」

## 1. 老板拍板规则（2026-08-17）
- 废弃 & 后续用不到 → **不合入**（留原处 / 归档）。
- 能用到 → **合入**，且必须写清：① 如何合入（步骤）② 后续合入计划（可复用标准动作）。

## 2. 逐文件定性（已执行）
| 文件 | 定性 | 处置 | 落地 |
|---|---|---|---|
| `会议系统/server.py`（Flask 5000） | 废弃、被 FastAPI 8000 取代、后续用不到 | **不合入** | 留 `会议系统/`（死代码，建议日后归档 `_archive_`） |
| `会议系统/agent_sim.py`（多 Agent 模拟） | 评审流(M7.3 / #29)本地联调要用 | **合入 legacy/** | ✅ 已 cp 到 `server/legacy/` |
| `会议系统/boss_driver.py`（老板驱动脚本） | 评审流本地联调要用 | **合入 legacy/** | ✅ 已 cp 到 `server/legacy/` |

## 3. 如何合入（已执行步骤）
1. `mkdir -p agent-meeting/server/legacy`
2. `cp` 两文件（跨仓库，无法 `git mv` 保历史；原路径记 README）：
   ```bash
   cp 会议系统/agent_sim.py   agent-meeting/server/legacy/
   cp 会议系统/boss_driver.py agent-meeting/server/legacy/
   ```
3. 写 `server/legacy/README.md`：标注协议(Flask 5000 room)、依赖(stdlib / requests)、状态(待适配 8000)、用法、后续合入计划。
4. 顶层 `README.md` 目录树加 `legacy/` + 新增「📦 历史原型」段。
5. 校验：`python -m py_compile server/legacy/*.py` ✅
6. 提交推送（monorepo）。

## 4. 后续合入计划（标准动作）
### 4.1 本两文件的适配（当 M7.3 并入 8000 时）
- `BASE` 5000 → 8000；端点 room → agents/messages（详见 `server/legacy/README.md`「后续合入计划」）。
- 适配后移出 legacy/ 进 `server/app/` 对应 router，删本目录副本。

### 4.2 通用「原型合入 monorepo」SOP（沉淀可复用）
1. **定性**：废弃不用 → 不合入；能用到 → 合入。
2. **落点**：可维护代码 → `server/`（模块）或 `skill/`；暂不能上生产的参考件 → `server/legacy/`（须标注协议 / 依赖 / 状态）。
3. **动作**：跨仓库 `cp`（原路径记 README）+ 写 README 标注 + 顶层 README 加索引。
4. **校验**：`py_compile`；不接生产主路径则零运行风险。
5. **提交**：monorepo `git add` + `commit` + `push`；源仓库不强行删（monorepo 作权威）。

## 5. 风险与回滚
- legacy 不接 8000 主路径 → 零运行风险。
- 复制可逆：删 `server/legacy/` 即回退。
- 不影响生产、不影响 `skill/` 安装。
