# -*- coding: utf-8 -*-
"""Agent 注册、列表、白名单管理。对应方案书 §5.1 #1/#2 + F-a/F-i 管理接口。"""
from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import (
    AgentRegister, AgentManageCreate, AgentManageDelete, AgentManageUpdate,
)
from app.services import agent_store, message_store
from app.auth import require_write_auth

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
    if "/" in name:   # F6：禁含 '/'（修复 /{name}/session 路径 404；不调用 register_agent → 不入库）
        raise HTTPException(status_code=422, detail="agent name must not contain '/'")
    # F-a.4：白名单校验（替代原自动建表）；非白名单名 → 403（Q6 统一拦截语义）
    if not agent_store.agent_exists(name):
        raise HTTPException(status_code=403, detail="agent not in whitelist: " + name)
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
def prune_agents(_: None = Depends(require_write_auth)):
    """手动清理僵尸占位 agent（管理兜底；删除判定=占位>1h / 离线>6h20min，并清理已读集合孤儿文件）。"""
    removed = agent_store.prune_zombie_agents()
    return {"status": "ok", "removed": removed}


# ---------------------------------------------------------------------------
# F-a / F-e / F-f / F-h / F-i：白名单管理接口（不鉴权，部署层内网隔离，AC-1.3）
# ---------------------------------------------------------------------------

@router.post("/manage/create")
def manage_create_endpoint(body: AgentManageCreate, _: None = Depends(require_write_auth)):
    """白名单预注册（幂等 upsert）：name 必填；read_scope 非法 → 400；已存在返回既有不覆盖。"""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    read_scope = body.read_scope or "all"
    if read_scope not in ("all", "direct"):
        raise HTTPException(status_code=400, detail="read_scope must be 'all' or 'direct'")
    agent = agent_store.manage_create(name, body.description or "", read_scope)
    return {"status": "ok", "agent": agent}


@router.post("/manage/delete")
def manage_delete_endpoint(body: AgentManageDelete, _: None = Depends(require_write_auth)):
    """白名单删除：按 name 移除 + 级联清 reads.json + agent_read_<name>.json（F-f）。"""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    agent_store.manage_delete(name)
    return {"status": "ok", "removed": True}


@router.get("/manage/list")
def manage_list_endpoint():
    """白名单列表：返回全部 Agent（含 presence / has_unread），供 ☰ 面板渲染。"""
    return {"agents": agent_store.manage_list()}


@router.patch("/manage/update")
def manage_update_endpoint(body: AgentManageUpdate, _: None = Depends(require_write_auth)):
    """白名单更新：改 description / read_scope（name 不可改，F-i / AC-9.1）。

    - name 缺失/空 → 400
    - 请求携带 new_name（改名诉求，设计上不可达）→ 400（name 不可改）
    - name 不存在 → 404
    - read_scope 非法 → 400
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    # F-i / AC-9.1：name 不可改（无 new_name 字段）；请求携带 new_name（改名诉求） → 400
    extra = getattr(body, "model_extra", None) or {}
    if "new_name" in extra:
        raise HTTPException(status_code=400, detail="name is immutable, cannot rename agent")
    if body.read_scope is not None and body.read_scope not in ("all", "direct"):
        raise HTTPException(status_code=400, detail="read_scope must be 'all' or 'direct'")
    agent = agent_store.manage_update(name, body.description, body.read_scope)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found: " + name)
    return {"status": "ok", "agent": agent}
