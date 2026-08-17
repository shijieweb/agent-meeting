# -*- coding: utf-8 -*-
"""Pydantic 数据模型（请求体校验）。"""
from pydantic import BaseModel, Field
from typing import Optional, Literal


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
