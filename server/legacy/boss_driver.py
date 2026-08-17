# -*- coding: utf-8 -*-
"""老板自动驱动器（事件驱动 + 异常保护 + 唯一应答标记防重复）。
检测到各阶段完成关键词再推进相位。
"""
import requests
import time
import hashlib

BASE = "http://localhost:5000"
ROOM = "meeting"
BOSS = "boss"


def _post(url, payload, n=8):
    for i in range(n):
        try:
            r = requests.post(url, json=payload, timeout=5)
            if r.ok:
                return r.json()
        except Exception:
            pass
        time.sleep(0.8)
    return None


def _get(url, n=8):
    for i in range(n):
        try:
            return requests.get(url, timeout=5).json()
        except Exception:
            pass
        time.sleep(0.8)
    return {"seq": 0, "messages": []}


def join():
    for _ in range(30):
        r = _post(f"{BASE}/api/room/{ROOM}/join", {"uid": BOSS, "name": "老板"})
        if r and r.get("ok"):
            return r
        time.sleep(1)
    raise SystemExit("boss join failed")


def send(content):
    _post(f"{BASE}/api/room/{ROOM}/message", {"uid": BOSS, "type": "text", "content": content})


def messages(since):
    return _get(f"{BASE}/api/room/{ROOM}/messages?since={since}")


def main():
    seq = join().get("seq", 0)
    print(f"boss joined, initial seq={seq}")

    # 发 /开始提问 只有在 waiting 阶段才发
    data = messages(0)
    phase = _current_phase(data)
    if phase == "waiting":
        send("/开始提问 我们要做一个全自动 AI 短剧生成工具，请评估技术方案与落地路径")
        print(">> /开始提问")
        phase = "asking"

    last = data["seq"]
    answered = set()          # 已应答的 seq_id
    plan_done = 0
    t0 = time.time()

    while time.time() - t0 < 600:
        data = messages(last)
        for m in data["messages"]:
            c = m.get("content") or ""
            frm = m.get("from", {})
            frm_uid = frm.get("uid")
            frm_name = frm.get("name", frm_uid)
            msg_id = m.get("id")   # 服务器消息唯一ID

            # 用服务器消息ID防重复应答（最可靠）
            key = msg_id
            if not key:
                key = f"{m['seq']}-{frm_uid}"

            if phase == "asking" and frm_uid != BOSS:
                # 必须 @boss 才回答（boss 发的不回答）
                if "@boss" not in c:
                    continue
                if key in answered:
                    continue
                answered.add(key)
                ans = f"@{frm_name} 背景约束：优先云端推理保证画质、成本可控、2周内出最小可用版本"
                send(ans)
                print(f"  A> {ans[:60]}")

                if "问完了" in c or "提问完毕" in c:
                    send("/出方案")
                    print(">> /出方案")
                    phase = "planning"
                    plan_done = 0

            elif phase == "planning":
                if frm_uid != BOSS and "方案完成" in c:
                    plan_done += 1
                    print(f"  plan_done={plan_done} from {frm_name}")
                if plan_done >= 2:
                    send("/互相评审")
                    print(">> /互相评审")
                    phase = "reviewing"

            elif phase == "reviewing":
                if "评审完毕" in c:
                    send("@Agent-A /合方案")
                    print(">> @Agent-A /合方案")
                    phase = "merging"

            elif phase == "merging":
                if frm_uid != BOSS and "@boss" in c and "合并" in c:
                    send("/结束会议")
                    print(">> /结束会议")
                    print("boss driver done.")
                    return

        last = data["seq"]
        time.sleep(1)

    print("boss driver: timeout after 600s")


def _current_phase(data):
    """从消息历史推断当前 phase。"""
    for m in reversed(data.get("messages", [])):
        c = m.get("content") or ""
        if c.startswith("/结束会议"):
            return "done"
        if c.startswith("/互相评审"):
            return "reviewing"
        if c.startswith("/出方案"):
            return "planning"
        if c.startswith("/开始提问"):
            return "asking"
    return "waiting"


if __name__ == "__main__":
    main()
