# -*- coding: utf-8 -*-
"""Agent Hub 配置项（数据目录、服务地址等）。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Agent 建议轮询间隔（秒），仅作提示，前端/agent 自行实现
POLL_INTERVAL = 3

# 回复长度上限（对齐我们 B 系统的 ≤100 字约定，演示用）
REPLY_MAX_LEN = 100
