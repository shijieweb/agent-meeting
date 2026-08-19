# -*- coding: utf-8 -*-
"""Agent Hub 配置项（数据目录、服务地址等）。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 支持通过环境变量 DATA_DIR 覆盖数据目录（向后兼容：缺省仍指向 BASE_DIR/data）。
# 用于隔离自测（如 8011 实例指向独立 data 目录，避免污染生产 8000）。
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))

# ---- 二期 v6：SQLite 存储层（仅新增，不修改任何既有常量）----
DB_FILENAME = "agent_meeting.db"   # 单文件库，落 DATA_DIR；gitignored；零运维、不引入外部服务

# Agent 建议轮询间隔（秒），仅作提示，前端/agent 自行实现
POLL_INTERVAL = 3

# 回复长度上限（字符级，与 created_at 同口径的 len(content)）。
# 提高到 4000：老板常见长回复（~500 字）有充足余量，不会误伤；
# 同时作为 reply 接口的业务上限（>4000 返回 400），避免超大内容撑爆 messages.json。
# 注意：绝不使用旧 100 字阈值做简单拒长回复（F11 红线）。
REPLY_MAX_LEN = 4000

# ---- 在线/离线状态自动化（presence 管理）----
ONLINE_WINDOW = 1200                 # 在线窗口（秒），统一=失联阈值，消除 600/120 双窗口闪烁
LOST_TIMEOUT = 1200                  # 失联阈值：>20min 无任何正常请求 = 失联（老板拍板 §5.1-1）
# F-g.1：离线判定窗口（session=1 时），默认 7200s（10 分钟→2 小时，老板拍板 §3.5）。
# 前端 via GET /api/config 读取 offline_window，不再硬编码 1200（design §0.7/§5.4）。
OFFLINE_WINDOW_SECONDS = 7200
LOST_GRACE_BEFORE_DELETE = 6 * 3600  # 失联/离线保留期：6h（老板拍板 §5.1-2）
# 惰性清扫节流：60s 内最多真正扫描一次（老板拍板 §5.1-5）。
# 支持环境变量覆盖（默认 60）：隔离集成测试设 0 让每次 status 都真正清扫；生产不设置。
SWEEP_INTERVAL = int(os.environ.get("SWEEP_INTERVAL", "60"))
# 删除时点 = last_seen 距今 > LOST_TIMEOUT + LOST_GRACE_BEFORE_DELETE（= 6h20min）

# ---- 文档协作系统·一期（T-agent-meeting-upload，design v2.5 §五/§八）----
# 外网下载 URL 前缀：走 8787 反代（老板 02:15 给）。DB 只存相对 path，
# 运行时拼 <EXTERNAL_BASE_URL>/api/docs/<id>/download（AC-11），DB 绝不存完整外网 URL。
# 注意反代 /meeting 前缀须正确透传后端 /api（AC-11.2 部署实测）。
EXTERNAL_BASE_URL = os.environ.get(
    "EXTERNAL_BASE_URL", "http://agnes.owen1.de5.net/meeting"
).rstrip("/")

# 额外 super-admin 账号补充（env，逗号分隔）。默认 []：**不默认任何 agent**
# （老板 02:14 纠正原 ["xiaobian"] 写错）。super-admin 主体 = 人类网页操作员
# （sender_type == "user" 恒为 super-admin），本项仅作额外账号补充。
SUPER_ADMINS = [s.strip() for s in os.environ.get("SUPER_ADMINS", "").split(",") if s.strip()]

# 人类网页操作员归属哨兵：人类上传文档的 owner 值（owner_type 恒为 "user"）。
HUMAN_OWNER = os.environ.get("HUMAN_OWNER", "user")

# 上传约束：单文件 ≤5MB（超出 413）；落盘子目录 = DATA_DIR/uploads/。
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", str(5 * 1024 * 1024)))
UPLOAD_SUBDIR = "uploads"

# GET /api/docs 分页默认值（azhu #11 / AC-21）。
DOC_LIST_DEFAULT_LIMIT = 50
DOC_LIST_MAX_LIMIT = 200
