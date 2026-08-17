#!/usr/bin/env python3
# 模拟外部 agent (openclaw) 经 agent_skill.md 的通用 HTTP 协议接入会议系统中转服务。
# 行为（严格照 agent_skill.md）：
#   - 加入房间 -> 每 3 秒轮询(带 uid 心跳) -> 收到 @/直接消息即回复 -> 收到结束会议/phase=done 即停止回复（保留连接）。
#   - 会议被重置回 waiting 后自动恢复回复（让 P1-1 重置功能端到端可用，无需重启 agent）。
#   - 回复必须基于用户消息内容，且单条 ≤ 100 字（含 @提及前缀，超出截断）。
# 说明：本脚本用规则化应答模拟一个接入 agent；真实 openclaw 会用它自己的 LLM 生成回复。
import json, re, time, urllib.request, urllib.error

SERVER = "http://localhost:5000"
ROOM = "meeting"
UID = "agent-openclaw"
NAME = "OpenClaw"  # mention-safe：仅 ASCII，否则 @Name 无法被平台正则解析


def req(method, path, payload=None):
    url = SERVER + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # 成功响应一律视为 ok。GET /messages 不返回 ok 字段，需补 true；
            # POST 错误响应自带 ok:False，保持不变。
            if isinstance(data, dict) and "ok" not in data:
                data["ok"] = True
            return data
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode("utf-8")}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}


def join():
    return req("POST", f"/api/room/{ROOM}/join", {"uid": UID, "name": NAME})


def poll(seq):
    return req("GET", f"/api/room/{ROOM}/messages?since={seq}&uid={UID}")


def send(content, reply_to=None):
    payload = {"uid": UID, "type": "text", "content": content[:100]}
    if reply_to:
        payload["reply_to"] = reply_to
    return req("POST", f"/api/room/{ROOM}/message", payload)


def mentioned_me(msg):
    # 仅当被 @我 或 @所有人 时才回复（阶段1 规则：必须识别@名字，带自己才回）
    mentions = msg.get("mentions") or []
    if "@所有人" in mentions:
        return True
    return UID in mentions or NAME in mentions


def make_reply(msg):
    # 基于用户消息生成回复；硬性 ≤ 100 字（含 @前缀），send() 再兜底截断一次
    text = (msg.get("content") or "").strip()
    # 去掉开头的 @某人 前缀，避免把 "@OpenClaw" 也复述进回复（对话更自然）
    text = re.sub(r'^@[^\s@]+\s*', '', text).strip()
    boss = msg.get("from", {}).get("name", "老板")
    if not text:
        return None
    quoted = text[:24]
    if "?" in text or "？" in text:
        reply = f"@{boss} 收到你的问题：「{quoted}」。我理解为你在确认可行性，补范围/验收我出方案。"
    else:
        reply = f"@{boss} 收到：「{quoted}」。需要我先出哪块初步想法？或发 `#结束会议` 我即停。"
    return reply[:100]


def main():
    print(f"[sim] joining room={ROOM} as {NAME} (uid={UID})")
    j = join()
    members = [m["name"] for m in j.get("members", [])]
    print(f"[sim] join ok={j.get('ok')} members={members}")
    send("@boss 你好，我是接入的 agent（OpenClaw）。从网页下拉框选我或直接 @我 即可聊天；发 `#结束会议` 我即停止。")

    seq = j.get("seq", 0)
    processed = set()
    paused = False
    print("[sim] entering poll loop (3s); 结束会议后停止回复，会议重置后可自动恢复")
    while True:
        try:
            snap = poll(seq)
            if not snap.get("ok"):
                time.sleep(3)
                continue
            seq = snap.get("seq", seq)
            phase = snap.get("phase")
            if phase == "done":
                if not paused:
                    paused = True
                    print("[sim] phase=done，已停止回复（保留连接，等待会议重置恢复）")
                time.sleep(3)
                continue
            if paused:
                # 检测到会议被重置（回到 waiting），恢复回复
                paused = False
                seq = 0
                processed.clear()
                print("[sim] phase=waiting，会议已重置，恢复回复")
                send("@boss 会议已重置，我还在，可以继续聊。")
            for m in snap.get("messages", []):
                s = m.get("seq")
                if s in processed:
                    continue
                processed.add(s)
                mtype = m.get("type")
                if mtype == "system":
                    if "结束" in (m.get("content") or ""):
                        print("[sim] 收到系统停止信号，退出")
                        return
                    continue
                if mtype != "text":
                    continue
                frm = m.get("from", {})
                if frm.get("uid") == UID:
                    continue  # 跳过自己的消息
                if not mentioned_me(m):
                    continue  # 没 @我、且消息里 @了别人 -> 不抢答
                reply = make_reply(m)
                if reply:
                    time.sleep(0.4)
                    send(reply, reply_to=m.get("id"))
                    print(f"[sim] <- {frm.get('name')}: {m.get('content')[:40]}")
                    print(f"[sim] -> {reply[:60].replace(chr(10), ' ')} (len={len(reply)})")
            time.sleep(3)  # 阶段1 固定 3 秒轮询
        except KeyboardInterrupt:
            print("[sim] interrupted by user")
            break
        except Exception as e:  # noqa
            print("[sim] error:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
