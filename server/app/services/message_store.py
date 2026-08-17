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
    load_agent_read_set,
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


def agent_has_unread(name):
    """F8 软保护用：只读判定该 agent 是否有「target 命中它且尚未 pull」的 user 消息。

    判定逻辑与 pull_messages 的未读判定一致（含首次接入从 reads.json 取种子），
    但**只读不写**——不改变任何已读状态（不创建已读集合、不标记 read_at）。

    返回：True 表示存在未读 user 消息，False 表示无。
    """
    msgs = load_messages()
    # 已读集合：文件已存在则直接载入；否则从 reads.json 取该 agent 已读 id 作种子（与 pull_messages 迁移一致）。
    if not agent_read_set_exists(name):
        seed = {
            r["message_id"]
            for r in load_reads()
            if r.get("agent_name") == name and r.get("read_at") is not None
        }
        read_set = seed
    else:
        read_set = load_agent_read_set(name)
    for msg in msgs:
        if msg.get("sender_type") != "user":
            continue
        targeted = (
            (msg.get("target_type") == "single" and msg.get("target_agent_name") == name)
            or msg.get("target_type") == "all"
        )
        if not targeted:
            continue
        if msg["id"] not in read_set:
            return True
    return False


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


def get_history(since_id=None, before_id=None, limit=30):
    """返回游标分页后的消息（用户消息附带 read_by 列表）。对应方案书 §5.1 + T-meeting-incremental。

    排序全序键 = (created_at, index)，index 为消息在 messages.json 数组中的下标
    （append 顺序 = 写入顺序），用于同秒消息的稳定 tie-break，保证切片连续无漏。

    参数：
      since_id: 游标消息 id；返回严格晚于该消息（按全序键）的全部消息，用于增量轮询拉新。
      before_id: 游标消息 id；返回严格早于该消息中紧邻其前的 limit 条（升序），用于向上翻加载更早。
      limit: 每页条数上限（首屏/翻页用）；<=0 视为 30。

    返回：按 (created_at, index) 升序的 dict 列表，字段与旧 get_history 完全一致
          （user 消息附 read_by，agent 消息 read_by=[]）。无参调用向后兼容返回最新 limit 条。
    """
    if limit is None or limit <= 0:
        limit = 30

    msgs = load_messages()
    reads = load_reads()

    # 候选集：保留原数组下标，构造 (index, message) 列表
    indexed = list(enumerate(msgs))

    # 全序键：(created_at 字符串字典序, 原数组下标)。created_at 同格式可直接比较。
    def sort_key(item):
        i, m = item
        return (m["created_at"], i)

    # 按全序键升序排序，保证输出升序、切片连续无漏
    indexed.sort(key=sort_key)

    # 按 id 定位游标的全序键值；找不到时退化为「无该游标」语义
    def locate(target_id):
        for i, m in indexed:
            if m.get("id") == target_id:
                return (m["created_at"], i)
        return None

    if since_id is not None:
        cur = locate(since_id)
        if cur is None:
            selected = indexed  # 游标无效 -> 退化为全部（升序）
        else:
            selected = [item for item in indexed if sort_key(item) > cur]  # 严格晚于，升序
    elif before_id is not None:
        cur = locate(before_id)
        if cur is None:
            selected = indexed  # 游标无效 -> 退化为全部（升序）
        else:
            earlier = [item for item in indexed if sort_key(item) < cur]  # 严格早于
            selected = earlier[-limit:]  # 紧邻其前的 limit 条，升序
    else:
        selected = indexed[-limit:]  # 最新 limit 条，升序（首屏）

    out = []
    for i, m in selected:
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


def cleanup_messages(keep_last=None, older_than=None):
    """F12 归档：按条数/时间清理 messages.json（删除式归档，archived=移除条数）。

    在锁内（update_json_atomic）按全序键 (created_at, 原数组下标) 升序排序后计算保留集：
    - 若给了 keep_last：仅保留排序后最后 keep_last 条（pos >= n - keep_last）；
    - 若给了 older_than：仅保留 created_at >= older_than 的消息（同格式字符串字典序 == 时间序）；
    - 二者同时给：取交集（两项都满足才保留）。
    同时清理 reads.json 中指向被移除消息的孤儿回执（message_id 不在保留集内则删），
    保证 history 已读回执一致。

    返回 {"archived":int, "remaining":int}，archived = 从 messages.json 移除的条数。

    注意：update_json_atomic 写回的是被原地修改的 data，因此必须原地改 msgs（clear+extend），
    不能只返回新列表。
    """
    holder = {}

    def _mut_messages(msgs):
        indexed = list(enumerate(msgs))
        # 全序键升序：(created_at 字符串字典序, 原数组下标)
        indexed.sort(key=lambda item: (item[1]["created_at"], item[0]))
        n = len(indexed)
        keep_ids = set()
        for pos, (i, m) in enumerate(indexed):
            keep = True
            if keep_last is not None:
                # 最近 keep_last 条：位置在 [n-keep_last, n) 才保留
                if pos < n - keep_last:
                    keep = False
            if older_than is not None:
                if m["created_at"] < older_than:
                    keep = False
            if keep:
                keep_ids.add(m["id"])
        kept = [m for i, m in indexed if m["id"] in keep_ids]
        archived = n - len(kept)
        msgs.clear()
        msgs.extend(kept)
        holder["archived"] = archived
        holder["remaining"] = len(kept)
        holder["keep_ids"] = keep_ids
        return None

    update_json_atomic(MESSAGES_FILE, [], _mut_messages)
    keep_ids = holder.get("keep_ids", set())
    archived = holder.get("archived", 0)
    remaining = holder.get("remaining", 0)

    def _mut_reads(reads):
        # D-1 修复：update_json_atomic 写回的是被原地修改的 data（而非 mutator 返回值），
        # 因此必须原地改 reads（reads[:] = ...），不能只返回新列表，否则孤儿回执永不清理。
        reads[:] = [r for r in reads if r.get("message_id") in keep_ids]
        return None

    update_json_atomic(READS_FILE, [], _mut_reads)
    return {"archived": archived, "remaining": remaining}
