# -*- coding: utf-8 -*-
"""T-collab-01 Task5: 任务同步到 shared_board 看板。

当任务状态变为 completed 时，自动同步到 shared_board 的 /api/tasks。
调用方通过 HTTP POST 写入；board 侧有 X-Board-Token 鉴权。

配置（按优先级）：
1. BOARD_API_URL  环境变量 → 看板地址（默认 http://localhost:8788）
2. BOARD_PROJECT  环境变量 → 目标 project_id（默认 19 = 短剧自动化工作流）
3. BOARD_TOKEN    环境变量 → 写接口令牌（优先于从 .env 读取）
   若无 BOARD_TOKEN，自动从 shared_board/.env 提取
"""
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("am_board_sync")

# ---- 配置 ----

BOARD_API_URL = os.environ.get(
    "BOARD_API_URL",
    os.environ.get("AGNES_BOARD_URL", "http://localhost:8788"),
)

def _load_board_token() -> Optional[str]:
    """从环境变量或 shared_board/.env 读取令牌。"""
    token = os.environ.get("BOARD_TOKEN")
    if token:
        return token
    # 尝试从 agent-meeting 项目相对路径找 shared_board/.env
    for candidate in [
        Path(__file__).parent.parent.parent.parent.parent / "shared_board" / ".env",
        Path(__file__).parent.parent.parent.parent.parent / "shared_board" / ".env",
    ]:
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("BOARD_TOKEN="):
                    t = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if t:
                        return t
    return None


BOARD_TOKEN = _load_board_token()
if BOARD_TOKEN:
    logger.info("BOARD_TOKEN loaded")
else:
    logger.warning("BOARD_TOKEN not found; board sync disabled")

# 默认项目ID（短剧自动化工作流 = 19）
BOARD_PROJECT_ID = int(os.environ.get("BOARD_PROJECT", "19") or "19")


# ---- 数据映射 ----

_STATUS_MAP = {
    "pending": "待办",
    "in_progress": "进行中",
    "review": "待验证",
    "completed": "已验证",
    "cancelled": "阻塞",
}

_PRIORITY_MAP = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "urgent": "紧急",
}


def map_task_for_board(task: dict, project_id: Optional[int] = None) -> dict:
    """把 agent-meeting task dict 转为 shared_board POST body。"""
    pid = project_id or BOARD_PROJECT_ID
    if not pid:
        raise ValueError("无可用 project_id：设置 BOARD_PROJECT 或在 workflow metadata 中指定")
    return {
        "project_id": pid,
        "title": task["title"],
        "detail": task.get("description", ""),
        "status": _STATUS_MAP.get(task.get("status", "pending"), "待办"),
        "author": task.get("assignee", "unknown"),
        "priority": _PRIORITY_MAP.get(task.get("priority", "medium"), "中"),
        "deadline": task.get("deadline") or "",
        "progress": task.get("progress", 0),
        "is_hotfix": False,
    }


def sync_task_to_board(task: dict, project_id: Optional[int] = None) -> dict:
    """POST 任务到 shared_board，返回响应 body。"""
    payload = map_task_for_board(task, project_id)
    url = f"{BOARD_API_URL}/api/tasks"
    headers = {
        "Content-Type": "application/json",
        "X-Board-Token": BOARD_TOKEN or "",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            logger.info("synced task %s -> board id=%s", task["id"], body.get("id"))
            return body
    except Exception as e:
        logger.warning("board sync failed for task %s: %s", task["id"], e)
        raise
