# -*- coding: utf-8 -*-
"""R2: 操作审计 API。"""
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import audit as audit_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: str
    actor: str
    action: str
    target_type: str
    target_id: Optional[str]
    summary: str
    created_at: str


@router.get("", response_model=list[AuditEntry])
def list_audit(
    actor: Optional[str] = Query(None, description="按操作者筛选"),
    action: Optional[str] = Query(None, description="按操作类型筛选"),
    target_type: Optional[str] = Query(None, description="按目标类型筛选"),
    limit: int = Query(50, ge=1, le=200, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """查询审计日志（支持筛选）。"""
    actions = audit_service.list_actions(
        actor=actor,
        action=action,
        target_type=target_type,
        limit=limit,
        offset=offset,
    )
    return actions


@router.get("/stats")
def audit_stats(
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
):
    """查询审计统计。"""
    total = audit_service.count_actions(actor=actor, action=action, target_type=target_type)
    return {"total": total}
