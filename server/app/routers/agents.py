# -*- coding: utf-8 -*-
"""Agent 注册、列表。对应方案书 §5.1 #1/#2。"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import AgentRegister
from app.services import agent_store, message_store

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/status")
def agents_status():
    """返回各 Agent 的 {name, last_seen, status, session, presence, has_unread}。

    presence：online/lost/offline 派生字段（服务端权威判定，不落盘）。
    先惰性清扫（60s 节流）再读最新状态——失联自动下线、超保留期自动删除。
    """
    agent_store.scan_and_sweep()            # ① 惰性清扫先于读（节流 60s，成本≈0）
    statuses = agent_store.get_agent_statuses()
    for a in statuses:
        a["has_unread"] = message_store.agent_has_unread(a.get("name", ""))
    return {"agents": statuses}


@router.post("/{name}/session")
def set_session_endpoint(name: str, active: bool = False):
    """开会(active=true)置会话中(working)，结束会议(active=false)置离线(offline)。

    F7：进入先校验该 agent 是否已注册，未注册直接 400（杜绝幽灵 agent，不调用 set_session）。
    F8：无论如何都照常 set_session（active=false -> offline，绝不 4xx 阻断收工）；
        随后计算 has_unread（软提示，仅提示不阻断），加入返回体。
    """
    if not agent_store.agent_exists(name):
        raise HTTPException(status_code=400, detail="agent not registered: " + name)
    agent_store.set_session(name, active)
    has_unread = message_store.agent_has_unread(name)
    return {"status": "ok", "session": active, "has_unread": has_unread}


@router.post("/register")
def register(body: AgentRegister):
    # T-REG-02 / T-PERM-01：区分新注册与已存在，重注册提示唯一性；
    # 名字规范化：strip 后空名 422（Pydantic min_length 只拦纯空串，拦不住 "   "）。
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="agent name must not be blank")
    agents, created, info = agent_store.register_agent(name)
    if created:
        return {"status": "ok", "message": "Agent registered successfully"}
    # 唤醒语义（老板拍板 §5.1-4）：失联/离线同名重注册返回 reactivated=true
    return {
        "status": "ok",
        "message": "Agent already registered",
        "already_exists": True,
        "reactivated": info.get("reactivated", False),
    }


@router.get("")
def list_agents(all: bool = False):
    """默认只返回在线 agent（presence==online，离线/失联不污染已读统计与下拉）；?all=true 返回全部（管理/调试用）。"""
    names = agent_store.list_agent_names() if all else agent_store.list_active_agent_names()
    return {"agents": names}


@router.post("/prune")
def prune_agents():
    """手动清理僵尸占位 agent（管理兜底；删除判定=占位>1h / 离线>6h20min，并清理已读集合孤儿文件）。"""
    removed = agent_store.prune_zombie_agents()
    return {"status": "ok", "removed": removed}
