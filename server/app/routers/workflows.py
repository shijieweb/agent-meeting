# -*- coding: utf-8 -*-
"""T-collab-01: Workflow 管理 API · 创建/查询/归档工作流。

端点：
- POST /api/workflows          创建工作流
- GET  /api/workflows          列出工作流（可选过滤）
- GET  /api/workflows/{id}     查看单个工作流详情
- PATCH /api/workflows/{id}    更新工作流（如归档）
- DELETE /api/workflows/{id}   删除工作流
"""
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import message_store
from app.auth import require_write_auth

logger = logging.getLogger("am_workflows")
router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# 内存存储（生产可换SQLite，T-collab-02）
_workflows: dict = {}  # id -> workflow dict


# ---- 数据模型 ----

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    participants: List[str] = Field(default_factory=list)  # agent names
    metadata: dict = Field(default_factory=dict)


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None
    metadata: Optional[dict] = None


class WorkflowDetail(BaseModel):
    id: str
    name: str
    description: str
    participants: List[str]
    metadata: dict
    archived: bool
    message_count: int
    created_at: str
    updated_at: str


# ---- 工具函数 ----

def _gen_id() -> str:
    return f"wf_{int(time.time() * 1000)}"


def _ensure_workflow_exists(wf_id: str) -> dict:
    wf = _workflows.get(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {wf_id}")
    return wf


def _count_messages(wf_id: str) -> int:
    """统计某 workflow_id 的消息数。"""
    try:
        msgs = message_store.load_messages()
        return sum(1 for m in msgs if m.get("workflow_id") == wf_id)
    except Exception:
        return 0


# ---- 端点 ----

@router.post("", response_model=WorkflowDetail, status_code=201)
def create_workflow(body: WorkflowCreate, _: None = Depends(require_write_auth)):
    """创建工作流。"""
    wf_id = _gen_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    wf = {
        "id": wf_id,
        "name": body.name,
        "description": body.description,
        "participants": body.participants,
        "metadata": body.metadata,
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    _workflows[wf_id] = wf
    logger.info("workflow created: %s (%s)", wf_id, body.name)
    return WorkflowDetail(
        **wf,
        message_count=_count_messages(wf_id),
    )


@router.get("", response_model=List[WorkflowDetail])
def list_workflows(
    archived: bool = Query(False, description="是否包含已归档"),
    participant: Optional[str] = Query(None, description="按参与者过滤"),
):
    """列出工作流。"""
    result = []
    for wf in _workflows.values():
        if wf["archived"] and not archived:
            continue
        if participant and participant not in wf.get("participants", []):
            continue
        result.append(WorkflowDetail(
            **wf,
            message_count=_count_messages(wf["id"]),
        ))
    return sorted(result, key=lambda x: x["created_at"], reverse=True)


@router.get("/{wf_id}", response_model=WorkflowDetail)
def get_workflow(wf_id: str):
    """查看单个工作流详情。"""
    wf = _ensure_workflow_exists(wf_id)
    return WorkflowDetail(
        **wf,
        message_count=_count_messages(wf_id),
    )


@router.patch("/{wf_id}", response_model=WorkflowDetail)
def update_workflow(wf_id: str, body: WorkflowUpdate, _: None = Depends(require_write_auth)):
    """更新工作流。"""
    wf = _ensure_workflow_exists(wf_id)
    if body.name is not None:
        wf["name"] = body.name
    if body.description is not None:
        wf["description"] = body.description
    if body.archived is not None:
        wf["archived"] = body.archived
    if body.metadata is not None:
        wf["metadata"].update(body.metadata)
    wf["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return WorkflowDetail(
        **wf,
        message_count=_count_messages(wf_id),
    )


@router.delete("/{wf_id}", status_code=204)
def delete_workflow(wf_id: str, _: None = Depends(require_write_auth)):
    """删除工作流。"""
    wf = _ensure_workflow_exists(wf_id)
    del _workflows[wf_id]
    logger.info("workflow deleted: %s", wf_id)
