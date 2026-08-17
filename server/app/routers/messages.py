# -*- coding: utf-8 -*-
"""消息拉取、提交回复、已读状态。对应方案书 §5.1 #3/#4/#5/#6。"""
from fastapi import APIRouter, Query, HTTPException
from app.models.schemas import MessageSend, MessageReply
from app.services import message_store, agent_store

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
def history():
    return {"messages": message_store.get_history()}
