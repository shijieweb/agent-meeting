# -*- coding: utf-8 -*-
"""Agent Hub 实弹冒烟测试：覆盖方案书关键链路 + 已读回执 + 幂等 + @all。"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
PASS, FAIL = 0, 0


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


print("== 1. 注册 Agent ==")
s, d = req("POST", "/api/agents/register", {"name": "AgentX"})
check("register AgentX", s == 200 and d.get("status") == "ok", str(d))
s, d = req("POST", "/api/agents/register", {"name": "AgentY"})
check("register AgentY", s == 200)
s, d = req("GET", "/api/agents")
check("agents 列表含 X/Y", set(["AgentX", "AgentY"]).issubset(set(d["agents"])), str(d["agents"]))

print("== 2. 发送单点消息 + 拉取 + 已读 ==")
s, d = req("POST", "/api/messages/send",
           {"sender_type": "user", "content": "你好AgentX", "target_type": "single", "target_agent_name": "AgentX"})
check("send single", s == 200 and "message_id" in d, str(d))
mid = d["message_id"]

s, d = req("GET", "/api/messages/pull?agent_name=AgentX")
check("pull 拿到未读", s == 200 and len(d["messages"]) == 1 and d["messages"][0]["id"] == mid, str([m["id"] for m in d["messages"]]))

s, d = req("GET", "/api/messages/pull?agent_name=AgentX")
check("再次 pull 为空（已读不重复）", s == 200 and d["messages"] == [], str(d["messages"]))

s, d = req("GET", "/api/messages/history")
hist = [m for m in d["messages"] if m["id"] == mid][0]
check("history 单点消息 read_by 含 AgentX（已读）", "AgentX" in hist["read_by"], str(hist.get("read_by")))

print("== 3. 回复 + 捎带 ==")
s, d = req("POST", "/api/messages/reply",
           {"agent_name": "AgentX", "content": "收到", "reply_to_message_id": mid})
check("reply 成功", s == 200 and d.get("status") == "ok", str(d))

print("== 4. 幂等（同 client_msg_id 不重复）==")
s, d1 = req("POST", "/api/messages/send",
            {"content": "幂等测试", "target_type": "single", "target_agent_name": "AgentX", "client_msg_id": "dup-1"})
s, d2 = req("POST", "/api/messages/send",
            {"content": "幂等测试", "target_type": "single", "target_agent_name": "AgentX", "client_msg_id": "dup-1"})
check("幂等：两次 client_msg_id 相同返回同一 message_id", d1.get("message_id") == d2.get("message_id"), f"{d1.get('message_id')} vs {d2.get('message_id')}")

print("== 5. @all 广播 + 多方已读 ==")
s, d = req("POST", "/api/messages/send",
           {"content": "大家好", "target_type": "all"})
check("send all", s == 200)
all_id = d["message_id"]
req("GET", "/api/messages/pull?agent_name=AgentX")  # X 读
req("GET", "/api/messages/pull?agent_name=AgentY")  # Y 读
s, d = req("GET", "/api/messages/history")
ah = [m for m in d["messages"] if m["id"] == all_id][0]
# 注：后台活 DemoAgent 也会读 @all，故只断言本测试可控的 X/Y 已读（子集而非精确相等）
check("@all 广播被 X/Y 已读（活 DemoAgent 也读属正常）", {"AgentX", "AgentY"}.issubset(set(ah["read_by"])), str(ah.get("read_by")))

print("== 6. 验证持久化（文件落盘）==")
import os
data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
check("messages.json 存在且有内容", os.path.isfile(os.path.join(data_dir, "messages.json")) and os.path.getsize(os.path.join(data_dir, "messages.json")) > 0)

print("== 7. 边界校验（§7 T-SEND-03 / T-REPLY-04 / T-REG-02）==")
# T-SEND-03：发给不存在的 Agent → 400 且不保存
s, d = req("POST", "/api/messages/send",
           {"sender_type": "user", "content": "hi", "target_type": "single", "target_agent_name": "ghost_not_exist"})
check("T-SEND-03 目标不存在返回400", s == 400, str(s))
s, d = req("GET", "/api/messages/history")
ghost_msgs = [m for m in d["messages"] if m.get("target_agent_name") == "ghost_not_exist"]
check("T-SEND-03 未保存 ghost 消息", len(ghost_msgs) == 0, f"ghost_msgs={len(ghost_msgs)}")
# T-REPLY-04：未注册 Agent 回复 → 400
s, d = req("POST", "/api/messages/reply", {"agent_name": "ghost2", "content": "x"})
check("T-REPLY-04 未注册 Agent 回复返回400", s == 400, str(s))
# T-REG-02：重注册 → 提示已存在
req("POST", "/api/agents/register", {"name": "EdgeProbe"})
s, d = req("POST", "/api/agents/register", {"name": "EdgeProbe"})
check("T-REG-02 重注册提示已存在", s == 200 and d.get("already_exists") is True, str(d))

print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
