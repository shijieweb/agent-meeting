# -*- coding: utf-8 -*-
import json, subprocess
BASE = "http://localhost:8000"
def api(path):
    r = subprocess.run(["curl.exe","-s","-m","10",BASE+path], capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout)
d = api("/api/messages/history?limit=200")
msgs = d.get("messages", [])
tagged = [m for m in msgs if "[TEST-DATA by qa_user]" in (m.get("content") or "")]
print("history returned %d msgs; [TEST-DATA by qa_user] in window: %d" % (len(msgs), len(tagged)))
for m in tagged:
    print("  - id=%s sender=%s target=%s/%s :: %r" % (
        m.get("id"), m.get("sender_type"), m.get("target_type"), m.get("target_agent_name"),
        (m.get("content") or "")[:46]))
# my 4 signatures this round
mine = ["EXT3 多行", "EXT3 line 1", "F2 default user", "F11 长回复"]
print("\nMY round-3 qa_user messages present:", sum(1 for m in tagged if any(k in (m.get("content") or "") for k in mine)))
