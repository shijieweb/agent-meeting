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


# 系统事件消息（presence_event）文案模板：event -> content。
# 由服务端生成，保证刷新后文案一致（对齐 AC-1.3 精神）。
_SYSTEM_EVENT_CONTENT = {
    "init": "{name} 上线了",
    "end": "{name} 下线了",
    "lost": "{name} 已离线（失联超时）",
    "reactivated": "{name} 重新上线了",
}


def append_system_event(event: str, agent_name: str) -> dict:
    """把一条系统事件消息（presence_event）追加进 messages.json 消息流（AC-3.4/4.1/4.2/4.3）。

    事件范围：显式 init（上线）/ end（下线）+ 失联自动下线（lost）+ 重注册唤醒（reactivated）。

    关键约束：
    - sender_type="system"：pull_messages / agent_has_unread 的未读过滤只取 sender_type=="user"，
      因此系统消息天然不进未读统计、不走 pull 通道、不入 reads.json 回执（AC-4.1）。
    - 用 update_json_atomic(MESSAGES_FILE, [], _add) 原地修改（D-1 铁律：不可只返回新对象）。
    - 不写 reads.json（事件消息无回执）。

    返回写入的系统消息 dict。
    """
    if event not in _SYSTEM_EVENT_CONTENT:
        raise ValueError("unknown system event: {0}".format(event))
    content = _SYSTEM_EVENT_CONTENT[event].format(name=agent_name)
    holder = {}

    def _add(msgs):
        msg = {
            "id": gen_id("msg"),
            "content": content,
            "sender_type": "system",
            "sender_agent_name": agent_name,
            "target_type": None,
            "target_agent_name": None,
            "created_at": now_iso(),
            "client_msg_id": None,
            "read_by": [],
            "message_type": "presence_event",
            "event": event,
        }
        msgs.append(msg)          # 原地修改（update_json_atomic 写回被原地修改的 data）
        holder["msg"] = msg
        return None

    update_json_atomic(MESSAGES_FILE, [], _add)
    return holder.get("msg")


def add_system_message(content, message_type="doc_event") -> dict:
    """把一条**任意文案**的 system 消息追加进消息流（文档协作系统·一期群联动）。

    与 append_system_event 的区别：后者只服务 presence 事件（文案由 _SYSTEM_EVENT_CONTENT
    模板生成、带 event 字段）；本函数 content 由调用方给定，用于文档上传/覆盖/新建/删除
    的群通知（content 内含 `[文件名](外网URL)` Markdown 链接，前端 renderSystemContent 渲染成
    可点击 <a>，即 AC-4）。design v2.5 §五已核验：本函数原仓库不存在，属新增。

    关键约束（与 append_system_event 完全一致，不改任何既有消息逻辑）：
    - sender_type="system"：pull_messages / agent_has_unread 的未读过滤只取 sender_type=="user"，
      故系统消息天然不进未读统计、不走 pull 通道、不入 reads.json 回执；
    - 用 update_json_atomic(MESSAGES_FILE, [], _add) 原地 append（D-1 铁律：不可只返回新对象）；
    - 不写 reads.json（通知类消息无回执）；
    - message_type 默认 "doc_event"，供前端只对文档通知做链接渲染（presence 类不受影响）。

    返回写入的系统消息 dict。
    """
    holder = {}

    def _add(msgs):
        msg = {
            "id": gen_id("msg"),
            "content": content,
            "sender_type": "system",
            "sender_agent_name": None,
            "target_type": None,
            "target_agent_name": None,
            "reply_to_message_id": None,
            "created_at": now_iso(),
            "client_msg_id": None,
            "read_by": [],
            "message_type": message_type,
        }
        msgs.append(msg)          # 原地修改（update_json_atomic 写回被原地修改的 data）
        holder["msg"] = msg
        return None

    update_json_atomic(MESSAGES_FILE, [], _add)
    return holder.get("msg")


def send_user_message(content, target_type, target_agent_name=None, client_msg_id=None):
    """前端发送用户消息：写消息 + 为目标 Agent 建未读回执。对应方案书 §5.3 send_user_message。

    F1 原子化（P0 并发不丢）：消息落库改 update_json_atomic 锁内 read-modify-write
    （对齐 submit_reply 既有范式），消除并发写覆盖；幂等 _dup_by_client_msg_id 也在锁内执行。
    顺序保证：消息先落库、reads 回执后补（回执失败不回滚消息，P0 核心=消息不丢）。
    """
    holder = {}

    def _mut(msgs):
        # D-1 铁律：update_json_atomic 写回的是被原地修改的 msgs，必须原地 append，不能只返回新对象。
        dup = _dup_by_client_msg_id(msgs, client_msg_id)
        if dup:
            holder["dup"] = True
            holder["msg"] = dup
            return None
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
        holder["dup"] = False
        holder["msg"] = msg
        return None

    update_json_atomic(MESSAGES_FILE, [], _mut)
    if holder["dup"]:
        return holder["msg"]

    # reads.json 联动：消息先落库、回执后补；回执失败不回滚消息（已知坑 2 / P0 核心=消息不丢）。
    msg = holder["msg"]

    def _mut_reads(reads):
        # D-1：原地 append，写回生效。
        if target_type == "single":
            if target_agent_name:
                reads.append({"message_id": msg["id"], "agent_name": target_agent_name, "read_at": None})
        elif target_type == "all":
            for a in load_agents():
                # F-b / AC-2.4：@all 回执范围 = 仅 read_scope=="all" 的 Agent（direct 跳过广播）
                if a.get("read_scope", "all") == "all":
                    reads.append({"message_id": msg["id"], "agent_name": a["name"], "read_at": None})
        return None

    update_json_atomic(READS_FILE, [], _mut_reads)
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


def maybe_generate_ack(orig_msg, receiver_name):
    """F-d 自动确认系统消息：B 拉到 A→B 的 single 消息后，给原 sender A 生成 1 条 system ack。

    orig_msg：B 拉到的 single 消息；receiver_name=B（当前拉取方）。
    仅当 orig_msg 由某 agent（sender_agent_name 非空）单发给 B 时触发（人类发的无 agent 可回执）。
    报文：sender_type="system"、target_type="single"、target_agent_name=原 sender、
          visible=0、content="{receiver_name} 已收到你的消息，正在思考，稍后回复"、
          reply_to_message_id=orig_msg.id、sender_agent_name=receiver_name。
    幂等①：messages.json 已存在同 reply_to_message_id 且 target_agent_name==原 sender 的 ack → 跳过。
    幂等②：pull 已读集合只返回一次（首次未读才进 unread → 仅触发一次）。
    落库走 update_json_atomic（全局 RLock），与并发 send/reply 不会互相覆盖（与 c9da420b 同机制）。
    """
    sender = orig_msg.get("sender_agent_name")
    if not sender:
        return                                            # 人类发的，无 agent 可回执
    if orig_msg.get("target_type") != "single":
        return                                            # 仅 single 触发（Q1：A→B 的 single 消息）
    if orig_msg.get("target_agent_name") != receiver_name:
        return
    msg_id = orig_msg.get("id")
    if not msg_id:
        return
    # 幂等①：messages.json 已存在同 reply_to_message_id 的 ack 则跳过
    existing = load_messages()
    for m in existing:
        if (m.get("sender_type") == "system"
                and m.get("reply_to_message_id") == msg_id
                and m.get("target_agent_name") == sender):
            return

    def _add(msgs):
        ack = {
            "id": gen_id("msg"),
            "content": "{0} 已收到你的消息，正在思考，稍后回复".format(receiver_name),
            "sender_type": "system",
            "sender_agent_name": receiver_name,
            "target_type": "single",
            "target_agent_name": sender,               # 回执给原 sender（AC-4.1：target_agent_name==原 sender）
            "reply_to_message_id": msg_id,
            "visible": 0,                              # 网页/历史不渲染（F-d / AC-4.3）
            "created_at": now_iso(),
            "client_msg_id": None,
            "read_by": [],
        }
        msgs.append(ack)                               # 原地修改（update_json_atomic 写回）
        return None

    update_json_atomic(MESSAGES_FILE, [], _add)


def pull_messages(agent_name, workflow_id=None):
    """Agent 拉取@自己且未读的消息，并标记为已读。对应方案书 §5.3 pull_messages。

    workflow_id（T-collab-01 新协作功能）：
    - 若指定，则仅返回 workflow_id 匹配的消息（用于隔离工作流上下文）
    - 若为 None，返回所有相关消息（原有行为）
    """
    msgs = load_messages()
    read_holder = {}

    def _mut(read_set):
        # 注意：update_json_atomic 写入的是被原地修改的 read_set（见 storage.update_json_atomic），
        # 因此这里必须在 read_set 上原地修改，不能只返回新对象（D-1 铁律）。
        s = set(read_set)
        # F4+F5 合并：仅在「首次 pull（无已读集合文件）」时做种子迁移，且全部在锁内完成
        if not agent_read_set_exists(agent_name):
            reg_ts = None
            for a in load_agents():            # 锁内读 agents.json（持全局 _lock，无竞态）
                if a.get("name") == agent_name:
                    reg_ts = a.get("registered_at")
                    break
            seed = {
                r["message_id"]
                for r in load_reads()           # 锁内读 reads.json：该 agent 已读回执 id
                if r.get("agent_name") == agent_name and r.get("read_at") is not None
            }
            if reg_ts:                          # 首拉过滤：注册前 @all 历史一律视为已读（F4）
                seed |= {
                    m["id"] for m in msgs
                    if m.get("sender_type") == "user"
                    and m.get("target_type") == "all"
                    and m.get("created_at") < reg_ts
                }
            s |= seed
        # 读取本 agent 的 read_scope 用于广播路由（F-b / AC-2.2/2.3）
        my_read_scope = "all"
        for a in load_agents():
            if a.get("name") == agent_name:
                my_read_scope = a.get("read_scope", "all")
                break
        unread = []

        # T-collab-01：按 workflow_id 过滤（可选）
        if workflow_id is not None:
            candidate_msgs = [m for m in msgs if m.get("workflow_id") == workflow_id]
        else:
            candidate_msgs = msgs

        for msg in candidate_msgs:
            st = msg.get("sender_type")
            # F-d 自动确认系统消息透传：仅「发给本 agent 且 visible==0」的 ack 返回给本 agent
            # （presence_event 无 visible 字段 → 不命中，仍按原逻辑跳过）
            if st == "system":
                if msg.get("target_agent_name") == agent_name and msg.get("visible") == 0:
                    if msg["id"] not in s:
                        unread.append(msg)
                        s.add(msg["id"])
                continue
            if st != "user" and st != "agent":
                continue
            # user / agent 消息：按 read_scope 路由（F-b）
            targeted = False
            if msg.get("target_type") == "single":
                targeted = (msg.get("target_agent_name") == agent_name)
            elif msg.get("target_type") == "all":
                targeted = (my_read_scope == "all")     # read_scope=direct 跳过 @all 广播
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
    # F-d：对每条「未读、sender 为 user/agent、target single 命中本 agent」的消息，
    # 给原 sender 生成 1 条 system ack（幂等靠 messages.json 已存在同 reply_to_message_id 的 ack + pull 已读集合）。
    for m in unread:
        if (m.get("sender_type") in ("user", "agent")
                and m.get("sender_agent_name")
                and m.get("target_type") == "single"
                and m.get("target_agent_name") == agent_name):
            maybe_generate_ack(m, agent_name)
    if unread:
        # 服务端持久化已读：per-agent 集合（已写入）+ reads.json 回执（前端展示）
        _mark_reads_json(agent_name, [m["id"] for m in unread])
    record_pull(agent_name, len(unread) > 0)  # pull 即心跳：刷新 last_seen + 状态(拉到数据=working / 没拉到=waiting)
    return unread
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


def agent_unread_count(name):
    """R9：返回该 agent 的未读消息数（只读不写）。

    与 agent_has_unread 逻辑一致，但返回数字而非布尔。
    """
    msgs = load_messages()
    if not agent_read_set_exists(name):
        seed = {
            r["message_id"]
            for r in load_reads()
            if r.get("agent_name") == name and r.get("read_at") is not None
        }
        read_set = seed
    else:
        read_set = load_agent_read_set(name)
    count = 0
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
            count += 1
    return count


def mark_reads_json(agent_name, message_ids):
    """R9：前端主动标记消息已读（update reads.json）。幂等：已读的不重复写。"""
    if not message_ids:
        return
    ids = set(message_ids)

    def _mut(reads):
        for r in reads:
            if r["agent_name"] == agent_name and r["message_id"] in ids and r["read_at"] is None:
                r["read_at"] = now_iso()
        return reads

    update_json_atomic(READS_FILE, [], _mut)


def submit_reply(agent_name, content, reply_to_message_id=None, client_msg_id=None,
                 target_type=None, target_agent_name=None):
    """Agent 提交回复：保存回复，并捎带返回该 Agent 剩余未读消息。对应方案书 §5.3 submit_reply。

    F-c：target_type / target_agent_name 透传（缺省 None → 兼容旧义视为回复人类老板，归一化为 "user"）。
    消息追加用 update_json_atomic 保证原子（并发回复不会互相覆盖）。
    """
    # 归一化 target（F-c）：仅接受 single/all/user；其余（含 None）→ "user"（兼容旧调用，回复人类老板）；
    # 非 single 时 target_agent_name 一律清空（@all / @user 无单名目标）。
    if target_type not in ("single", "all", "user"):
        target_type = "user"
    if target_type != "single":
        target_agent_name = None

    def _add(msgs):
        dup = _dup_by_client_msg_id(msgs, client_msg_id)
        if dup:
            return {"dup": True}
        reply = {
            "id": gen_id("msg"),
            "content": content,
            "sender_type": "agent",
            "sender_agent_name": agent_name,
            "target_type": target_type,
            "target_agent_name": target_agent_name,
            "reply_to_message_id": reply_to_message_id,   # F3：不再静默丢弃（缺省 None → JSON null，兼容旧调用）
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
        # F-d / AC-4.3：过滤可见性为 0 的系统确认消息（presence_event 无 visible 字段 → 仍展示）
        if m.get("visible") == 0:
            continue
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
    # R2: 记录清理审计
    try:
        from app.services import audit as audit_svc
        audit_svc.record_action(
            actor="system",
            action=audit_svc.ACTION_CLEANUP_MESSAGES,
            target_type="messages",
            summary=f"archived={archived}, remaining={remaining}",
        )
    except Exception:
        pass
    return {"archived": archived, "remaining": remaining}
