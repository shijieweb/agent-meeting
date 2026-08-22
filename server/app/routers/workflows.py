# -*- coding: utf-8 -*-
"""T-collab-01: Workflow 管理 API · 创建/查询/归档工作流。

端点：
- POST /api/workflows          创建工作流
- GET  /api/workflows          列出工作流（可选过滤）
- GET  /api/workflows/{id}     查看单个工作流详情
- PATCH /api/workflows/{id}    更新工作流（如归档）
- DELETE /api/workflows/{id}   删除工作流
"""
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import message_store
from app.auth import require_write_auth
from app.services.collab_db import CollabDB
from app.config import DATA_DIR

logger = logging.getLogger("am_workflows")
router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# 持久化层
_db = CollabDB(DATA_DIR)


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

def _ensure_workflow_exists(wf_id: str) -> dict:
    wf = _db.workflow_get(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {wf_id}")
    return wf


def _to_detail(wf: dict) -> dict:
    """把 DB 结果包装成与 WorkflowDetail 对齐的 dict。"""
    wf["message_count"] = _count_messages(wf["id"])
    wf["archived"] = bool(wf.get("archived"))
    return wf


def _count_messages(wf_id: str) -> int:
    """统计某 workflow_id 的消息数。"""
    try:
        msgs = message_store.load_messages()
        return sum(1 for m in msgs if m.get("workflow_id") == wf_id)
    except Exception:
        return 0


# ---- 端点 ----

@router.post("", response_model=dict, status_code=201)
def create_workflow(body: WorkflowCreate, _: None = Depends(require_write_auth)):
    """创建工作流。"""
    wf_id = f"wf_{int(time.time() * 1000)}"
    wf = _db.workflow_create(
        wf_id=wf_id,
        name=body.name,
        description=body.description,
        participants=body.participants,
        metadata=body.metadata,
    )
    logger.info("workflow created: %s (%s)", wf_id, body.name)
    return _to_detail(wf)


@router.get("", response_model=List[dict])
def list_workflows(
    archived: bool = Query(False, description="是否包含已归档"),
    participant: Optional[str] = Query(None, description="按参与者过滤"),
):
    """列出工作流。"""
    wfs = _db.workflow_list(archived=archived, participant=participant)
    result = []
    for wf in wfs:
        wf["archived"] = bool(wf.get("archived"))
        wf["message_count"] = _count_messages(wf["id"])
        result.append(wf)
    return sorted(result, key=lambda x: x["created_at"], reverse=True)


@router.get("/{wf_id}", response_model=dict)
def get_workflow(wf_id: str):
    """查看单个工作流详情。"""
    wf = _ensure_workflow_exists(wf_id)
    return _to_detail(wf)


@router.patch("/{wf_id}", response_model=dict)
def update_workflow(wf_id: str, body: WorkflowUpdate, _: None = Depends(require_write_auth)):
    """更新工作流。"""
    wf = _ensure_workflow_exists(wf_id)
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.archived is not None:
        updates["archived"] = int(body.archived)
    if body.metadata is not None:
        existing_meta = wf.get("metadata", {})
        existing_meta.update(body.metadata)
        updates["metadata"] = existing_meta
    updated = _db.workflow_update(wf_id, **updates)
    return _to_detail(updated)


@router.delete("/{wf_id}", status_code=204)
def delete_workflow(wf_id: str, _: None = Depends(require_write_auth)):
    """删除工作流。"""
    wf = _ensure_workflow_exists(wf_id)
    deleted = _db.workflow_delete(wf_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {wf_id}")
    logger.info("workflow deleted: %s", wf_id)
