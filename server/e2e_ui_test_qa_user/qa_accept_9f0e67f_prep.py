# -*- coding: utf-8 -*-
"""Pre-populate the ISOLATED data dir D:/tmp/am-qa-user for qa_user acceptance (commit 9f0e67f).

Writes 45 historical agent messages (so #message-list is scrollable for IME + scroll-to-top
tests), plus empty reads.json and empty agents.json. The 8022 server reads these on first
request; atomic writes in storage.py will not clobber already-present files.
"""
import os, json, time

DATA = "D:/tmp/am-qa-user"
os.makedirs(DATA, exist_ok=True)

base = time.time() - 3600  # 1 hour ago; strictly increasing per message
msgs = []
for i in range(1, 46):
    t = time.localtime(base + i)
    created = time.strftime("%Y-%m-%dT%H:%M:%S", t)
    msgs.append({
        "id": "msg_{0:04d}".format(i),
        "content": "历史消息 #{0:03d}".format(i),
        "sender_type": "agent",
        "sender_agent_name": "HistoryBot",
        "target_type": "all",
        "target_agent_name": None,
        "created_at": created,
        "client_msg_id": None,
        "read_by": [],
    })

with open(os.path.join(DATA, "messages.json"), "w", encoding="utf-8") as f:
    json.dump(msgs, f, ensure_ascii=False, indent=2)
with open(os.path.join(DATA, "reads.json"), "w", encoding="utf-8") as f:
    json.dump([], f, ensure_ascii=False, indent=2)
with open(os.path.join(DATA, "agents.json"), "w", encoding="utf-8") as f:
    json.dump([], f, ensure_ascii=False, indent=2)

print("prep done: messages={0} dir={1}".format(len(msgs), DATA))
