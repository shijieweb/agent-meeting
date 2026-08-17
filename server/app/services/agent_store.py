# -*- coding: utf-8 -*-
"""Agent 注册与查询逻辑。对应方案书 §5.3 register_agent / load_agents。"""
import time
from .storage import read_json, write_json, now_iso

AGENTS_FILE = "agents.json"


def load_agents():
    return read_json(AGENTS_FILE, [])


def save_agents(agents):
    write_json(AGENTS_FILE, agents)


def register_agent(name):
    """注册 Agent；名字已存在则视为已注册，不重复添加。

    返回 (agents, created)：created=True 表示本次新注册，False 表示已存在。
    满足方案书 §7 T-REG-02 / T-PERM-01（重注册需提示唯一性）。

    注意：必须先 prune 历史僵尸、再追加新 agent。否则新注册的 agent 因
    last_seen==registered_at 会被 _is_zombie 误判为僵尸、被自己的清理逻辑当场删掉
    （曾导致注册成功却查不到、发送报 target not found）。
    """
    agents = load_agents()
    if any(a.get("name") == name for a in agents):
        return agents, False
    agents = [a for a in agents if not _is_zombie(a)]  # 先清历史僵尸（不含本次新注册）
    agents.append({"name": name, "registered_at": now_iso(), "last_seen": now_iso(), "status": "waiting", "session": False})
    save_agents(agents)
    return agents, True


def agent_exists(name):
    """目标/回复 Agent 是否存在（用于发送/回复前的存在性校验）。"""
    return any(a.get("name") == name for a in load_agents())


INACTIVE_DAYS = 7
# 新注册宽限期：刚注册（last_seen==registered_at）但还没来得及首 pull 的 agent 不能算僵尸，
# 否则注册即被清掉。超过该时长仍未任何活动才判定为被遗弃的占位僵尸。
ZOMBIE_GRACE_SECONDS = 3600


def _is_zombie(a):
    """僵尸占位 = 注册后超宽限期仍无任何活动（last_seen==registered_at 且已超 grace）或长期离线（无 session 且超阈值）。"""
    if a.get("session"):
        return False
    reg = a.get("registered_at")
    seen = a.get("last_seen")
    now = time.time()
    if seen == reg:          # 注册后从未被 record_pull 刷新过 last_seen
        try:
            reg_ts = time.mktime(time.strptime(reg, "%Y-%m-%dT%H:%M:%S"))
            if now - reg_ts > ZOMBIE_GRACE_SECONDS:  # 超宽限期仍无活动 → 真被遗弃的占位
                return True
        except (ValueError, TypeError):
            pass
        return False         # 宽限期内：刚注册、即将首 pull，不算僵尸
    try:
        seen_ts = time.mktime(time.strptime(seen, "%Y-%m-%dT%H:%M:%S"))
        if now - seen_ts > INACTIVE_DAYS * 86400:
            return True
    except (ValueError, TypeError):
        pass
    return False


def prune_zombie_agents(inactive_days=INACTIVE_DAYS):
    """删除僵尸占位 agent（测试残留等），返回删除数量。

    注意：不再于 register_agent 内自动调用——新注册 agent 的 last_seen==registered_at，
    若自动 prune 会把自己删掉（已改为 register 时先清旧僵尸再追加新 agent）。
    现仅由 POST /api/agents/prune 手动触发，或在需要时显式调用。"""
    agents = load_agents()
    before = len(agents)
    kept = [a for a in agents if not _is_zombie(a)]
    removed = before - len(kept)
    if removed:
        save_agents(kept)
    return removed


def list_active_agent_names():
    """只返回活跃（非僵尸）agent 名，供 /api/agents 默认使用，避免僵尸占位进入已读统计。"""
    return [a.get("name") for a in load_agents() if a.get("name") and not _is_zombie(a)]


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
            return True
    return False


def get_agent_statuses():
    """返回所有 Agent 的 {name, last_seen}，供前端判断在线/工作状态。"""
    return [{"name": a.get("name"), "last_seen": a.get("last_seen"), "status": a.get("status", "waiting"), "session": a.get("session", False)} for a in load_agents()]
