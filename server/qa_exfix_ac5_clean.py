# -*- coding: utf-8 -*-
"""QA AC-5 干净版：全新 agent 名 + 唯一 client_msg_id，打印实际 pull 内容。"""
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


suffix = str(int(time.time() * 1000))[-6:]

# ---- AC-5.1：3 新 agent 并发首拉 post-reg batch ----
print("===== AC-5.1 clean =====")
names = ["[TEST-DATA] QA5A_{}".format(suffix), "[TEST-DATA] QA5B_{}".format(suffix), "[TEST-DATA] QA5C_{}".format(suffix)]
for nm in names:
    c, b = req("POST", "/api/agents/register", {"name": nm})
    assert c == 200, (nm, b)
time.sleep(1.2)  # 确保 reg 秒与后续 batch 秒错开（created_at 严格大于 registered_at）
batch_ids = []
for i in range(5):
    c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] c5batch_{}_{:d}".format(suffix, i), "target_type": "all", "client_msg_id": "qa_c5b_{}_{:d}".format(suffix, i)})
    batch_ids.append(b["message_id"])
time.sleep(0.3)

def first_pull(nm):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote(nm)))

for nm in names:
    c, pull = first_pull(nm)
    msgs = pull.get("messages", [])
    ids = [m["id"] for m in msgs]
    got_batch = [i for i in batch_ids if i in ids]
    print("  {} pulled contents:".format(nm), [m["content"] for m in msgs])
    check("AC-5.1 {} gets 5 c5batch exactly once".format(nm),
          len(got_batch) == 5 and len(ids) == 5,
          "pulled={} unique={} batch_hit={}".format(len(ids), len(set(ids)), len(got_batch)))

# ---- AC-5.1b：同 agent 并发首拉无 dup ----
print("===== AC-5.1b clean =====")
races = "[TEST-DATA] QAR_{}".format(suffix)
req("POST", "/api/agents/register", {"name": races})
time.sleep(1.2)
batch_ids2 = []
for i in range(5):
    c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] c5race_{}_{:d}".format(suffix, i), "target_type": "all", "client_msg_id": "qa_c5r_{}_{:d}".format(suffix, i)})
    batch_ids2.append(b["message_id"])
time.sleep(0.3)

def same_pull(i):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote(races)))

results = threaded(6, same_pull)
delivered = []
for code, pull in results:
    delivered.extend(m["id"] for m in pull.get("messages", []))
unique = set(delivered)
print("  delivered ids:", delivered)
check("AC-5.1b same-agent concurrent first pull no dup delivery",
      len(unique) == 5 and len(delivered) == 5 and len(set(batch_ids2) & unique) == 5,
      "unique_delivered={} total_delivered={} batch_hit={}".format(len(unique), len(delivered), len(set(batch_ids2) & unique)))

print("\n==== QA AC-5 clean SUMMARY: {} passed, {} failed ====".format(PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
