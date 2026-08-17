# -*- coding: utf-8 -*-
"""Agent 注册、列表。对应方案书 §5.1 #1/#2。"""
from fastapi import APIRouter
from app.models.schemas import AgentRegister
from app.services import agent_store

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/status")
def agents_status():
    """返回各 Agent 的 {name, last_seen, status, session}，前端据此显示在线/工作状态。"""
    return {"agents": agent_store.get_agent_statuses()}


@router.post("/{name}/session")
def set_session_endpoint(name: str, active: bool = False):
    """开会(active=true)置会话中(working)，结束会议(active=false)置离线(offline)。"""
    agent_store.set_session(name, active)
    return {"status": "ok", "session": active}


@router.post("/register")
def register(body: AgentRegister):
    # T-REG-02 / T-PERM-01：区分新注册与已存在，重注册提示唯一性
    agents, created = agent_store.register_agent(body.name)
    if created:
        return {"status": "ok", "message": "Agent registered successfully"}
    return {"status": "ok", "message": "Agent already registered", "already_exists": True}


@router.get("")
def list_agents(all: bool = False):
    """默认只返回活跃 agent（僵尸占位已过滤，不污染已读统计）；?all=true 返回全部（管理/调试用）。"""
    names = agent_store.list_agent_names() if all else agent_store.list_active_agent_names()
    return {"agents": names}


@router.post("/prune")
def prune_agents():
    """手动清理僵尸占位 agent（测试残留等）。"""
    removed = agent_store.prune_zombie_agents()
    return {"status": "ok", "removed": removed}
