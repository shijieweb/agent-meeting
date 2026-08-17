# -*- coding: utf-8 -*-
"""在线/离线状态自动化 —— 单元测试（design §11.1 全表）。

隔离模式：monkeypatch `app.config.DATA_DIR` 到 tmp 临时目录（storage._path 每次调用
`from app.config import DATA_DIR` 取当前值，天然生效），绝不触碰生产 server/data。
"""
import time

import pytest

from app import config
from app.services import agent_store, storage

_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


@pytest.fixture(autouse=True)
def iso_dir(tmp_path, monkeypatch):
    """每个用例独立 tmp 数据目录；并重置清扫节流标记（防用例间串扰）。"""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_store, "_last_sweep_ts", 0.0)
    return tmp_path


def iso_before(seconds):
    """now - seconds 的 ISO 本地时间字符串。"""
    return time.strftime(_ISO_FMT, time.localtime(time.time() - seconds))


def make_agent(name, last_seen_ago, status="waiting", session=False, registered_ago=99999):
    return {
        "name": name,
        "registered_at": iso_before(registered_ago),
        "last_seen": iso_before(last_seen_ago),
        "status": status,
        "session": session,
    }


# ---------------------------------------------------------------------------
# derive_state：三态推导
# ---------------------------------------------------------------------------

def test_derive_state_online_boundary():
    # 用整数秒 now，与 last_seen 的秒级粒度对齐，避免浮点 ±1ms 抖动
    now = int(time.time())
    a1 = {"status": "waiting", "last_seen": time.strftime(_ISO_FMT, time.localtime(now - 1199))}
    a2 = {"status": "waiting", "last_seen": time.strftime(_ISO_FMT, time.localtime(now - 1200))}
    a3 = {"status": "waiting", "last_seen": time.strftime(_ISO_FMT, time.localtime(now - 1201))}
    assert agent_store.derive_state(a1, now) == "online"
    assert agent_store.derive_state(a2, now) == "online"    # ≤1200s 在线（边界含）
    assert agent_store.derive_state(a3, now) == "lost"      # >1200s 失联


def test_derive_state_offline_priority():
    # status=offline 即使 last_seen 新鲜也是 offline
    a = {"status": "offline", "last_seen": iso_before(1)}
    assert agent_store.derive_state(a) == "offline"


def test_derive_state_missing_or_invalid_last_seen():
    assert agent_store.derive_state({"status": "waiting"}) == "offline"
    assert agent_store.derive_state({"status": "waiting", "last_seen": "garbage"}) == "offline"
    assert agent_store.derive_state({"status": "waiting", "last_seen": None}) == "offline"


# ---------------------------------------------------------------------------
# _should_delete：删除判定
# ---------------------------------------------------------------------------

def test_should_delete_placeholder_never_active():
    reg = iso_before(3700)   # 注册 1h+ 后仍 last_seen==registered_at（占位从未活动）
    a = {"name": "P", "registered_at": reg, "last_seen": reg, "status": "waiting", "session": False}
    assert agent_store._should_delete(a) is True


def test_should_delete_offline_past_retention():
    # 离线超 6h20min（22800s）→ 删
    a = make_agent("O", last_seen_ago=22801, status="offline")
    assert agent_store._should_delete(a) is True


def test_should_delete_within_retention():
    # 保留期内（<22800s）→ 不删；在线/失联均保留
    a = make_agent("W", last_seen_ago=100, status="offline")
    assert agent_store._should_delete(a) is False
    b = make_agent("L", last_seen_ago=1300, status="waiting", session=True)   # 失联但保留期
    assert agent_store._should_delete(b) is False


def test_should_delete_new_register_protected():
    # 新注册（last_seen==registered_at，宽限期内）→ 不删（AC-3.4）
    now_iso = storage.now_iso()
    a = {"name": "New", "registered_at": now_iso, "last_seen": now_iso, "status": "waiting", "session": False}
    assert agent_store._should_delete(a) is False


# ---------------------------------------------------------------------------
# scan_and_sweep：失联下线 / 删除 / 幂等 / 节流
# ---------------------------------------------------------------------------

def test_sweep_lost_session_to_offline():
    old_seen = iso_before(1300)   # 超 1200s 失联
    agent_store.save_agents([make_agent("Dead", last_seen_ago=1300, status="waiting", session=True, registered_ago=99999)])
    agent_store.scan_and_sweep()
    agents = agent_store.load_agents()
    assert len(agents) == 1
    assert agents[0]["status"] == "offline"
    assert agents[0]["session"] is False
    assert agents[0]["last_seen"] == old_seen   # last_seen 不变（失联证据）


def test_sweep_delete_and_read_set():
    name = "GhostDel"
    agent_store.save_agents([make_agent(name, last_seen_ago=23000, status="offline")])
    storage.save_agent_read_set(name, ["m1", "m2"])
    assert storage.agent_read_set_exists(name)
    removed = agent_store.scan_and_sweep()
    assert removed == 1
    assert all(a.get("name") != name for a in agent_store.load_agents())
    assert not storage.agent_read_set_exists(name)   # D4：孤儿 read set 一并删除


def test_sweep_idempotent():
    agent_store.save_agents([make_agent("X", last_seen_ago=1300, status="waiting", session=True, registered_ago=99999)])
    agent_store.scan_and_sweep()
    snapshot = agent_store.load_agents()
    agent_store._last_sweep_ts = 0.0                  # 重置节流再扫
    removed = agent_store.scan_and_sweep()
    assert removed == 0
    assert agent_store.load_agents() == snapshot      # 幂等：无变化


def test_sweep_throttle(monkeypatch):
    # 节流：上次扫描 1s 前 → 第二次调用直接返回 0，即使有可删记录也不扫
    monkeypatch.setattr(agent_store, "_last_sweep_ts", time.time() - 1)
    agent_store.save_agents([make_agent("Y", last_seen_ago=23000, status="offline")])
    removed = agent_store.scan_and_sweep()
    assert removed == 0
    assert any(a.get("name") == "Y" for a in agent_store.load_agents())  # 未被删（节流跳过）


def test_sweep_online_agent_untouched():
    # 正常在线 agent（last_seen 新鲜）绝不被误判失联（AC-2.4）
    agent_store.save_agents([make_agent("Alive", last_seen_ago=10, status="waiting", session=True)])
    removed = agent_store.scan_and_sweep()
    assert removed == 0
    agents = agent_store.load_agents()
    assert agents[0]["status"] == "waiting"
    assert agents[0]["session"] is True


# ---------------------------------------------------------------------------
# register：在线幂等 / 失联离线唤醒 / strip / 新建
# ---------------------------------------------------------------------------

def test_register_online_idempotent():
    agent_store.register_agent("Alive")
    first_reg = agent_store.load_agents()[0]["registered_at"]
    agents, created, info = agent_store.register_agent("Alive")
    assert created is False
    assert info["reactivated"] is False
    assert agents[0]["registered_at"] == first_reg   # 在线同名：不重置


def test_register_wake_lost():
    # 失联旧记录（session=true 超 1200s）→ 唤醒重置（不等清扫）
    agent_store.save_agents([make_agent("Wake", last_seen_ago=1300, status="waiting", session=True, registered_ago=99999)])
    agents, created, info = agent_store.register_agent("Wake")
    assert created is False
    assert info["reactivated"] is True
    rec = agents[0]
    assert rec["status"] == "waiting"
    assert rec["session"] is False
    assert rec["registered_at"] == rec["last_seen"]   # 都重置为 now


def test_register_wake_offline():
    # 离线旧记录（保留期内）→ 唤醒
    agent_store.save_agents([make_agent("Wake2", last_seen_ago=600, status="offline", session=False, registered_ago=99999)])
    agents, created, info = agent_store.register_agent("Wake2")
    assert created is False
    assert info["reactivated"] is True
    assert agents[0]["status"] == "waiting"
    assert agents[0]["session"] is False


def test_register_strip():
    agents, created, info = agent_store.register_agent("  Spaced  ")
    assert created is True
    assert any(a.get("name") == "Spaced" for a in agents)


def test_register_new():
    agents, created, info = agent_store.register_agent("BrandNew")
    assert created is True
    assert info["reactivated"] is False
    rec = agents[0]
    assert rec["name"] == "BrandNew"
    assert rec["status"] == "waiting"
    assert rec["session"] is False


# ---------------------------------------------------------------------------
# list_active_agent_names：仅在线语义
# ---------------------------------------------------------------------------

def test_list_active_online_only():
    agent_store.save_agents([
        make_agent("OnlineA", last_seen_ago=10, status="waiting", session=True),
        make_agent("LostB", last_seen_ago=1300, status="waiting", session=True),   # 失联
        make_agent("OfflineC", last_seen_ago=10, status="offline", session=False),  # 显式收工
        make_agent("StaleD", last_seen_ago=23000, status="offline", session=False), # 超保留期
    ])
    names = agent_store.list_active_agent_names()
    assert names == ["OnlineA"]


# ---------------------------------------------------------------------------
# get_agent_statuses：presence 派生字段
# ---------------------------------------------------------------------------

def test_get_agent_statuses_presence():
    agent_store.save_agents([
        make_agent("OnlineA", last_seen_ago=10, status="waiting", session=True),
        make_agent("LostB", last_seen_ago=1300, status="waiting", session=True),
        make_agent("OfflineC", last_seen_ago=10, status="offline", session=False),
    ])
    statuses = agent_store.get_agent_statuses()
    by_name = {s["name"]: s for s in statuses}
    assert by_name["OnlineA"]["presence"] == "online"
    assert by_name["LostB"]["presence"] == "lost"
    assert by_name["OfflineC"]["presence"] == "offline"
    assert "presence" in statuses[0]  # 派生字段仅输出


# ---------------------------------------------------------------------------
# prune_zombie_agents：手动兜底走 _should_delete + 清理 read set
# ---------------------------------------------------------------------------

def test_prune_uses_should_delete():
    agent_store.save_agents([
        make_agent("Keep", last_seen_ago=10, status="waiting", session=True),
        make_agent("Ghost", last_seen_ago=23000, status="offline", session=False),
    ])
    storage.save_agent_read_set("Ghost", ["x"])
    removed = agent_store.prune_zombie_agents()
    assert removed == 1
    assert all(a.get("name") != "Ghost" for a in agent_store.load_agents())
    assert not storage.agent_read_set_exists("Ghost")
