# 端到端界面测试报告 · Agent Hub (localhost:8000)

**测试日期**：2026-08-17 15:4x
**测试对象**：`agent-meeting/server` 网页（Agent Hub 会议系统前端 + FastAPI 后端）
**测试结论**：**14/14 全 PASS** —— 真实无头浏览器端到端验证，所有界面功能正常，已读回执 bug 修复确证闭环。

---

## 一、测试方法（真实界面，非 mock）

- **驱动**：playwright（托管 Python 3.13.12）+ 系统中 `agent-browser` 下载的 Chrome 150，**真实无头浏览器**打开 `http://localhost:8000`。
- **视口**：480×900（竖屏，贴近老板看板实际观感）。
- **断言方式**：`page.evaluate` 直接读 DOM（气泡数、`.read-status` 徽标文本、banner 显隐、scrollTop），配合后端 `/api/messages/history` 双证；每步 `screenshot` 存证。
- **服务**：系统 Python 3.14 uvicorn（PID 新起），`/health`→`{"status":"ok"}`，served app.js 修复标记 `refreshReadReceipts=2`。
- **脚本**：`server/e2e_ui_test/smoke.py`（冒烟）、`e2e.py`（14 项全量）。

---

## 二、测试结果总表（14/14）

| 编号 | 功能点 | 断言 | 结果 | 关键证据 |
|---|---|---|---|---|
| T1 | 页面加载·标题 | `title == "Agent Hub"` | ✅ | Agent Hub |
| T1 | 页面加载·首屏 | 渲染 30 条气泡 | ✅ | `bubbles=30`（首屏 `?limit=30`） |
| T1 | 页面加载·下拉 | `all`+4 agent 共 5 项 | ✅ | `['all','WorkBuddy','AgentX','AgentY','EdgeProbe']` |
| T2 | 发送消息 | 点击发送后新增气泡 | ✅ | `bubbles 30→31` |
| T2 | 初始状态 | single 消息初始 `○ 未读` | ✅ | DOM 实测 `○ 未读` |
| T2 | 后端落库 | `read_by=[]` 且 `target=WorkBuddy` | ✅ | `id=msg_707ee0c31c read_by=[] target=WorkBuddy` |
| **T3** | **已读刷新（核心 bug）** | 本人 pull 后界面翻 `✓ 已读` | ✅ | **DOM `○ 未读`→`✓ 已读`**；后端 `read_by=['WorkBuddy']` |
| T4 | 增量加载 | 触顶加载更早 30 条 | ✅ | `bubbles 31→61` |
| T4 | scrollTop 补偿 | 加载后视口锚定不跳 | ✅ | `scrollTop=5932`（AC-4.2 守住，非 0 抖动） |
| T5 | 浮动提示出现 | 非底部新消息弹 banner | ✅ | `1 条新消息` |
| T5 | 1000ms 消失 | 约 1s 后自动隐藏 | ✅ | `display→none` |
| T5 | 点击跳底 | 点 banner 滚到底部 | ✅ | `clicked=True atBottom=True` |
| T6 | agent 下拉驱动 | 选 AgentX 发送→target=AgentX | ✅ | 后端 `target=AgentX type=single` |

---

## 三、核心 bug 修复验证（老板报的"已读不刷新"）

**完整界面闭环，真浏览器实测**：
1. 选 `agent-select=WorkBuddy`，输入框发一条消息 → 列表新增 user 气泡，**DOM 实测 `.read-status` 显示 `○ 未读`**（初始 `read_by=[]`）。
2. 用 API 模拟本人读取：`GET /api/messages/pull?agent_name=WorkBuddy` → 后端把 `WorkBuddy` 持久化进该消息 `read_by`。
3. 等待 >5s（前端 `refreshReadReceipts` 每 5s 拉 `/history` 全量）→ **同一气泡的 `.read-status` DOM 文本原地翻成 `✓ 已读`**（无重建、无整屏清空）。
4. 后端核验：该消息 `read_by=['WorkBuddy']`。

**结论**：原 bug（已渲染消息的 read receipt 只在首次 build 时 paint 一次、增量去重分支 `return` 跳过导致徽标永不重画）已真实修复，界面行为符合预期。

---

## 四、其他界面功能

- **增量加载 + 滚动补偿**：列表滚到顶自动 `loadOlder` 前置插入更早 30 条（61 条），`scrollTop` 补偿 5932px 保持视口锚定，无抖动、无丢失位置（AC-4.2）。
- **微信式浮动提示**：用户在非底部时新消息到达 → 弹"N 条新消息"banner → 约 1000ms 自动消失 → 点击 banner 立即跳到底部（`atBottom=True`）。
- **agent 下拉选择**：切换 `@WorkBuddy/@AgentX/...` 直接决定发送 `target_type`/`target_agent_name`（后端落库核验一致）。

---

## 五、留痕清理（保持生产数据干净）

- 测试期间共造 **8 条** `【E2E测试】` 消息（脚本两版各 4 条）。
- 已备份 `messages.json`（→ `messages.json.bak_e2e_20260817154942`）→ 停服 → 删除 8 条 → 重启服务。
- 重启后核验：`total=154`、`e2e=0`，修复码仍在（`refreshReadReceipts=2`）。

---

## 六、截图存证（真实浏览器渲染）

| 文件 | 内容 |
|---|---|
| `shot_1_load.png` | 首屏加载（30 条 + 下拉 + 状态点） |
| `shot_2_sent.png` | 发送后新气泡 + `○ 未读` |
| `shot_3_read.png` | 已读刷新后 `✓ 已读` |
| `shot_4_older.png` | 触顶加载更早消息 + scrollTop 补偿 |
| `shot_5_banner.png` | 浮动提示"1 条新消息"出现 |
| `shot_5_jump.png` | 点击跳底后位于底部 |
| `shot_6_agent.png` | 选 AgentX 发送 |

---

## 七、最终裁定

界面功能端到端全绿，已读回执 bug 修复在真实浏览器中确证闭环，数据已清理。该 hotfix **可正式收口**。
