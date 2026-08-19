# -*- coding: utf-8 -*-
"""Pydantic 数据模型（请求体校验）。"""
from typing import Optional, Literal, List
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


# ---- 文档协作系统·一期请求/响应模型（T-agent-meeting-upload，design v2.5 §五）----
# 铁律（AC-17/19）：sender_type / owner / owner_type / actor 一律服务端按路由推导，
# 请求体禁止携带。因此下面两个「写」模型均用 extra="allow" 收下多余字段，
# 由路由层 derive_actor() 检出被禁字段后返回 400（而非被 pydantic 静默丢弃）。

class DocCreate(BaseModel):
    """POST /api/docs：新建空文档（仅网页端，Agent 调用 → 403）。"""
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., min_length=1, max_length=300)
    content: Optional[str] = ""


class DocEdit(BaseModel):
    """PUT /api/docs/<id>：编辑正文 / 改名（仅 owner 或 super-admin）。

    extra="forbid"：禁止请求体携带 sender_type/owner/owner_type，
    由 derive_actor 路由推导 + 路由器层二次检查兜底（AC-17/19）。
    """
    model_config = ConfigDict(extra="forbid")
    content: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=300)


class DocChange(BaseModel):
    """document_changes 一条改动记录（actor 服务端推导，azhu #9 / AC-19）。"""
    id: str
    doc_id: str
    actor: str
    action: str          # upload / overwrite / create / edit / rename / delete
    summary: str = ""
    created_at: str


class DocMeta(BaseModel):
    """documents 元数据 + 运行时拼接的外网 url（DB 不存完整 URL，AC-11）。"""
    id: str
    name: str
    owner: str
    owner_type: str      # "user"（人类网页操作员）| "agent"
    mime: str
    size: int
    created_at: str
    updated_at: str
    url: str             # EXTERNAL_BASE_URL + /api/docs/<id>/download（运行时拼）
    editable: bool       # 纯文本类(txt/md/json/csv) 才可在线编辑（R3 / AC-20）


class DocDetail(DocMeta):
    """GET /api/docs/<id>：元数据 + 改动记录列表。"""
    changes: List[DocChange] = []


class DocUploadResponse(DocMeta):
    """POST /api/docs/upload 返回值：元数据 + 本次动作（upload / overwrite）。"""
    action: str


class DocListResponse(BaseModel):
    """GET /api/docs：分页列表（azhu #11 / AC-21，默认 limit=50，updated_at desc）。"""
    docs: List[DocMeta] = []
    total: int = 0
    limit: int = 0
    offset: int = 0


class DocChangesResponse(BaseModel):
    """GET /api/docs/<id>/changes：改动记录列表。"""
    changes: List[DocChange] = []


class DocDeleteResponse(BaseModel):
    """DELETE /api/docs/<id>：删除结果。"""
    status: str = "ok"
    id: str
    name: str
