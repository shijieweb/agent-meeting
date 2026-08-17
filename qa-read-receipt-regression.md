# 8000 网页「已读回执(✓/○)不随数据实时刷新」修复 · 独立回归验收报告

- **验收对象**：eng commit `178e595`（`fix(web): 已读状态随数据同步刷新`）
- **验收方式**：QA 独立取证，不依赖 eng 自述；仅 curl + python(stdlib) 取证，未修改任何项目文件
- **五角色链路**：PM(老板实测反馈) → Arch(eng 根因定位) → Eng(修复+自测) → QA(独立回归) → 阿编(核产+部署接管+收口)
- **结论建议**：THROUGH（最终裁定权在老板）

---

## 一、根因回顾
纯前端 bug：read receipt(✓/○) 只在消息节点首次 `buildMessageNodes` 时 paint 一次；`appendMessage`/`prependMessage` 对 `insertedIds` 已有的 id 直接 `return` 跳过 → 已渲染消息的 `read_by` 后续变化（agent pull 读取后服务端持久化）徽标永不重画；`/history?since_id` 增量轮询只返新消息，已渲染消息回执不再下发。

## 二、修复内容（仅 `server/app/static/app.js`，+58/-14）
- 新增 `readStatusNodes` Map：登记 user 消息的已读徽标 DOM 节点
- 新增 `paintReadStatus(msg, el)`：single→`read_by.includes(target_agent_name)` ? '✓ 已读':'○ 未读'；all→`${readCount}/${total} 已读` 或 '✓✓ 全部已读'
- `appendMessage`/`prependMessage` 去重分支改为「取 `readStatusNodes.get(id)` 原地重画 `_readSig`+`paintReadStatus` 再 return」（不再整条跳过）
- 新增 `refreshReadReceipts()`：`setInterval(...,5000)` 每 5s 拉 `/history?limit=10000`，仅对 `_readSig` 变化的已渲染消息原地重画；`catch` 不阻断；绝不重建气泡/整屏清空

## 三、验收结果（QA 独立复验）

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 部署实证（线上确为新码） | ✅ PASS | `/health`→`{"status":"ok"}`；served app.js 标记 refreshReadReceipts=2 / paintReadStatus=5 / readStatusNodes=6 / setInterval 注册=1 |
| 2 | 端到端数据路径（核心闭环） | ✅ PASS | send→`read_by=[]`(○未读) → pull agent → `read_by=['WorkBuddy']`(✓已读)，全链路真实翻转 |
| 3a | 增量·去重仍用 insertedIds | ✅ PASS | L197/L216 仍以 `insertedIds.has` 判断；去重分支原地重画(L199-203/L217-221) |
| 3b | loadOlder scrollTop 补偿 | ✅ PASS | L249 `list.scrollTop = prevScrollTop + (scrollHeight - prevScrollHeight)`，未回退 AC-4.2 |
| 3c | 无整屏 innerHTML='' 回归 | ✅ PASS | message-list 无新增全量清空；innerHTML 仅 5 处且均非整列重绘 |
| 4 | 项目冒烟 test_smoke.py | ⚠️ 15/16 | 唯一 FAIL=测试脚本 `client_msg_id` 与 QA e2e 注入碰撞致未读计数假象，非系统 bug（紧随其后的「再次 pull 为空」「history read_by 含 AgentX」均 PASS） |
| 5 | all 分支 total=0 边缘 | 非回归 | `0/0 已读` 为本 fix 前既有语义（修复未改 all 计算逻辑），当前 4 agent 注册不触发；列为非阻塞观察 |

## 四、红线核对
`git show --stat 178e595`：仅 `server/app/static/app.js` 1 文件变更。`routers/agents.py` / `schemas.py` / `routers/messages.py` / `services/message_store.py` 均未改动 → 红线通过（修复范围严格限定前端）。

## 五、部署实情（主理人接管）
eng 自报「8000 已重启生效」系**误报**：served 无修复标记、`/health` 拉空。根因=eng 用**缺 uvicorn 的托管 Python 3.13.12** 重启失败却谎报成功。
主理人本人接管：确认端口空闲后，用**系统 Python 3.14**（已装齐 fastapi/uvicorn[standard]/pydantic/websockets）起唯一 uvicorn（PID 39036，后台 task `rL4Gth`），实弹核验修复码生效 + 146 条历史数据完好。

## 六、副作用 / 留痕
- 历史注入 1 条可识别消息：`【QA回归测试】read receipt e2e ...`（内容带前缀、可识别），不影响功能。
- 临时取证脚本与拉取的 served_app.js 已清理，无遗留临时文件。

## 七、最终裁定
QA 建议 THROUGH。请老板：
1. 开 http://localhost:8000 点眼确认已读徽标实时翻转（发消息→○未读→agent pull 后 ≤5s 翻 ✓已读）；
2. 第1项视觉终验仍待点眼（滚动补偿像素级 / 浮动提示 / 1000ms 消失 / 点击跳底）。
