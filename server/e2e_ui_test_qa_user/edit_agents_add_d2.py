# -*- coding: utf-8 -*-
"""Add a TEST agent 'qa_d2' in LOST state to PROD 8000 agents.json for D-2 browser test.
Pre-condition: agents_backup_before.json already captured (net-zero: restore_agents.py reverts).
Only adds a clearly-named test agent; does NOT touch real/production agents.
"""
import json, os, datetime

PROD = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/data"
f = os.path.join(PROD, "agents.json")
agents = json.load(open(f, encoding="utf-8"))
# remove any prior qa_d2 to be idempotent
agents = [a for a in agents if a.get("name") != "qa_d2"]
old = (datetime.datetime.now() - datetime.timedelta(seconds=700)).strftime("%Y-%m-%dT%H:%M:%S")
agents.append({
    "name": "qa_d2",
    "registered_at": old,
    "last_seen": old,        # session=true + age>600s -> front-end renders '已掉线·需重唤'
    "status": "working",
    "session": True,
})
json.dump(agents, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("added qa_d2 (lost). current agents:", [a["name"] for a in agents])
