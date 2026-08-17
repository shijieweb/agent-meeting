# -*- coding: utf-8 -*-
"""消息拉取、提交回复、已读状态。对应方案书 §5.1 #3/#4/#5/#6。"""
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from app.models.schemas import MessageSend, MessageReply, BaseModel
from app.services import message_store, agent_store
from app.config import REPLY_MAX_LEN

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("/pull")
def pull(agent_name: str = Query(..., description="Agent 名字")):
    # 未读下沉：pull_messages 仅返回该 agent 未读 user 消息，并在服务端持久化已读
    # （per-agent 已读集合 data/agent_read_<X>.json + reads.json 回执），客户端只透传。
    return {"messages": message_store.pull_messages(agent_name)}


@router.post("/reply")
def reply(body: MessageReply):
    # T-REPLY-04：未注册 Agent 回复 → 返回错误（不保存）
    if not agent_store.agent_exists(body.agent_name):
        raise HTTPException(status_code=400, detail="agent not registered: " + body.agent_name)
    # F11：业务上限校验（方案①）。仅校验 >REPLY_MAX_LEN(=4000) 返回 400；
    # <=4000（含老板常见 ~500 字长回复）全接受入库。绝不使用 100 字阈值。
    if len(body.content) > REPLY_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail="reply too long: {0} > REPLY_MAX_LEN ({1})".format(len(body.content), REPLY_MAX_LEN),
        )
    return message_store.submit_reply(
        body.agent_name, body.content, body.reply_to_message_id, body.client_msg_id
    )


@router.post("/send")
def send(body: MessageSend):
    # T-SEND-03 / 邻接：single 必须指定且目标 Agent 已存在，否则返回错误、不保存
    if body.target_type == "single":
        if not body.target_agent_name:
            raise HTTPException(status_code=400, detail="single target requires target_agent_name")
        if not agent_store.agent_exists(body.target_agent_name):
            raise HTTPException(status_code=400, detail="target agent not found: " + body.target_agent_name)
    msg = message_store.send_user_message(
        body.content, body.target_type, body.target_agent_name, body.client_msg_id
    )
    return {"status": "ok", "message_id": msg["id"]}


@router.get("/history")
def history(
    since_id: Optional[str] = Query(None, description="游标消息 id，返回严格晚于它的消息（增量轮询）"),
    before_id: Optional[str] = Query(None, description="游标消息 id，返回严格早于它的前 limit 条（向上翻）"),
    limit: int = Query(30, description="每页条数上限（首屏/翻页）"),
):
    messages = message_store.get_history(since_id=since_id, before_id=before_id, limit=limit)
    return {"messages": messages}


class MessageCleanup(BaseModel):
    """F12 归档请求体：keep_last 按条数、older_than 按时间（ISO），二选一或同时给（交集保留）。"""
    keep_last: Optional[int] = None
    older_than: Optional[str] = None


@router.post("/cleanup")
def cleanup(body: MessageCleanup):
    """F12 归档：按条数/时间清理 messages.json（删除式归档，archived=移除条数）。

    - 两个参数皆缺 -> 400 "provide keep_last or older_than"
    - keep_last < 0 -> 400
    返回 {status:"ok", archived:N, remaining:M}，且会同步清理 reads.json 孤儿回执。
    """
    if body.keep_last is None and body.older_than is None:
        raise HTTPException(status_code=400, detail="provide keep_last or older_than")
    if body.keep_last is not None and body.keep_last < 0:
        raise HTTPException(status_code=400, detail="keep_last must be >= 0")
    result = message_store.cleanup_messages(body.keep_last, body.older_than)
    return {"status": "ok", "archived": result["archived"], "remaining": result["remaining"]}
