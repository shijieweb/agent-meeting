# -*- coding: utf-8 -*-
"""Prep ISOLATED data dir D:/tmp/am-qa-user for qa_user re-verify (current HEAD d3d99fb).

20 agent-only historical messages (NO user messages) so:
  - chat list has content for scroll/presence visibility,
  - readStatusNodes stays empty -> AC-10.1 (empty chat skips read-receipt poll) holds.
agents.json / reads.json start empty (agents registered via API during the test).
"""
import os, json, time

DATA = "D:/tmp/am-qa-user"
os.makedirs(DATA, exist_ok=True)

base = time.time() - 3600
msgs = []
for i in range(1, 21):
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
with open(os.path.join(DATA, "agents.json"), "w", encoding="utf-8") as f:
    json.dump([], f, ensure_ascii=False, indent=2)
with open(os.path.join(DATA, "reads.json"), "w", encoding="utf-8") as f:
    json.dump([], f, ensure_ascii=False, indent=2)

print("prep done: agent_messages={0} dir={1}".format(len(msgs), DATA))
