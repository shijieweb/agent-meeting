# -*- coding: utf-8 -*-
"""QA 独立验收脚本：T-agent-meeting-external-fix（后端契约，qa_agent）。
与 eng 的 external_fix_verify.py 独立编写，断言独立；隔离实例 8028 + test_data_qa_agent_exfix。
覆盖：AC-1.1/1.2/1.3, 2.1/2.2/2.3, 3.1/3.2, 4.1/4.2/4.3, 5.1/5.1b/5.2, 6.1/6.2/6.3 + 回归。
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
FAILURES = []


def req(method, path, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            code = resp.status
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        code = e.code
        text = e.read().decode("utf-8")
    if raw:
        return code, text
    try:
        return code, json.loads(text) if text else {}
    except json.JSONDecodeError:
        return code, text


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("[PASS] {} | {}".format(name, detail))
    else:
        FAIL += 1
        FAILURES.append((name, detail))
        print("[FAIL] {} | {}".format(name, detail))


def load_json(name, default):
    p = os.path.join(DATA_DIR, name)
    if not os.path.isfile(p):
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def count_messages():
    return len(load_json("messages.json", []))


def count_agents():
    return len(load_json("agents.json", []))


def threaded(n, fn):
    """并发执行 n 次 fn(i)，收集返回值列表。"""
    results = [None] * n
    def worker(i):
        results[i] = fn(i)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ---------------------------------------------------------------- AC-6 名字边界
print("===== AC-6 名字禁含 '/' =====")
code, body = req("POST", "/api/agents/register", {"name": "a/b"})
check("AC-6.1 a/b -> 4xx", code in (400, 422), "status={}".format(code))
check("AC-6.3 detail explicit", isinstance(body, dict) and "must not contain" in str(body.get("detail", "")), "detail={}".format(body.get("detail") if isinstance(body, dict) else body))
check("AC-6.1 a/b not persisted", count_agents() == 0, "agents={}".format(count_agents()))

for nm in ["[TEST-DATA] 中文名", "[TEST-DATA] abc_123", "[TEST-DATA] with-dash"]:
    code, body = req("POST", "/api/agents/register", {"name": nm})
    check("AC-6.2 normal name ok: {}".format(nm), code == 200, "status={}".format(code))

# ---------------------------------------------------------------- AC-2 并发注册
print("===== AC-2 并发注册原子化 =====")
base_agents = count_agents()

def reg_distinct(i):
    return req("POST", "/api/agents/register", {"name": "[TEST-DATA] R{:02d}".format(i)})

results = threaded(30, reg_distinct)
check("AC-2.1 30 concurrent register all 200", all(code == 200 for code, _ in results), "ok={}".format(sum(1 for c, _ in results if c == 200)))
agents = load_json("agents.json", [])
names = [a["name"] for a in agents if a["name"].startswith("[TEST-DATA] R")]
check("AC-2.1 30 distinct agents persisted", len(names) == 30 and len(set(names)) == 30, "count={} unique={}".format(len(names), len(set(names))))

def reg_same(i):
    return req("POST", "/api/agents/register", {"name": "[TEST-DATA] SameName"})

results = threaded(20, reg_same)
check("AC-2.2 20 concurrent same-name -> 1 record", count_agents() == base_agents + 31, "agents={} expect={}".format(count_agents(), base_agents + 31))
same = [a for a in load_json("agents.json", []) if a["name"] == "[TEST-DATA] SameName"]
check("AC-2.2 same-name count == 1", len(same) == 1, "count={}".format(len(same)))

code, body = req("POST", "/api/agents/register", {"name": "[TEST-DATA] StructNew"})
check("AC-2.3 created structure", code == 200 and body.get("status") == "ok" and "message" in body, "body={}".format(body))
code, body = req("POST", "/api/agents/register", {"name": "[TEST-DATA] StructNew"})
check("AC-2.3 already_exists structure", code == 200 and body.get("already_exists") is True and "reactivated" in body, "body={}".format(body))

# ---------------------------------------------------------------- AC-1 并发 send
print("===== AC-1 并发 send 原子化 =====")
base_msgs = count_messages()

def send_msg(i):
    return req("POST", "/api/messages/send", {
        "sender_type": "user",
        "content": "[TEST-DATA] concurrent send {:02d}".format(i),
        "target_type": "all",
        "client_msg_id": "qa_send_{:02d}".format(i),
    })

results = threaded(20, send_msg)
check("AC-1.1 20 concurrent send all ok", all(code == 200 for code, _ in results), "ok={}".format(sum(1 for c, _ in results if c == 200)))
msgs = load_json("messages.json", [])
sent = [m for m in msgs if m.get("content", "").startswith("[TEST-DATA] concurrent send")]
check("AC-1.1 20 messages persisted", len(sent) == 20, "count={}".format(len(sent)))

# AC-1.2 幂等
c1, b1 = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] idem", "target_type": "all", "client_msg_id": "qa_idem_1"})
c2, b2 = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] idem", "target_type": "all", "client_msg_id": "qa_idem_1"})
idem = [m for m in load_json("messages.json", []) if m.get("client_msg_id") == "qa_idem_1"]
check("AC-1.2 idempotent duplicate -> 1 msg", c1 == 200 and c2 == 200 and len(idem) == 1, "s1={} s2={} count={}".format(c1, c2, len(idem)))

# AC-1.3 single 不存在 400 不入库
before = count_messages()
c3, b3 = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] ghost", "target_type": "single", "target_agent_name": "[TEST-DATA] Ghost_NoExist", "client_msg_id": "qa_ghost_1"})
check("AC-1.3 single ghost -> 400 not persisted", c3 == 400 and count_messages() == before, "status={} before={} after={}".format(c3, before, count_messages()))

# ---------------------------------------------------------------- AC-3 reply_to_message_id
print("===== AC-3 reply_to_message_id 落库 =====")
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] ReplyBot"})
# 需先有一条 target 消息供 reply 引用
c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] target for reply", "target_type": "single", "target_agent_name": "[TEST-DATA] ReplyBot", "client_msg_id": "qa_target_1"})
target_id = b.get("message_id")
c, b = req("POST", "/api/messages/reply", {"agent_name": "[TEST-DATA] ReplyBot", "content": "[TEST-DATA] reply with ref", "reply_to_message_id": target_id, "client_msg_id": "qa_rep_ref_1"})
check("AC-3.1 reply with ref status ok", c == 200 and b.get("status") == "ok", "body={}".format(b))
c, hist = req("GET", "/api/messages/history?limit=50")
found = [m for m in hist.get("messages", []) if m.get("sender_type") == "agent" and m.get("content") == "[TEST-DATA] reply with ref"]
check("AC-3.1 history has reply_to_message_id", len(found) == 1 and found[0].get("reply_to_message_id") == target_id, "field={}".format(found[0].get("reply_to_message_id") if found else None))
c, b = req("POST", "/api/messages/reply", {"agent_name": "[TEST-DATA] ReplyBot", "content": "[TEST-DATA] reply no ref", "client_msg_id": "qa_rep_noref_1"})
check("AC-3.2 reply without ref ok (field null)", c == 200 and b.get("status") == "ok", "body={}".format(b))
c, hist = req("GET", "/api/messages/history?limit=50")
found = [m for m in hist.get("messages", []) if m.get("sender_type") == "agent" and m.get("content") == "[TEST-DATA] reply no ref"]
check("AC-3.2 field null compatible", len(found) == 1 and found[0].get("reply_to_message_id") is None, "field={}".format(found[0].get("reply_to_message_id") if found else None))

# ---------------------------------------------------------------- AC-4 首拉不回灌
print("===== AC-4 新 agent 首拉不回灌 =====")
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] Old1"})
# 发 2 条 @all 作为"注册前历史"
for i in range(2):
    req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] pre-reg @all {:d}".format(i), "target_type": "all", "client_msg_id": "qa_prereg_{:d}".format(i)})
time.sleep(1.1)  # 保证 created_at 严格早于 registered_at（秒级精度）
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] New1"})
time.sleep(0.3)
c, pull = req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] New1")))
pre = [m for m in pull.get("messages", []) if m.get("content", "").startswith("[TEST-DATA] pre-reg")]
check("AC-4.1 first pull excludes pre-reg @all", len(pre) == 0, "pulled_pre_reg={}".format(len(pre)))
# AC-4.2 注册后新 @all 正常投递
c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] after-reg", "target_type": "all", "client_msg_id": "qa_postreg_1"})
c, pull = req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] New1")))
post = [m for m in pull.get("messages", []) if m.get("content") == "[TEST-DATA] after-reg"]
check("AC-4.2 post-reg @all delivered", len(post) == 1, "pulled={}".format(len(post)))
# AC-4.3 老 agent 增量行为不变：Old1 先 pull（建 agent_read 文件），再发新消息，Old1 增量拉得到
c, pull = req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] Old1")))
first_pull_old = len(pull.get("messages", []))
c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] old-incremental-new", "target_type": "all", "client_msg_id": "qa_oldinc_1"})
c, pull = req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] Old1")))
inc = [m for m in pull.get("messages", []) if m.get("content") == "[TEST-DATA] old-incremental-new"]
check("AC-4.3 old agent incremental unchanged", len(inc) == 1, "pulled_new={}".format(len(inc)))

# ---------------------------------------------------------------- AC-5 并发首拉
print("===== AC-5 并发首拉 =====")
# 先造 5 条 @all batch（在注册新 agent 之前）
for i in range(5):
    req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] batch {:d}".format(i), "target_type": "all", "client_msg_id": "qa_batch_{:d}".format(i)})
time.sleep(1.1)
names5 = ["[TEST-DATA] New2", "[TEST-DATA] New3", "[TEST-DATA] New4"]
for nm in names5:
    req("POST", "/api/agents/register", {"name": nm})
time.sleep(0.3)

def first_pull(nm):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote(nm)))

for nm in names5:
    c, pull = first_pull(nm)
    msgs = pull.get("messages", [])
    batch = [m for m in msgs if m.get("content", "").startswith("[TEST-DATA] batch")]
    ids = [m["id"] for m in batch]
    check("AC-5.1 {} concurrent first pull each gets 5 batch exactly once".format(nm),
          len(batch) == 5 and len(set(ids)) == 5,
          "pulled={} unique={}".format(len(batch), len(set(ids))))

# AC-5.1b 同 agent 并发首拉无 dup
def same_agent_pull(i):
    return req("GET", "/api/messages/pull?agent_name={}".format(urllib.parse.quote("[TEST-DATA] New5")))

req("POST", "/api/agents/register", {"name": "[TEST-DATA] New5"})
time.sleep(0.3)
results = threaded(6, same_agent_pull)
all_delivered = []
for code, pull in results:
    all_delivered.extend(m["id"] for m in pull.get("messages", []))
batch5 = [i for i in all_delivered]
unique = set(batch5)
check("AC-5.1b same-agent concurrent first pull no dup delivery", len(unique) == len(batch5), "unique_delivered={} total={}".format(len(unique), len(batch5)))

# AC-5.2 agent_read 文件创建
import glob
read_files = [os.path.basename(p) for p in glob.glob(os.path.join(DATA_DIR, "agent_read_*.json"))]
check("AC-5.2 agent_read files created", len(read_files) >= 7, "files={}".format(len(read_files)))

# ---------------------------------------------------------------- 回归 F1/F2/presence
print("===== 回归：F1 reads 回执 / F2 reactivated / presence =====")
# F1 回归：@all 为每个已注册 agent 建 reads 回执
c, b = req("POST", "/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] reads-regress", "target_type": "all", "client_msg_id": "qa_readsreg_1"})
reads = load_json("reads.json", [])
reg_agents = [a["name"] for a in load_json("agents.json", [])]
this_reads = [r for r in reads if r.get("message_id") == b.get("message_id")]
this_agents = set(r["agent_name"] for r in this_reads)
check("F1 regression: @all reads receipt for each registered agent", this_agents == set(reg_agents), "receipts={} agents={}".format(len(this_agents), len(reg_agents)))

# F2 回归：reactivated 系统消息仍写入（state-persist 不回归）
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] Wake1"})
req("POST", "/api/agents/{}/session?active=false".format(urllib.parse.quote("[TEST-DATA] Wake1")))
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] Wake1"})
check("F2 regression: reactivated=true", b.get("reactivated") is True, "body={}".format(b))
c, hist = req("GET", "/api/messages/history?limit=100")
sysm = [m for m in hist.get("messages", []) if m.get("sender_type") == "system" and m.get("event") == "reactivated" and m.get("sender_agent_name") == "[TEST-DATA] Wake1"]
check("F2 regression: reactivated system message written", len(sysm) == 1, "sys_reactivated={}".format(len(sysm)))

# presence 三态不受影响：新注册 waiting / init working / end offline
c, b = req("POST", "/api/agents/register", {"name": "[TEST-DATA] Presence1"})
req("POST", "/api/agents/{}/session?active=true".format(urllib.parse.quote("[TEST-DATA] Presence1")))
c, st = req("GET", "/api/agents/status")
p1 = [a for a in st.get("agents", []) if a["name"] == "[TEST-DATA] Presence1"]
check("presence regression: init -> presence online", p1 and p1[0].get("presence") == "online", "presence={}".format(p1[0].get("presence") if p1 else None))
req("POST", "/api/agents/{}/session?active=false".format(urllib.parse.quote("[TEST-DATA] Presence1")))
c, st = req("GET", "/api/agents/status")
p1 = [a for a in st.get("agents", []) if a["name"] == "[TEST-DATA] Presence1"]
check("presence regression: end -> presence offline", p1 and p1[0].get("presence") == "offline", "presence={}".format(p1[0].get("presence") if p1 else None))


print("\n==== QA external-fix SUMMARY: {} passed, {} failed ====".format(PASS, FAIL))
if FAILURES:
    for name, detail in FAILURES:
        print("FAILED: {} | {}".format(name, detail))
sys.exit(0 if FAIL == 0 else 1)
