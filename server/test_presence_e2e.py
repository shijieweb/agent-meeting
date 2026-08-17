# -*- coding: utf-8 -*-
"""在线/离线状态自动化 —— 集成测试（design §11.2，L1 隔离实例）。

起隔离实例：DATA_DIR=<tmp> SWEEP_INTERVAL=0 uvicorn app.main:app --port 8011
（SWEEP_INTERVAL=0 使每次 /api/agents/status 都真正清扫，便于在秒级验证完整生命周期；
 生产默认 60s 节流，见 config.py。数据目录完全隔离，绝不触碰生产 server/data。）

链路：loop.py init（register + session）→ 心跳 pull → 模拟失联 1300s → 置 offline
      → 同名重注册唤醒 → 模拟超保留期 23000s → 删除 + 删 read set → @all 在线分母回归。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_LOOP = os.path.join(os.path.dirname(SERVER_DIR), "skill", "loop.py")
ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def _pick_port(preferred=8011):
    """优先用指定端口；被占用则退化为空闲端口。"""
    for port in (preferred, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1] if port == 0 else port
        except OSError:
            pass
        finally:
            s.close()
    raise RuntimeError("no free port")


@pytest.fixture(scope="module")
def isolated(tmp_path_factory):
    """起隔离实例；返回 (base_url, data_dir)。"""
    data_dir = str(tmp_path_factory.mktemp("presence_e2e_data"))
    port = _pick_port(8011)
    base = "http://127.0.0.1:{0}".format(port)
    env = {**os.environ, "DATA_DIR": data_dir, "SWEEP_INTERVAL": "0"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=SERVER_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if _req(base, "GET", "/health")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        else:
            raise RuntimeError("isolated instance failed to start")
        yield base, data_dir
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _req(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _load_agents(data_dir):
    p = os.path.join(data_dir, "agents.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _set_last_seen(data_dir, name, ago):
    agents = _load_agents(data_dir)
    ts = time.strftime(ISO_FMT, time.localtime(time.time() - ago))
    for a in agents:
        if a.get("name") == name:
            a["last_seen"] = ts
    p = os.path.join(data_dir, "agents.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def _status_of(base, name):
    _, d = _req(base, "GET", "/api/agents/status")
    return next((a for a in d["agents"] if a["name"] == name), None)


def test_presence_lifecycle(isolated):
    base, data_dir = isolated
    agents_path = os.path.join(data_dir, "agents.json")

    # ---- 1. loop.py init 链路（register + session active=true）→ presence=online ----
    env = {**os.environ, "AGENT_HUB_URL": base, "AGENT_HUB_DATA_DIR": data_dir}
    r = subprocess.run(
        [sys.executable, SKILL_LOOP, "init", "--name", "TestAgent"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert r.returncode == 0, "loop.py init failed: " + r.stderr[-500:]
    ta = _status_of(base, "TestAgent")
    assert ta is not None and ta["presence"] == "online", ta

    # ---- 2. 心跳 pull（发消息再 pull；顺带创建 agent_read_TestAgent.json）----
    s, d = _req(base, "POST", "/api/messages/send",
                {"sender_type": "user", "content": "hello", "target_type": "single",
                 "target_agent_name": "TestAgent"})
    assert s == 200
    mid = d["message_id"]
    s, d = _req(base, "GET", "/api/messages/pull?agent_name=TestAgent")
    assert any(m["id"] == mid for m in d["messages"])
    read_file = os.path.join(data_dir, "agent_read_TestAgent.json")
    assert os.path.isfile(read_file), "read set file should exist after pull"

    # ---- 3. 模拟失联 1300s（>LOST_TIMEOUT）→ status 触发清扫 → 置 offline ----
    _set_last_seen(data_dir, "TestAgent", 1300)
    ta = _status_of(base, "TestAgent")
    assert ta is not None and ta["presence"] == "offline", ta
    rec = next(a for a in _load_agents(data_dir) if a["name"] == "TestAgent")
    assert rec["status"] == "offline"
    assert rec["session"] is False

    # ---- 4. 同名重注册 → 唤醒（reactivated=true），列表无重复 ----
    s, d = _req(base, "POST", "/api/agents/register", {"name": "TestAgent"})
    assert s == 200 and d.get("already_exists") is True and d.get("reactivated") is True, d
    s, d = _req(base, "GET", "/api/agents?all=true")
    assert d["agents"].count("TestAgent") == 1, d["agents"]
    ta = _status_of(base, "TestAgent")
    assert ta is not None and ta["presence"] == "online", ta

    # ---- 5. 模拟超保留期 23000s（>6h20min）→ status → 删除 + 删 read set ----
    _set_last_seen(data_dir, "TestAgent", 23000)
    s, d = _req(base, "GET", "/api/agents/status")
    assert all(a["name"] != "TestAgent" for a in d["agents"])
    assert all(a["name"] != "TestAgent" for a in _load_agents(data_dir))
    assert not os.path.isfile(read_file), "agent_read_TestAgent.json should be deleted"
    # 历史消息保留
    s, d = _req(base, "GET", "/api/messages/history")
    assert any(m["id"] == mid for m in d["messages"]), "messages.json must be retained"
    # 向已删除名字发 single → 400（AC-3.3）
    s, d = _req(base, "POST", "/api/messages/send",
                {"sender_type": "user", "content": "x", "target_type": "single",
                 "target_agent_name": "TestAgent"})
    assert s == 400

    # ---- 6. /api/agents 默认仅在线（R3 分母语义）----
    for n in ("Online1", "Online2", "Offline1"):
        s, d = _req(base, "POST", "/api/agents/register", {"name": n})
        assert s == 200
    _set_last_seen(data_dir, "Offline1", 1300)   # 让 Offline1 失联
    _req(base, "GET", "/api/agents/status")       # 触发清扫：Offline1 → offline
    s, d = _req(base, "GET", "/api/agents")
    assert set(d["agents"]) == {"Online1", "Online2"}, d["agents"]
    # agents.json 仍保留 Offline1（保留期内不删），仅列表/分母剔除
    names_in_file = {a["name"] for a in _load_agents(data_dir)}
    assert names_in_file == {"Online1", "Online2", "Offline1"}
