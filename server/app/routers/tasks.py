# -*- coding: utf-8 -*-
"""T-collab-01: 任务委派 API · 多Agent协作核心。

端点：
- POST /api/tasks              创建/委派任务
- GET  /api/tasks              列出任务（支持过滤）
- GET  /api/tasks/{id}         查看详情
- PATCH /api/tasks/{id}        更新状态（进行中/待验证/已完成）
- POST /api/tasks/{id}/comment 添加评论
"""
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth import require_write_auth
from app.services import agent_store, message_store
from app.services.collab_db import CollabDB
from app.services.board_sync import sync_task_to_board
from app.config import DATA_DIR

logger = logging.getLogger("am_tasks")
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 持久化层
_db = CollabDB(DATA_DIR)


# ---- 数据模型 ----

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    assignee: str = Field(..., description="目标agent名")
    workflow_id: Optional[str] = None
    project_id: Optional[int] = None
    parent_task_id: Optional[str] = None
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    deadline: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(pending|in_progress|review|completed|cancelled)$")
    assignee: Optional[str] = None
    progress: Optional[int] = Field(None, ge=0, le=100)
    comment: Optional[str] = None  # 更新时附带评论


class TaskComment(BaseModel):
    content: str
    author: str  # agent name


# ---- 工具函数 ----

def _ensure_task_exists(task_id: str) -> dict:
    task = _db.task_get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


def _notify_agent(agent_name: str, message: str):
    """发送系统消息通知agent。"""
    try:
        from app.services.message_store import send_message
        send_message(
            content=f"📋 新任务分配: {message}",
            sender_type="system",
            target_type="single",
            target_agent_name=agent_name,
        )
        logger.info("notified agent %s: %s", agent_name, message[:50])
    except Exception as e:
        logger.warning("failed to notify agent %s: %s", agent_name, e)


# ---- 端点 ----

@router.post("", response_model=dict, status_code=201)
def create_task(body: TaskCreate, request: Request, _: None = Depends(require_write_auth)):
    """创建/委派任务。"""
    task_id = f"task_{int(time.time() * 1000)}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    task = _db.task_create(
        task_id=task_id,
        title=body.title,
        description=body.description,
        assignee=body.assignee,
        reporter="boss",
        project_id=body.project_id,
        parent_task_id=body.parent_task_id,
        priority=body.priority,
        deadline=body.deadline,
        metadata=body.metadata,
    )
    task["created_at"] = now
    task["updated_at"] = now

    # 通知目标agent
    _notify_agent(body.assignee, f"新任务: {body.title}")

    logger.info("task created: %s -> %s", task_id, body.assignee)
    return task


@router.get("", response_model=List[dict])
def list_tasks(
    status: Optional[str] = Query(None, description="按状态过滤"),
    assignee: Optional[str] = Query(None, description="按负责人过滤"),
    project_id: Optional[int] = Query(None, description="按项目过滤"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出任务。"""
    tasks = _db.task_list(
        status=status,
        assignee=assignee,
        project_id=project_id,
        limit=limit,
    )
    return tasks


@router.get("/{task_id}", response_model=dict)
def get_task(task_id: str):
    """查看任务详情。"""
    return _ensure_task_exists(task_id)


@router.patch("/{task_id}", response_model=dict)
def update_task(task_id: str, body: TaskUpdate, _: None = Depends(require_write_auth)):
    """更新任务状态。"""
    task = _ensure_task_exists(task_id)

    updates = {}
    if body.status:
        updates["status"] = body.status
        # 状态变更通知
        if body.status == "completed":
            _notify_agent(task["assignee"], f"任务已完成: {task['title']}")
            # 同步到 shared_board 看板（异步失败不影响主流程）
            try:
                wf_id = task.get("workflow_id") or task.get("project_id")
                sync_task_to_board(task, project_id=int(wf_id) if wf_id else None)
            except Exception as e:
                logger.warning("board sync skipped: %s", e)
        elif body.status == "in_progress":
            _notify_agent(task["assignee"], f"开始处理: {task['title']}")

    if body.assignee:
        updates["assignee"] = body.assignee

    if body.progress is not None:
        updates["progress"] = body.progress

    if body.comment:
        updates["comment"] = body.comment

    updated = _db.task_update(task_id, **updates)
    return updated


@router.post("/{task_id}/comment", response_model=dict)
def add_comment(task_id: str, body: TaskComment, _: None = Depends(require_write_auth)):
    """添加评论。"""
    task = _ensure_task_exists(task_id)
    updated = _db.task_update(task_id, comment=body.content)
    return updated
