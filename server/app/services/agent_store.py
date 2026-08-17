# -*- coding: utf-8 -*-
"""Agent 注册与查询逻辑。对应方案书 §5.3 register_agent / load_agents。

在线/离线状态自动化（presence 管理）：
- 三态 online/lost/offline 由 derive_state(a, now) 纯函数从 last_seen + status 推导，
  不加任何存储字段；presence 仅接口输出，不落盘。
- 惰性清扫 scan_and_sweep：status 接口触发 + SWEEP_INTERVAL(60s) 节流；
  失联(session=true 超 LOST_TIMEOUT) → 置 offline；离线/失联超保留期 → 删除记录 + 删 agent_read_<name>.json。
- register 同名：在线幂等（reactivated=false）；失联/离线 → 唤醒重置（reactivated=true）。
"""
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

# JSONL 日志文件名（DATA_DIR 下，gitignored，只追加不覆盖）
SWEEP_LOG = "sweep_log.jsonl"
STATUS_EVENTS_LOG = "status_events.jsonl"

# 时间口径：统一本地时间 "%Y-%m-%dT%H:%M:%S"（与 now_iso / 既有解析一致）。
_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def load_agents():
    return read_json(AGENTS_FILE, [])


def save_agents(agents):
    write_json(AGENTS_FILE, agents)


def register_agent(name):
    """注册 Agent；名字规范化（strip）+ 同名唤醒语义。

    返回 (agents, created, info)：
      - created=True：本次为新注册；
      - created=False：名字已存在；info["reactivated"]=True 表示失联/离线旧记录被唤醒重置，
        False 表示在线同名幂等（不动记录）。
    """
    name = (name or "").strip()
    agents = load_agents()
    for a in agents:
        if a.get("name") == name:
            state = derive_state(a)
            if state == "online":
                return agents, False, {"reactivated": False}   # 在线：幂等，不动
            # 失联/离线（无论是否超保留期）→ 唤醒重置（老板拍板 §5.1-4）
            ts = now_iso()
            a["registered_at"] = ts
            a["last_seen"] = ts
            a["status"] = "waiting"
            a["session"] = False
            a["token_hash"] = None            # token 预留（可空，不实现校验）
            save_agents(agents)
            append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "reactivated"})
            return agents, False, {"reactivated": True}
    # 先清僵尸、再追加新 agent（防注册即删：新注册 last_seen==registered_at，
    # 若保留历史僵尸不清理，_should_delete 的占位判定可能误伤——沿用原注释语义）
    agents = [a for a in agents if not _should_delete(a)]
    ts = now_iso()
    agents.append({"name": name, "registered_at": ts, "last_seen": ts,
                   "status": "waiting", "session": False, "token_hash": None})
    save_agents(agents)
    append_jsonl(STATUS_EVENTS_LOG, {"ts": ts, "name": name, "event": "registered"})
    return agents, True, {"reactivated": False}


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
            return True
    return False


def get_agent_statuses():
    """返回所有 Agent 状态，含派生 presence 字段（online/lost/offline，仅接口输出，不落盘）。"""
    agents = load_agents()
    result = []
    for a in agents:
        result.append({
            "name": a.get("name"),
            "last_seen": a.get("last_seen"),
            "status": a.get("status", "waiting"),
            "session": a.get("session", False),
            "presence": derive_state(a),
        })
    return result
