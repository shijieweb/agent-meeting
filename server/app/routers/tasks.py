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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import require_write_auth
from app.services import agent_store, message_store

logger = logging.getLogger("am_tasks")
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 内存存储（生产换SQLite）
_tasks: dict = {}  # id -> task dict


# ---- 数据模型 ----

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    assignee: str = Field(..., description="目标agent名")
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


class TaskDetail(BaseModel):
    id: str
    title: str
    description: str
    status: str
    assignee: str
    reporter: str
    project_id: Optional[int]
    parent_task_id: Optional[str]
    priority: str
    deadline: Optional[str]
    progress: int
    comments: List[dict]
    metadata: dict
    created_at: str
    updated_at: str


# ---- 工具函数 ----

def _gen_id() -> str:
    return f"task_{int(time.time() * 1000)}"


def _ensure_task_exists(task_id: str) -> dict:
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


def _notify_agent(agent_name: str, message: str):
    """发送系统消息通知agent。"""
    try:
        # 找一条@all消息作为reply_to，或者创建新消息
        msgs = message_store.load_messages()
        reply_to = None
        for m in reversed(msgs):
            if m.get("target_type") == "all":
                reply_to = m.get("id")
                break
        
        from app.services.message_store import send_message
        send_message(
            content=f"📋 新任务分配: {message}",
            sender_type="system",
            target_type="single",
            target_agent_name=agent_name,
            reply_to_message_id=reply_to,
        )
        logger.info("notified agent %s: %s", agent_name, message[:50])
    except Exception as e:
        logger.warning("failed to notify agent %s: %s", agent_name, e)


# ---- 端点 ----

@router.post("", response_model=TaskDetail, status_code=201)
def create_task(body: TaskCreate, request: Request, _: None = Depends(require_write_auth)):
    """创建/委派任务。"""
    from fastapi import Request
    
    # 获取reporter（从Authorization header或默认"boss"）
    reporter = "boss"
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        reporter = "authenticated_user"  # TODO: 解析JWT获取用户
    
    task_id = _gen_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    task = {
        "id": task_id,
        "title": body.title,
        "description": body.description,
        "status": "pending",
        "assignee": body.assignee,
        "reporter": reporter,
        "project_id": body.project_id,
        "parent_task_id": body.parent_task_id,
        "priority": body.priority,
        "deadline": body.deadline,
        "progress": 0,
        "comments": [],
        "metadata": body.metadata,
        "created_at": now,
        "updated_at": now,
    }
    _tasks[task_id] = task
    
    # 通知目标agent
    _notify_agent(body.assignee, f"新任务: {body.title}")
    
    logger.info("task created: %s -> %s", task_id, body.assignee)
    return TaskDetail(**task)


@router.get("", response_model=List[TaskDetail])
def list_tasks(
    status: Optional[str] = Query(None, description="按状态过滤"),
    assignee: Optional[str] = Query(None, description="按负责人过滤"),
    project_id: Optional[int] = Query(None, description="按项目过滤"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出任务。"""
    result = []
    for t in _tasks.values():
        if status and t["status"] != status:
            continue
        if assignee and t["assignee"] != assignee:
            continue
        if project_id and t.get("project_id") != project_id:
            continue
        result.append(TaskDetail(**t))
    return sorted(result, key=lambda x: x["created_at"], reverse=True)[:limit]


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: str):
    """查看任务详情。"""
    task = _ensure_task_exists(task_id)
    return TaskDetail(**task)


@router.patch("/{task_id}", response_model=TaskDetail)
def update_task(task_id: str, body: TaskUpdate, _: None = Depends(require_write_auth)):
    """更新任务状态。"""
    task = _ensure_task_exists(task_id)
    
    if body.status:
        task["status"] = body.status
        # 状态变更通知
        if body.status == "completed":
            _notify_agent(task["assignee"], f"任务已完成: {task['title']}")
        elif body.status == "in_progress":
            _notify_agent(task["assignee"], f"开始处理: {task['title']}")
    
    if body.assignee:
        task["assignee"] = body.assignee
    
    if body.progress is not None:
        task["progress"] = body.progress
    
    if body.comment:
        task["comments"].append({
            "content": body.comment,
            "author": "user",  # TODO: 从token解析
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    
    task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return TaskDetail(**task)


@router.post("/{task_id}/comment", response_model=TaskDetail)
def add_comment(task_id: str, body: TaskComment, _: None = Depends(require_write_auth)):
    """添加评论。"""
    task = _ensure_task_exists(task_id)
    task["comments"].append({
        "content": body.content,
        "author": body.author,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return TaskDetail(**task)
