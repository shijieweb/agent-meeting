# -*- coding: utf-8 -*-
"""QA 修正版 AC-5 验证：先注册新 agent，再发 batch @all（post-reg），并发首拉。
修正点：原脚本把 batch 发在注册前 → 被 F4 正确过滤（pulled=0 反证 F4 生效），
但无法验证 AC-5.1「并发首拉各恰一次」。此处按 eng 时序：注册 → 发 batch → 并发首拉。
"""
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:8028"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_data_qa_agent_exfix")
PASS = 0
FAIL = 0


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("[PASS] {} | {}".format(name, detail))
    else:
        FAIL += 1
        print("[FAIL] {} | {}".format(name, detail))


def threaded(n, fn):
    results = [None] * n
    def worker(i):
        results[i] = fn(i)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


# ---- AC-5.1：3 新 agent 并发首拉 post-reg batch，各恰 5 条 ----
print("===== AC-5.1（修正时序：注册→发batch→并发首拉） =====")
names = ["[TEST-DATA] New6", "[TEST-DATA] New7", "[TEST-DATA] New8"]
for nm in names:
    c, b = req("POST", "/api/agents/register", {"name": nm})
    assert c == 200, nm
time.sleep(0.3)
# 发 5 条 post-reg batch @all
batch_ids = []
for i in range(5):
    c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] postbatch {:d}".format(i), "target_type": "all", "client_msg_id": "qa_pbatch_{:d}".format(i)})
    batch_ids.append(b["message_id"])
time.sleep(0.3)

def first_pull(nm):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote(nm)))

for nm in names:
    c, pull = first_pull(nm)
    ids = [m["id"] for m in pull.get("messages", [])]
    got_batch = [i for i in batch_ids if i in ids]
    check("AC-5.1 {} gets 5 postbatch exactly once".format(nm),
          len(got_batch) == 5 and len(ids) == 5,
          "pulled={} unique={} batch_hit={}".format(len(ids), len(set(ids)), len(got_batch)))

# ---- AC-5.1b：同 agent 并发首拉无 dup（全局 5 条只投一次） ----
print("===== AC-5.1b（同 agent 并发首拉） =====")
req("POST", "/api/agents/register", {"name": "[TEST-DATA] Race2"})
time.sleep(0.3)
batch_ids2 = []
for i in range(5):
    c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] racebatch {:d}".format(i), "target_type": "all", "client_msg_id": "qa_rbatch_{:d}".format(i)})
    batch_ids2.append(b["message_id"])
time.sleep(0.3)

def same_pull(i):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] Race2")))

results = threaded(6, same_pull)
delivered = []
for code, pull in results:
    delivered.extend(m["id"] for m in pull.get("messages", []))
unique = set(delivered)
check("AC-5.1b same-agent concurrent first pull no dup delivery",
      len(unique) == 5 and len(delivered) == 5,
      "unique_delivered={} total_delivered={} batch_hit={}".format(len(unique), len(delivered), len(set(batch_ids2) & unique)))

print("\n==== QA AC-5 fix SUMMARY: {} passed, {} failed ====".format(PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
