# -*- coding: utf-8 -*-
"""Pydantic 数据模型（请求体校验）。"""
from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class AgentRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MessageSend(BaseModel):
    # F2：锁定 sender_type 只能是 "user"。省略 -> 默认 "user"(200)；非 user -> Pydantic 422 拒收不入库。
    sender_type: Literal["user"] = "user"
    content: str = Field(..., min_length=1, max_length=100000)
    target_type: str = Field(..., pattern="^(single|all)$")
    target_agent_name: Optional[str] = None
    client_msg_id: Optional[str] = None  # 幂等去重用


class MessageReply(BaseModel):
    agent_name: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=100000)
    reply_to_message_id: Optional[str] = None
    client_msg_id: Optional[str] = None  # 幂等去重用
    # F-c：回复放开 target_type / target_agent_name（不再写死只回老板）。
    # 缺省 None → 兼容旧义（回复人类老板），由 submit_reply 归一化为 "user"。
    target_type: Optional[str] = None   # "single" | "all" | "user" | None(=user)
    target_agent_name: Optional[str] = None


# ---- F-a / F-i 管理接口请求体（白名单 manage CRUD）----
class AgentManageCreate(BaseModel):
    """POST /api/agents/manage/create：白名单预注册（幂等 upsert）。"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    read_scope: Optional[str] = "all"   # "all" | "direct"，缺省 "all"


class AgentManageDelete(BaseModel):
    """POST /api/agents/manage/delete：按 name 移除白名单记录。"""
    name: str = Field(..., min_length=1, max_length=200)


class AgentManageUpdate(BaseModel):
    """PATCH /api/agents/manage/update：改 description / read_scope（name 不可改）。

    extra="allow"：允许请求携带额外的 new_name 字段，路由层据其判定"改名诉求"
    并拒绝（AC-9.1 / design §2.4：name 为不可改标识符）。
    """
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    read_scope: Optional[str] = None    # "all" | "direct" | None(不改)
