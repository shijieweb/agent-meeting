# -*- coding: utf-8 -*-
"""Agent 注册与查询逻辑。对应方案书 §5.3 register_agent / load_agents。"""
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
    """
    agents = load_agents()
    if any(a.get("name") == name for a in agents):
        return agents, False
    agents.append({"name": name, "registered_at": now_iso(), "last_seen": now_iso(), "status": "waiting", "session": False})
    save_agents(agents)
    return agents, True


def agent_exists(name):
    """目标/回复 Agent 是否存在（用于发送/回复前的存在性校验）。"""
    return any(a.get("name") == name for a in load_agents())


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
    会话级状态：整个开会期间保持绿色，不因回话/改代码的 pull 间隙而变灰——解决『30分钟没拉消息就不知道在不在』。"""
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
