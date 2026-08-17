# -*- coding: utf-8 -*-
"""Pydantic 数据模型（请求体校验）。"""
from pydantic import BaseModel, Field
from typing import Optional


class AgentRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MessageSend(BaseModel):
    sender_type: str = "user"
    content: str = Field(..., min_length=1, max_length=100000)
    target_type: str = Field(..., pattern="^(single|all)$")
    target_agent_name: Optional[str] = None
    client_msg_id: Optional[str] = None  # 幂等去重用


class MessageReply(BaseModel):
    agent_name: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=100000)
    reply_to_message_id: Optional[str] = None
    client_msg_id: Optional[str] = None  # 幂等去重用
