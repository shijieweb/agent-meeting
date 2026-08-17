# -*- coding: utf-8 -*-
"""Agent Hub 配置项（数据目录、服务地址等）。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 支持通过环境变量 DATA_DIR 覆盖数据目录（向后兼容：缺省仍指向 BASE_DIR/data）。
# 用于隔离自测（如 8011 实例指向独立 data 目录，避免污染生产 8000）。
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))

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
LOST_GRACE_BEFORE_DELETE = 6 * 3600  # 失联/离线保留期：6h（老板拍板 §5.1-2）
# 惰性清扫节流：60s 内最多真正扫描一次（老板拍板 §5.1-5）。
# 支持环境变量覆盖（默认 60）：隔离集成测试设 0 让每次 status 都真正清扫；生产不设置。
SWEEP_INTERVAL = int(os.environ.get("SWEEP_INTERVAL", "60"))
# 删除时点 = last_seen 距今 > LOST_TIMEOUT + LOST_GRACE_BEFORE_DELETE（= 6h20min）
