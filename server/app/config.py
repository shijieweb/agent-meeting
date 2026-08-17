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
