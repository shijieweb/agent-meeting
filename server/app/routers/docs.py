# -*- coding: utf-8 -*-
"""文档协作系统·一期 API 路由（design v2.5 §四/§五）。

8 个端点：
  POST   /api/docs/upload    multipart 上传（创建或覆盖）
  POST   /api/docs           JSON 新建空文档
  GET    /api/docs           列表（支持 limit/offset）
  GET    /api/docs/<id>      详情（含 url + changes）
  GET    /api/docs/<id>/download  流式下载
  PUT    /api/docs/<id>      编辑正文 / 改名
  DELETE /api/docs/<id>       删除
  GET    /api/docs/<id>/changes  改动记录

身份可信推导（AC-17/19/22）：
  sender_type / owner / owner_type / document_changes.actor
  一律服务端按路由推导，绝不接受请求体传入。
"""
import os
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.config import DOC_LIST_DEFAULT_LIMIT, DOC_LIST_MAX_LIMIT, EXTERNAL_BASE_URL
from app.models.schemas import (
    DocChange,
    DocChangesResponse,
    DocCreate,
    DocDeleteResponse,
    DocDetail,
    DocEdit,
    DocListResponse,
    DocMeta,
    DocUploadResponse,
)
from app.services.document_store import (
    can_write,
    create_empty,
    delete_doc,
    derive_actor,
    download_doc,
    edit_doc,
    get_detail,
    get_download,
    is_text_mime,
    list_changes,
    list_docs,
    upload,
)

router = APIRouter(prefix="/api/docs", tags=["docs"])


def _build_url(doc_id: str) -> str:
    """运行时拼外网下载 URL（DB 不存完整 URL，AC-11）。"""
    return f"{EXTERNAL_BASE_URL}/api/docs/{doc_id}/download"


def _meta(doc: dict) -> DocMeta:
    """把 documents 行转 DocMeta（含 editable 判定）。"""
    mime = doc.get("mime", "")
    return DocMeta(
        id=doc["id"],
        name=doc["name"],
        owner=doc["owner"],
        owner_type=doc["owner_type"],
        mime=mime,
        size=doc.get("size", 0),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        url=_build_url(doc["id"]),
        editable=is_text_mime(mime),
    )


# ---------------------------------------------------------------------------
# POST /api/docs/upload  —  multipart 上传（创建或覆盖）
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=DocUploadResponse)
async def doc_upload(
    file: UploadFile = File(...),
    agent_name: Optional[str] = Form(None, description="Agent 名字（路由推导 owner_type=agent）"),
    doc_id: Optional[str] = Form(None, description="文档 id（带此参数=覆盖已有文档，不带=新建）"),
):
    actor = derive_actor({}, agent_name)

    # AC-17/19/22：body（表单字段）禁止传 sender_type/owner/owner_type
    # derive_actor 已做检查

    # Agent 不能新建（不带 doc_id → 403）
    if actor["sender_type"] == "agent" and doc_id is None:
        raise HTTPException(
            status_code=403,
            detail="agent cannot create docs; provide doc_id to overwrite",
        )

    # 读取文件内容
    contents = await file.read()
    filename = file.filename or "unnamed"

    # mime 校验（由 document_store.guess_mime 按扩展名判定）
    # 上传到 document_store.upload 内部校验

    result = upload(contents, filename, actor, doc_id=doc_id)
    meta = _meta(result)
    return DocUploadResponse(
        **meta.model_dump(),
        action=result["action"],
    )


# ---------------------------------------------------------------------------
# POST /api/docs  —  JSON 新建空文档（仅网页端）
# ---------------------------------------------------------------------------

@router.post("", response_model=DocMeta)
def doc_create(body: DocCreate):
    actor = derive_actor(body.__dict__, agent_name=None)

    # AC-17/19：sender_type/owner/owner_type 拒请求体传入
    if "sender_type" in body.__dict__ or "owner" in body.__dict__ or "owner_type" in body.__dict__:
        raise HTTPException(
            status_code=400,
            detail="sender_type/owner/owner_type are server-derived; do not send",
        )

    result = create_empty(body.name, actor, content=body.content or "")
    return _meta(result)


# ---------------------------------------------------------------------------
# GET /api/docs  —  列表（支持 limit/offset，AC-21）
# ---------------------------------------------------------------------------

@router.get("", response_model=DocListResponse)
def doc_list(
    owner: Optional[str] = Query(None, description="按 owner 过滤"),
    limit: int = Query(DOC_LIST_DEFAULT_LIMIT, ge=1, le=DOC_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    result = list_docs(owner=owner, limit=limit, offset=offset)
    return DocListResponse(
        docs=[_meta(d) for d in result["docs"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )


# ---------------------------------------------------------------------------
# GET /api/docs/<id>  —  详情（含 url + changes）
# ---------------------------------------------------------------------------

@router.get("/{doc_id}", response_model=DocDetail)
def doc_detail(doc_id: str):
    detail = get_detail(doc_id)
    # get_detail 返回 public_doc + changes，_meta 需要去掉 file_uuid
    meta = _meta(detail)
    return DocDetail(
        **meta.model_dump(),
        changes=[
            DocChange(
                id=c["id"],
                doc_id=c["doc_id"],
                actor=c["actor"],
                action=c["action"],
                summary=c.get("summary", ""),
                created_at=c["created_at"],
            )
            for c in detail.get("changes", [])
        ],
    )


# ---------------------------------------------------------------------------
# GET /api/docs/<id>/download  —  流式下载（AC-18 路径重建）
# ---------------------------------------------------------------------------

@router.get("/{doc_id}/download")
def doc_download(doc_id: str):
    info = get_download(doc_id)
    file_path = info["path"]
    name = info["name"]
    mime = info["mime"]

    return FileResponse(
        file_path,
        media_type=mime,
        filename=name,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
        },
    )


# ---------------------------------------------------------------------------
# PUT /api/docs/<id>  —  编辑正文 / 改名（AC-20）
# ---------------------------------------------------------------------------

@router.put("/{doc_id}", response_model=DocMeta)
def doc_edit(doc_id: str, body: DocEdit):
    actor = derive_actor(body.__dict__, agent_name=None)

    # AC-17/19：拒伪造
    if "sender_type" in body.__dict__ or "owner" in body.__dict__ or "owner_type" in body.__dict__:
        raise HTTPException(
            status_code=400,
            detail="sender_type/owner/owner_type are server-derived; do not send",
        )

    if body.content is None and body.name is None:
        raise HTTPException(status_code=400, detail="provide content or name")

    result = edit_doc(doc_id, actor, content=body.content, name=body.name)
    return _meta(result)


# ---------------------------------------------------------------------------
# DELETE /api/docs/<id>  —  删除（AC-10 / AC-22）
# ---------------------------------------------------------------------------

@router.delete("/{doc_id}", response_model=DocDeleteResponse)
def doc_delete(
    doc_id: str,
    agent_name: Optional[str] = Query(None, description="Agent 名字（路由推导 owner_type=agent）"),
):
    actor = derive_actor({}, agent_name)
    result = delete_doc(doc_id, actor)
    return DocDeleteResponse(
        status="ok",
        id=result["id"],
        name=result["name"],
    )


# ---------------------------------------------------------------------------
# GET /api/docs/<id>/changes  —  改动记录列表
# ---------------------------------------------------------------------------

@router.get("/{doc_id}/changes", response_model=DocChangesResponse)
def doc_changes(doc_id: str):
    changes = list_changes(doc_id)
    return DocChangesResponse(
        changes=[
            DocChange(
                id=c["id"],
                doc_id=c["doc_id"],
                actor=c["actor"],
                action=c["action"],
                summary=c.get("summary", ""),
                created_at=c["created_at"],
            )
            for c in changes
        ],
    )
