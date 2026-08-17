# A3 文档共编草案 · SKILL.md / README 待改进清单 + 草案

> 状态：共编中（老板选「chat 内共编：我草案你改」）
> 关联决策卡：A3 推荐 ① chat 内共编

## 1. 待改进清单（来源：路线图 3 项未勾 + 老板反馈）
| # | 项 | 现状 | 优先级 |
|---|---|---|---|
| D1 | 多工具适配壳（OpenClaw/Trae 薄壳） | 路线图未勾 | 中 |
| D2 | `loop.py --help` 子命令说明 | 仅 `--version` | 低 |
| D3 | 断线重连 | 仅 `pull` 容错 | 中 |
| D4 | 入口硬约束/I-10 醒目示例 | **已落地**（2026-08-17，实例加进 SKILL.md 入口硬约束段） | 低 |
| D5 | 安装脚本失败自检 | 已写，缺校验提示 | 低 |

## 2. 草案（供老板改/补）
### D1 多工具适配壳（拟加到 SKILL.md「路线图」段）
> 核心已是标准 HTTP 接口（`init/pull/reply/end`），OpenClaw / Trae 等工具接入只需一层薄壳：
> 壳负责把工具的"发消息"映射到 `loop.py reply`、"收消息"映射到 `pull`；思考仍在本侧 LLM。
> 薄壳示例（伪代码）见 `server/legacy/` 或后续 `skill/adapters/`。

### D2 --help（拟加到 loop.py + SKILL.md）
> `loop.py` 增加 `--help` 输出 4 方法用法；`python loop.py <方法> --help` 显示该方法参数。

### D3 断线重连（拟加到 SKILL.md「工程细节」）
> 当前仅 `pull` 容错（ConnectionError 捕获续轮询）；`init/reply/end` 依赖服务在线。
> 可选增强：`_req` 重试已覆盖瞬断；长断线可由调用侧（本对话）重跑 `init` 恢复。

### D4 入口硬约束示例（拟加到 SKILL.md）
> ❌ 反例：裸跑 `python loop.py pull`（没调 Skill = 违规）。
> ✅ 正例：`Skill` 调 `agent-meeting` → 再跑其 `pull` 命令。
> 漏拉=伪收口：曾发生"拉到消息没回复"被老板抓，教训：抓到必须回。

## 3. 共编方式
- 老板逐条改/补上述草案（或直接说"D1 这么写…"）。
- 我实时落到 `skill/SKILL.md` / `skill/README.md` 并推 GitHub。
- 改完即测（py_compile / --version / pull 实弹），循环到全绿。

## 4. 待老板动作
- 从 D1~D5 选要写哪几条，或给具体文字，我落地。
