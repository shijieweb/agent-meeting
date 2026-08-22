# -*- coding: utf-8 -*-
"""Agent 注册与查询逻辑。对应方案书 §5.3 register_agent / load_agents。

在线/离线状态自动化（presence 管理）：
- 三态 online/lost/offline 由 derive_state(a, now) 纯函数从 last_seen + status 推导，
  不加任何存储字段；presence 仅接口输出，不落盘。
- 惰性清扫 scan_and_sweep：status 接口触发 + SWEEP_INTERVAL(60s) 节流；
  失联(session=true 超 LOST_TIMEOUT) → 置 offline；离线/失联超保留期 → 删除记录 + 删 agent_read_<name>.json。
- register 同名：在线幂等（reactivated=false）；失联/离线 → 唤醒重置（reactivated=true）。
"""
import json
import threading
import time

from app.config import LOST_TIMEOUT, LOST_GRACE_BEFORE_DELETE, SWEEP_INTERVAL
from .storage import (
    read_json,
    write_json,
    now_iso,
    update_json_atomic,
    delete_agent_read_set,
    append_jsonl,
)

AGENTS_FILE = "agents.json"
READS_FILE = "reads.json"    # 引用同 message_store.READS_FILE，避免顶层 import 形成循环依赖

# JSONL 日志文件名（DATA_DIR 下，gitignored，只追加不覆盖）
SWEEP_LOG = "sweep_log.jsonl"
STATUS_EVENTS_LOG = "status_events.jsonl"

# 时间口径：统一本地时间 "%Y-%m-%dT%H:%M:%S"（与 now_iso / 既有解析一致）。
_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def _emit_system_event(event, name):
    """在事件点向消息流追加一条系统消息（presence_event，AC-3.4/4.1/4.2/4.3）。

    函数体内局部导入 message_store：message_store 模块顶层 import 了本模块（load_agents），
    若本模块顶层再 import message_store 会形成循环依赖；延迟到函数体内导入即可避免。
    调用方保证事件对应状态已落盘成功后调用（事件写入时机约束）。
    """
    if not name:
        return
    from .message_store import append_system_event
    append_system_event(event, name)


def load_agents():
    return read_json(AGENTS_FILE, [])


def save_agents(agents):
    write_json(AGENTS_FILE, agents)


def register_agent(name):
    """注册 Agent；名字规范化（strip）+ 同名唤醒语义。

    F2 原子化（P0 并发不丢）：agents.json 改 update_json_atomic 锁内 read-modify-write
    （锁内同名判定 / 唤醒重置 / 先清僵尸再追加），消除并发注册后写覆盖先写。
    返回 (agents, created, info)：
      - created=True：本次为新注册；
      - created=False：名字已存在；info["reactivated"]=True 表示失联/离线旧记录被唤醒重置，
        False 表示在线同名幂等（不动记录）。
    """
    name = (name or "").strip()
    holder = {}

    def _mut(agents):
        # D-1 铁律：update_json_atomic 写回的是被原地修改的 agents，必须原地改，不能只返回新对象。
        for a in agents:                       # ① 同名判定（锁内）
            if a.get("name") == name:
                state = derive_state(a)
                if state == "online":
                    holder.update(created=False, info={"reactivated": False})
                    return None                # 在线幂等：不动记录（不写事件日志）
                # ② 唤醒重置（失联/离线 → reactivated；老板拍板 §5.1-4）
                ts = now_iso()
                a["registered_at"] = ts        # F4 前提：唤醒即更新 registered_at（旧 @all 不回灌）
                a["last_seen"] = ts
                a["status"] = "waiting"
                a["session"] = False
                a["token_hash"] = None         # token 预留（可空，不实现校验）
                holder.update(created=False, info={"reactivated": True}, ts=ts)
                return None
        # ③ 未找到：先清僵尸、再追加新 agent（防注册即删：新注册 last_seen==registered_at，
        #    若保留历史僵尸不清理，_should_delete 的占位判定可能误伤——沿用原注释语义）
        agents[:] = [a for a in agents if not _should_delete(a)]
        ts = now_iso()
        agents.append({"name": name, "registered_at": ts, "last_seen": ts,
                       "status": "waiting", "session": False, "token_hash": None,
                       "description": "", "read_scope": "all"})   # F-b/h：补 description/read_scope 缺省
        holder.update(created=True, info={"reactivated": False}, ts=ts)
        return None

    update_json_atomic(AGENTS_FILE, [], _mut)   # 锁内 read-modify-write，写回原地修改后的 agents

    # 事件日志：状态写回成功后再记（与现状 save→append_jsonl 同序；append_jsonl 自身持锁）
    ts = holder.get("ts") or now_iso()
    if holder["created"]:
        append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "registered"})
    elif holder["info"]["reactivated"]:
        append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "reactivated"})
        _emit_system_event("reactivated", name)   # state-persist 保活：重注册唤醒 → 系统消息「X 重新上线了」
    # 在线同名幂等：不写事件日志（原实现也不写）
    return load_agents(), holder["created"], holder["info"]


def agent_exists(name):
    """目标/回复 Agent 是否存在（用于发送/回复前的存在性校验）。"""
    return any(a.get("name") == name for a in load_agents())


# ---- 已废弃常量/函数（D6：删除判定统一由 _should_delete 取代）----
INACTIVE_DAYS = 7  # [DEPRECATED] 旧 7 天离线删除窗口，已由 LOST_TIMEOUT+LOST_GRACE_BEFORE_DELETE 取代
# 新注册宽限期：刚注册（last_seen==registered_at）但还没来得及首 pull 的 agent 不能算僵尸，
# 否则注册即被清掉。超过该时长仍未任何活动才判定为被遗弃的占位僵尸。
ZOMBIE_GRACE_SECONDS = 3600


def _is_zombie(a):
    """[DEPRECATED] 旧僵尸判定（7 天窗口 + session=true 永不算僵尸）。

    已由 _should_delete（占位 >1h / 离线 >6h20min）取代；保留本函数仅供历史调用/测试兼容，新代码勿用。
    """
    if a.get("session"):
        return False
    reg = a.get("registered_at")
    seen = a.get("last_seen")
    now = time.time()
    if seen == reg:          # 注册后从未被 record_pull 刷新过 last_seen
        try:
            reg_ts = time.mktime(time.strptime(reg, _ISO_FMT))
            if now - reg_ts > ZOMBIE_GRACE_SECONDS:  # 超宽限期仍无活动 → 真被遗弃的占位
                return True
        except (ValueError, TypeError):
            pass
        return False         # 宽限期内：刚注册、即将首 pull，不算僵尸
    try:
        seen_ts = time.mktime(time.strptime(seen, _ISO_FMT))
        if now - seen_ts > INACTIVE_DAYS * 86400:
            return True
    except (ValueError, TypeError):
        pass
    return False


# ---------------------------------------------------------------------------
# 在线/离线状态推导（纯函数，不落盘）
# ---------------------------------------------------------------------------

def _parse_iso(s):
    """ISO 本地时间字符串 → epoch 秒；缺失/非法返回 None。"""
    if not s:
        return None
    try:
        return time.mktime(time.strptime(s, _ISO_FMT))
    except (ValueError, TypeError):
        return None


def _age_of(s, now=None):
    """ISO 字符串距今秒数；无法解析返回 None。"""
    now = now if now is not None else time.time()
    ts = _parse_iso(s)
    if ts is None:
        return None
    return now - ts


def derive_state(a, now=None):
    """纯函数推导三态：online / lost / offline。

    - status == 'offline'       → offline（显式收工或已被清扫下线）
    - last_seen 缺失/不可解析    → offline（保守）
    - 距今 > LOST_TIMEOUT(1200s) → lost（应在线但心跳中断，疑似崩溃）
    - 否则                      → online
    """
    now = now if now is not None else time.time()
    if a.get("status") == "offline":
        return "offline"
    age = _age_of(a.get("last_seen"), now)
    if age is None:
        return "offline"
    if age > LOST_TIMEOUT:
        return "lost"
    return "online"


# 思考状态阈值：在线 + working + 距上次心跳 > N 秒 → 视为"正在思考中"
# 5s 足够覆盖轮询间隔（loop.py 默认 3s），又不会误触发短静默。
THINKING_THRESHOLD_SECONDS = 5


def derive_thinking_status(a, now=None):
    """纯函数推导 thinking 状态（仅接口输出，不落盘）。

    判定：presence==online AND status==working AND last_seen 距今 > THINKING_THRESHOLD_SECONDS
    → 表示 agent 当前没在拉消息（不是 working 的即时心跳），而是在"思考"/生成回复中。
    """
    if derive_state(a, now) != "online":
        return False
    if a.get("status") != "working":
        return False
    age = _age_of(a.get("last_seen"), now)
    if age is None:
        return False
    return age > THINKING_THRESHOLD_SECONDS


def _should_delete(a, now=None):
    """删除判定：① 占位从未活动（注册后超 ZOMBIE_GRACE_SECONDS 仍 last_seen==registered_at）；
    ② 离线/失联超保留期（last_seen 距今 > LOST_TIMEOUT + LOST_GRACE_BEFORE_DELETE = 6h20min）。"""
    now = now if now is not None else time.time()
    if a.get("last_seen") == a.get("registered_at"):
        reg_age = _age_of(a.get("registered_at"), now)
        if reg_age is not None and reg_age > ZOMBIE_GRACE_SECONDS:
            return True
        return False         # 宽限期内：刚注册、即将首 pull，不删（AC-3.4）
    age = _age_of(a.get("last_seen"), now)
    if age is None:
        return False         # 无法解析：保守不删
    return age > LOST_TIMEOUT + LOST_GRACE_BEFORE_DELETE


# ---------------------------------------------------------------------------
# 惰性清扫（status 接口触发 + 60s 节流；无后台常驻线程）
# ---------------------------------------------------------------------------

_sweep_lock = threading.Lock()          # 节流标记锁（模块级，防并发 status 同时扫描）
_last_sweep_ts = 0.0                    # 上次真正扫描时间戳


def scan_and_sweep() -> int:
    """惰性清扫：失联下线 + 幽灵删除。幂等；SWEEP_INTERVAL(60s) 节流；返回本次删除数量。"""
    global _last_sweep_ts
    now = time.time()
    with _sweep_lock:                    # 节流检查在锁内，防并发 status 同时触发
        if now - _last_sweep_ts < SWEEP_INTERVAL:
            return 0
        _last_sweep_ts = now

    removed_names = []
    lost_names = []

    def _mut(agents):
        keep = []
        for a in agents:
            # ① 失联自动下线：derive_state == 'lost' → 置 offline（last_seen 不变=失联证据）
            if derive_state(a, now) == 'lost':
                a['session'] = False
                a['status'] = 'offline'
                lost_names.append(a.get('name'))
            # ② 删除判定（顺序在①之后：先下线再计保留期）
            if _should_delete(a, now):
                removed_names.append(a.get('name'))
            else:
                keep.append(a)
        agents[:] = keep                   # update_json_atomic 写回原地修改的 data
        return None

    update_json_atomic(AGENTS_FILE, [], _mut)   # R4：全局锁内 read-modify-write

    # 动作日志：失联下线 + 删除（只追加，JSONL；幂等——已 offline 的记录不再触发 lost）
    ts = now_iso()
    for name in lost_names:
        if not name:
            continue
        append_jsonl(SWEEP_LOG, {"ts": ts, "name": name, "action": "lost_to_offline",
                                 "reason": "no heartbeat > LOST_TIMEOUT (1200s)"})
        append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "lost"})
        _emit_system_event("lost", name)   # 失联自动下线 → 系统消息「X 已离线（失联超时）」（AC-4.3）
    for name in removed_names:
        if not name:
            continue
        delete_agent_read_set(name)         # D4：删除 agent_read_<name>.json
        append_jsonl(SWEEP_LOG, {"ts": ts, "name": name, "action": "deleted",
                                 "reason": "offline/lost beyond retention (LOST_TIMEOUT+6h, 22800s)"})
        append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "deleted"})
    return len(removed_names)


# ---------------------------------------------------------------------------
# 查询 / 状态
# ---------------------------------------------------------------------------

def prune_zombie_agents(inactive_days=None):
    """手动清理僵尸占位 agent（管理兜底）。

    删除判定统一走 _should_delete（占位 >1h / 离线 >6h20min）；
    删除记录的同时清理 agent_read_<name>.json 孤儿文件。
    inactive_days 参数已废弃（旧 7 天窗口），保留仅兼容旧调用。
    """
    agents = load_agents()
    removed_names = []
    kept = []
    for a in agents:
        if _should_delete(a):
            removed_names.append(a.get("name"))
        else:
            kept.append(a)
    removed = len(removed_names)
    if removed:
        save_agents(kept)
        for n in removed_names:
            if n:
                delete_agent_read_set(n)
    return removed


def list_active_agent_names():
    """只返回在线 agent 名（derive_state == 'online'），供 /api/agents 默认列表与 @all 已读分母。

    语义从旧「活跃（非僵尸）」改为「在线」（design 决策 6 / D4）：失联/离线 agent 不再进入默认列表。
    """
    return [a.get("name") for a in load_agents() if a.get("name") and derive_state(a) == "online"]


def list_agent_names():
    return [a.get("name") for a in load_agents() if a.get("name")]


def record_pull(name, got_data):
    """记录一次 pull（pull 即心跳 + 状态）：刷新 last_seen，并据是否拉到消息置状态。
    got_data=True → working(处理中)；got_data=False → waiting(待命中)。
    对应老板逻辑：拉到数据=去干活(处理中)；没拉到=在线等待(待命中)；久未拉=离线(前端据 last_seen 判定)。
    session 仅影响前端离线判定窗口(600s)与"需重唤"，不改变 working/waiting 着色。"""
    agents = load_agents()
    for a in agents:
        if a.get("name") == name:
            a["last_seen"] = now_iso()
            if got_data:
                a["status"] = "working"
            else:
                a["status"] = "waiting"
            save_agents(agents)
            return True
    return False


def set_session(name, active):
    """开会=进入会话(置 working + session=True)、结束会议=退出(置 offline + session=False)。
    注意：本函数只在 init/end 时刻置状态；开会中空轮 pull 仍由 record_pull 按 got_data 翻 working/waiting，
    故开会期间无消息时显示待命(黄)而非强制锁绿（见需求文档 A-2：2026-08-17 决策接受待命）。"""
    agents = load_agents()
    for a in agents:
        if a.get("name") == name:
            a["session"] = active
            a["status"] = "working" if active else "offline"
            a["last_seen"] = now_iso()
            save_agents(agents)
            append_jsonl(STATUS_EVENTS_LOG, {
                "ts": now_iso(),
                "name": name,
                "event": "session_on" if active else "session_off",
            })
            _emit_system_event("init" if active else "end", name)   # 显式上线/下线 → 系统消息（AC-4.3）
            return True
    return False


def get_agent_statuses():
    """返回所有 Agent 状态，含派生 presence + thinking 字段（仅接口输出，不落盘）。"""
    agents = load_agents()
    result = []
    for a in agents:
        result.append({
            "name": a.get("name"),
            "last_seen": a.get("last_seen"),
            "status": a.get("status", "waiting"),
            "session": a.get("session", False),
            "presence": derive_state(a),
            "thinking": derive_thinking_status(a),
        })
    return result


# ---------------------------------------------------------------------------
# F-a / F-b / F-f / F-h / F-i：白名单管理接口（不鉴权，部署层内网隔离）
# ---------------------------------------------------------------------------

def manage_create(name, description="", read_scope="all"):
    """白名单预注册（幂等 upsert）。

    name 不存在 → append 新记录（registered_at/last_seen=now, status=waiting,
    session=False, token_hash=None, description, read_scope）；
    已存在 → 返回既有记录（200，不覆盖），便于 Q4 seed 幂等重跑。

    返回：该 name 对应的全量记录（新创建或既有）。
    """
    name = (name or "").strip()
    holder = {}

    def _mut(agents):
        for a in agents:
            if a.get("name") == name:
                holder["agent"] = a          # 已存在：返回既有，不覆盖
                return None
        ts = now_iso()
        rec = {
            "name": name,
            "registered_at": ts,
            "last_seen": ts,
            "status": "waiting",
            "session": False,
            "token_hash": None,
            "description": description,
            "read_scope": read_scope,
        }
        agents.append(rec)
        holder["agent"] = rec
        return None

    update_json_atomic(AGENTS_FILE, [], _mut)
    return holder["agent"]


def manage_delete(name):
    """白名单删除：从 agents.json 移除该 name；级联清 reads.json 中 agent_name==name 的全部回执（F-f）；
    并删除 agent_read_<name>.json（per-agent 已读集合文件，幂等）。"""
    def _mut_agents(agents):
        agents[:] = [a for a in agents if a.get("name") != name]
        return None

    update_json_atomic(AGENTS_FILE, [], _mut_agents)

    def _mut_reads(reads):
        # 级联删：清掉该 agent 的全部回执记录（F-f 级联删脏数据）
        reads[:] = [r for r in reads if r.get("agent_name") != name]
        return None

    update_json_atomic(READS_FILE, [], _mut_reads)
    delete_agent_read_set(name)         # 清理 per-agent 已读集合文件
    return True


def manage_update(name, description=None, read_scope=None):
    """白名单更新：仅改 description / read_scope（name 不可改，F-i）。

    返回更新后的全量记录；name 不存在返回 None（调用方应转 404）。
    """
    holder = {}

    def _mut(agents):
        for a in agents:
            if a.get("name") == name:
                if description is not None:
                    a["description"] = description
                if read_scope is not None:
                    a["read_scope"] = read_scope
                holder["agent"] = a
                return None
        return None

    update_json_atomic(AGENTS_FILE, [], _mut)
    return holder.get("agent")


def manage_list():
    """白名单列表：返回全部 Agent（含 presence / has_unread / role / capabilities / team）。

    presence：derive_state 派生（online/lost/offline）；
    has_unread：message_store.agent_has_unread（局部导入避免与 message_store→agent_store 循环依赖）。
    role/capabilities/team：新协作字段，缺失时返回默认值（general/[]/''）。
    """
    from .message_store import agent_has_unread
    agents = load_agents()
    result = []
    for a in agents:
        name = a.get("name")
        # 新协作字段回退兼容：缺失时给默认值
        role = a.get("role", "general") or "general"
        try:
            capabilities = json.loads(a.get("capabilities", "[]"))
            if not isinstance(capabilities, list):
                capabilities = []
        except (ValueError, TypeError):
            capabilities = []
        team = a.get("team") or ""
        result.append({
            "name": name,
            "description": a.get("description", ""),
            "read_scope": a.get("read_scope", "all"),
            "status": a.get("status", "waiting"),
            "last_seen": a.get("last_seen"),
            "session": a.get("session", False),
            "registered_at": a.get("registered_at"),
            "presence": derive_state(a),
            "has_unread": agent_has_unread(name),
            "role": role,
            "capabilities": capabilities,
            "team": team,
        })
    return result
