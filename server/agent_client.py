# -*- coding: utf-8 -*-
"""演示用 Agent 客户端（纯标准库，忠实 A 协议）。

流程（对应方案书 §2.1 核心价值交换链路）：
  1. register 注册
  2. 每 3 秒 GET /api/messages/pull?agent_name= 拉取@自己且未读的消息（拉取即标记已读）
  3. 本地规则化生成回复（≤100 字）
  4. POST /api/messages/reply 提交回复（响应会捎带剩余未读）

仅用于本地演示"群聊 + 已读回执"效果；真实 Agent（如 openclaw）按同一协议接入即可。
"""
import json
import time
import urllib.request
import urllib.error

SERVER = "http://localhost:8000"
NAME = "DemoAgent"
POLL = 3


def req(method, path, body=None, timeout=10):
    url = SERVER + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def make_reply(text):
    # 规则化回复：复述+截断到 ≤100 字（对齐 B 的 ≤100 字约定）
    prefix = f"（{NAME} 已收到）"
    reply = prefix + text
    return reply[:100]


def main():
    try:
        req("POST", "/api/agents/register", {"name": NAME})
        print(f"[{NAME}] registered @ {SERVER}")
    except urllib.error.URLError as e:
        print(f"[{NAME}] 无法连接服务器 {SERVER}：{e}")
        return

    seen = set()
    while True:
        try:
            snap = req("GET", f"/api/messages/pull?agent_name={NAME}")
            for m in snap.get("messages", []):
                if m["id"] in seen:
                    continue
                seen.add(m["id"])
                reply = make_reply(m.get("content", ""))
                req("POST", "/api/messages/reply", {
                    "agent_name": NAME,
                    "content": reply,
                    "reply_to_message_id": m["id"],
                    "client_msg_id": "c_" + m["id"],  # 幂等去重
                })
                print(f"[{NAME}] 回复 {m['id']}: {reply}")
        except Exception as e:
            print(f"[{NAME}] 轮询异常: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
