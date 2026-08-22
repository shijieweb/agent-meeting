# -*- coding: utf-8 -*-
"""R2: 操作审计服务。

记录用户/agent 的关键操作（消息删除、清理、文档改动等），
供前端可视化展示时间线。
"""
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from app.config import DATA_DIR
from app.services.db import get_conn

logger = logging.getLogger("am_audit")

# 操作类型枚举
ACTION_DELETE_MESSAGE = "delete_message"
ACTION_CLEANUP_MESSAGES = "cleanup_messages"
ACTION_UPLOAD_DOC = "upload_doc"
ACTION_EDIT_DOC = "edit_doc"
ACTION_DELETE_DOC = "delete_doc"
ACTION_CREATE_DOC = "create_doc"


def record_action(
    actor: str,
    action: str,
    target_type: str = "system",
    target_id: Optional[str] = None,
    summary: str = "",
) -> None:
    """记录一条审计操作。"""
    conn = get_conn()
    import time
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    import uuid
    audit_id = f"audit_{uuid.uuid4().hex[:8]}"
    try:
        conn.execute(
            """INSERT INTO audit_log (id, actor, action, target_type, target_id, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, actor, action, target_type, target_id, summary, now),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to record audit action: %s", e)


def list_actions(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    """查询审计日志（支持筛选）。"""
    conn = get_conn()
    sql = "SELECT id, actor, action, target_type, target_id, summary, created_at FROM audit_log WHERE 1=1"
    params = []
    if actor:
        sql += " AND actor = ?"
        params.append(actor)
    if action:
        sql += " AND action = ?"
        params.append(action)
    if target_type:
        sql += " AND target_type = ?"
        params.append(target_type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_actions(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
) -> int:
    """统计审计日志数量。"""
    conn = get_conn()
    sql = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
    params = []
    if actor:
        sql += " AND actor = ?"
        params.append(actor)
    if action:
        sql += " AND action = ?"
        params.append(action)
    if target_type:
        sql += " AND target_type = ?"
        params.append(target_type)
    return conn.execute(sql, params).fetchone()[0]
