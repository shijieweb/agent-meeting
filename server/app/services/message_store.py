# -*- coding: utf-8 -*-
"""消息、已读回执、回复逻辑。对应方案书 §5.2 / §5.3。

核心差异点（相对 B 系统）：
- 服务端按 target_type 路由（single/all）；Agent 只拉到@自己的消息。
- 显式 MessageRead 实体 + read_by 数组 -> 前端展示"✓已读 / N/N 已读"。
- submit_reply 响应捎带该 Agent 剩余未读（减轮询）。
- client_msg_id 幂等去重（防网络重试重复保存）。
"""
import uuid

from .storage import (
    read_json,
    write_json,
    now_iso,
    update_json_atomic,
    agent_read_set_file,
    agent_read_set_exists,
    save_agent_read_set,
    mark_agent_read,
)
from .agent_store import load_agents, record_pull


def gen_id(prefix="msg"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


MESSAGES_FILE = "messages.json"
READS_FILE = "reads.json"


def load_messages():
    return read_json(MESSAGES_FILE, [])


def save_messages(msgs):
    write_json(MESSAGES_FILE, msgs)


def load_reads():
    return read_json(READS_FILE, [])


def save_reads(reads):
    write_json(READS_FILE, reads)


def _dup_by_client_msg_id(msgs, client_msg_id):
    if not client_msg_id:
        return None
    for m in msgs:
        if m.get("client_msg_id") == client_msg_id:
            return m
    return None


def send_user_message(content, target_type, target_agent_name=None, client_msg_id=None):
    """前端发送用户消息：写消息 + 为目标 Agent 建未读回执。对应方案书 §5.3 send_user_message。"""
    msgs = load_messages()
    dup = _dup_by_client_msg_id(msgs, client_msg_id)
    if dup:
        return dup

    msg = {
        "id": gen_id("msg"),
        "content": content,
        "sender_type": "user",
        "sender_agent_name": None,
        "target_type": target_type,
        "target_agent_name": target_agent_name if target_type == "single" else None,
        "created_at": now_iso(),
        "client_msg_id": client_msg_id,
        "read_by": [],
    }
    msgs.append(msg)
    save_messages(msgs)

    reads = load_reads()
    if target_type == "single":
        if target_agent_name:
            reads.append({"message_id": msg["id"], "agent_name": target_agent_name, "read_at": None})
    elif target_type == "all":
        for a in load_agents():
            reads.append({"message_id": msg["id"], "agent_name": a["name"], "read_at": None})
    save_reads(reads)
    return msg


def _mark_reads_json(agent_name, message_ids):
    """把指定消息在该 agent 的 read 回执上标记 read_at（供前端 ✓已读 / N/N 展示）。"""
    if not message_ids:
        return
    ids = set(message_ids)

    def _mut(reads):
        for r in reads:
            if r["agent_name"] == agent_name and r["message_id"] in ids and r["read_at"] is None:
                r["read_at"] = now_iso()
        return reads

    update_json_atomic(READS_FILE, [], _mut)


def pull_messages(agent_name):
    """Agent 拉取@自己且未读的消息，并标记为已读。对应方案书 §5.3 pull_messages。

    未读判断由服务端完成（per-agent 已读集合 agent_read_<X>.json）：
    - 读入该 agent 已读 id 集合，过滤掉已读 -> 仅剩未读；
    - 在 update_json_atomic 锁内把本次返回的未读 id 写入已读集合（read-modify-write 原子），
      满足 §7 T-PULL-05（并发拉取同一条消息只会被一个 Agent 领取）；
    - 同时更新 reads.json 回执的 read_at，供前端「✓已读 / N/N」展示。
    客户端只透传结果，不再做 seen.json 去重。
    """
    msgs = load_messages()
    # 迁移种子：若该 agent 的已读集合文件尚不存在（多为既有 agent 首次接入），
    # 从 reads.json 取「该 agent 已读过的消息 id」作为初始集合，避免首次 pull 把历史消息全当未读回灌。
    if not agent_read_set_exists(agent_name):
        seed = {
            r["message_id"]
            for r in load_reads()
            if r.get("agent_name") == agent_name and r.get("read_at") is not None
        }
        save_agent_read_set(agent_name, seed)

    read_holder = {}

    def _mut(read_set):
        # 注意：update_json_atomic 写入的是被原地修改的 read_set（见 storage.update_json_atomic），
        # 因此这里必须在 read_set 上原地修改，不能只返回新对象。
        s = set(read_set)
        unread = []
        for msg in msgs:
            if msg["sender_type"] != "user":
                continue
            targeted = (
                (msg["target_type"] == "single" and msg["target_agent_name"] == agent_name)
                or msg["target_type"] == "all"
            )
            if not targeted:
                continue
            if msg["id"] in s:
                continue
            unread.append(msg)
        for m in unread:
            s.add(m["id"])
        read_holder["unread"] = unread
        read_set.clear()
        read_set.extend(sorted(s))

    update_json_atomic(agent_read_set_file(agent_name), [], _mut)
    unread = read_holder.get("unread", [])
    if unread:
        # 服务端持久化已读：per-agent 集合（已写入）+ reads.json 回执（前端展示）
        _mark_reads_json(agent_name, [m["id"] for m in unread])
    record_pull(agent_name, len(unread) > 0)  # pull 即心跳：刷新 last_seen + 状态(拉到数据=working / 没拉到=waiting)
    return unread


def submit_reply(agent_name, content, reply_to_message_id=None, client_msg_id=None):
    """Agent 提交回复：保存回复，并捎带返回该 Agent 剩余未读消息。对应方案书 §5.3 submit_reply。

    消息追加用 update_json_atomic 保证原子（并发回复不会互相覆盖）。
    """
    def _add(msgs):
        dup = _dup_by_client_msg_id(msgs, client_msg_id)
        if dup:
            return {"dup": True}
        reply = {
            "id": gen_id("msg"),
            "content": content,
            "sender_type": "agent",
            "sender_agent_name": agent_name,
            "target_type": "user",
            "target_agent_name": None,
            "created_at": now_iso(),
            "client_msg_id": client_msg_id,
            "read_by": [],
        }
        msgs.append(reply)
        return {"dup": False}

    res = update_json_atomic(MESSAGES_FILE, [], _add)
    if res.get("dup"):
        return {"status": "ok", "new_messages": [], "duplicate": True}
    new_messages = pull_messages(agent_name)  # 捎带返回新未读
    return {"status": "ok", "new_messages": new_messages}


def get_history():
    """返回全部消息（用户消息附带 read_by 列表）。对应方案书 §5.1 获取聊天历史。"""
    msgs = load_messages()
    reads = load_reads()
    out = []
    for m in msgs:
        d = dict(m)
        if m["sender_type"] == "user":
            d["read_by"] = [
                r["agent_name"]
                for r in reads
                if r["message_id"] == m["id"] and r["read_at"] is not None
            ]
        else:
            d["read_by"] = []
        out.append(d)
    return out
