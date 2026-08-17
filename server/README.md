# Agent Hub（A 原型 · 腾讯文档方案书 FastAPI 实现）

> 本目录是**按腾讯文档《项目可行性与技术实施方案书 · 本地多 Agent 群聊网页端（Agent Hub）v1.0 MVP》忠实实现的独立可运行原型**，用于老板评估"他们的底座"效果。
> **与 `会议系统/server.py`（B，端口 5000）完全独立、互不影响**，B 作为回退保留不动。

## 与 B（我们的会议系统）的差异（对照用）
| 维度 | A 原型（本目录） | B（会议系统/server.py） |
|---|---|---|
| 框架 | FastAPI + Uvicorn | Flask + gevent |
| 存储 | 本地 JSON 文件（持久化） | 纯内存（重启即丢） |
| 已读回执 | ✅ 原生（✓已读 / N/N 已读） | ❌ 无 |
| 消息路由 | 服务端按 target 路由 | 全量下发 + 客户端@过滤 |
| 会议生命周期 | ❌ 无 | ✅ 阶段机 + #结束会议 + reset |
| 回复捎带 | ✅ reply 响应带回未读 | ❌ 固定 3s 轮询 |
| 幂等 | ✅ client_msg_id | ❌ 无 |

## 运行
```bash
# 依赖（一次性）
pip install -r requirements.txt
# 或用自己的 venv

# 启动服务（端口 8000）
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000

# 另开一个终端，启动演示 Agent（让群聊有真实回复 + 看已读效果）
python agent_client.py
```

## 接口（方案书 §5.1）
- `POST /api/agents/register` 注册 Agent
- `GET  /api/agents` 列表
- `GET  /api/messages/pull?agent_name=` 拉取未读（标记已读）
- `POST /api/messages/reply` 提交回复（捎带未读）
- `POST /api/messages/send` 发送用户消息
- `GET  /api/messages/history` 聊天历史（含 read_by）

## 数据文件（运行时生成，在 data/）
- `agents.json` / `messages.json` / `reads.json`

## 注意
- 本原型**不含会议语义**（无 #结束会议 / 阶段机 / reset）——那是 B 的能力，后续融合时再叠到 A 底座上。
- 单用户、本机/内网使用，未做鉴权（方案书 MVP 亦如此）。
