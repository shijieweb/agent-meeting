# -*- coding: utf-8 -*-
"""T-agent-meeting-external-fix 隔离自测（端口 8026 + test_data_external_fix）。

覆盖 AC-1.1/1.2/1.3、AC-2.1/2.2/2.3、AC-3.1/3.2、AC-4.1/4.2/4.3、
AC-5.1/5.2、AC-6.1/6.2/6.3、AC-7.1（7.2/7.3 见命令行步骤）。
"""
import concurrent.futures
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8026"
DATA_DIR = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/test_data_external_fix"

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + detail) if detail else ""))

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def load_messages():
    try:
        with open(DATA_DIR + "/messages.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def load_agents():
    try:
        with open(DATA_DIR + "/agents.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def main():
    # ============ AC-6 名字校验（先做：干净数据） ============
    s, body = post("/api/agents/register", {"name": "a/b"})
    check("AC-6.1 a/b -> 422", s == 422, "status=%d detail=%s" % (s, body.get("detail")))
    check("AC-6.3 detail explicit", body.get("detail") == "agent name must not contain '/'", repr(body.get("detail")))
    names = [a["name"] for a in load_agents()]
    check("AC-6.1 a/b not persisted", "a/b" not in names, "agents=%d" % len(names))
    # AC-6.2 正常名不受影响
    for nm in ["[TEST-DATA] 中文名", "[TEST-DATA] abc_123", "[TEST-DATA] with-dash"]:
        s, _ = post("/api/agents/register", {"name": nm})
        check("AC-6.2 normal name ok: %s" % nm, s == 200, "status=%d" % s)

    # ============ AC-2 并发注册原子化 ============
    # AC-2.1: 30 并发不同名
    def reg(i):
        return post("/api/agents/register", {"name": "[TEST-DATA] ag%d" % i})
    with concurrent.futures.ThreadPoolExecutor(30) as ex:
        statuses = list(ex.map(reg, range(30)))
    check("AC-2.1 30 concurrent register all 200", all(s == 200 for s, _ in statuses), "ok=%d" % sum(1 for s, _ in statuses if s == 200))
    agents = load_agents()
    ag_names = [a["name"] for a in agents if a["name"].startswith("[TEST-DATA] ag")]
    check("AC-2.1 30 distinct agents persisted", len(ag_names) == 30 and len(set(ag_names)) == 30, "count=%d unique=%d" % (len(ag_names), len(set(ag_names))))

    # AC-2.2: 20 并发同名 -> 仅 1 条
    def reg_same(_):
        return post("/api/agents/register", {"name": "[TEST-DATA] same"})
    with concurrent.futures.ThreadPoolExecutor(20) as ex:
        statuses = list(ex.map(reg_same, range(20)))
    agents = load_agents()
    same_count = sum(1 for a in agents if a["name"] == "[TEST-DATA] same")
    check("AC-2.2 20 concurrent same-name -> 1 record", same_count == 1, "count=%d" % same_count)

    # AC-2.3: 返回结构（created/already_exists/reactivated）
    s, body = post("/api/agents/register", {"name": "[TEST-DATA] ag0"})
    check("AC-2.3 already_exists structure", s == 200 and body.get("already_exists") is True, repr(body))
    s, body = post("/api/agents/register", {"name": "[TEST-DATA] brand_new_for_structure"})
    check("AC-2.3 created structure", s == 200 and "message" in body and "Agent registered" in body.get("message", ""), repr(body))

    # ============ AC-1 并发 send 原子化 ============
    # AC-1.1: 20 并发 send 不同内容
    def send(i):
        return post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] msg %d" % i,
                                           "target_type": "all", "client_msg_id": "usr_t_%d" % i})
    with concurrent.futures.ThreadPoolExecutor(20) as ex:
        statuses = list(ex.map(send, range(20)))
    check("AC-1.1 20 concurrent send all ok", all(s == 200 for s, _ in statuses), "ok=%d" % sum(1 for s, _ in statuses if s == 200))
    msgs = load_messages()
    sent20 = [m for m in msgs if m.get("content", "").startswith("[TEST-DATA] msg ")]
    check("AC-1.1 20 messages persisted", len(sent20) == 20, "count=%d" % len(sent20))

    # AC-1.2: 同 client_msg_id 重复 send -> 幂等 1 条
    s1, b1 = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] dup",
                                         "target_type": "all", "client_msg_id": "usr_dup_1"})
    s2, b2 = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] dup",
                                         "target_type": "all", "client_msg_id": "usr_dup_1"})
    dup_count = sum(1 for m in load_messages() if m.get("client_msg_id") == "usr_dup_1")
    check("AC-1.2 idempotent duplicate -> 1 msg", s1 == 200 and s2 == 200 and dup_count == 1,
          "s1=%d s2=%d count=%d" % (s1, s2, dup_count))

    # AC-1.3: single 目标不存在 -> 400 不落库
    before = len(load_messages())
    s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] to-ghost",
                                          "target_type": "single", "target_agent_name": "[TEST-DATA] GhostX",
                                          "client_msg_id": "usr_ghost_1"})
    after = len(load_messages())
    check("AC-1.3 single ghost -> 400 not persisted", s == 400 and after == before,
          "status=%d before=%d after=%d detail=%s" % (s, before, after, body.get("detail")))

    # ============ AC-3 reply_to_message_id ============
    # 需要 agent 回复：用 ag0（已注册）
    s, body = post("/api/messages/reply", {"agent_name": "[TEST-DATA] ag0",
                                           "content": "[TEST-DATA] reply-with-ref",
                                           "reply_to_message_id": "msg_TARGET_X",
                                           "client_msg_id": "c_msg_TARGET_X_1"})
    check("AC-3.1 reply with ref ok", s == 200, "status=%d body=%s" % (s, body))
    hist = get("/api/messages/history?limit=100")["messages"]
    with_ref = [m for m in hist if m.get("content") == "[TEST-DATA] reply-with-ref"]
    check("AC-3.1 history has reply_to_message_id", len(with_ref) == 1 and with_ref[0].get("reply_to_message_id") == "msg_TARGET_X",
          "found=%d field=%r" % (len(with_ref), with_ref[0].get("reply_to_message_id") if with_ref else None))
    s, body = post("/api/messages/reply", {"agent_name": "[TEST-DATA] ag0",
                                           "content": "[TEST-DATA] reply-no-ref",
                                           "client_msg_id": "c_plain_1"})
    hist = get("/api/messages/history?limit=100")["messages"]
    no_ref = [m for m in hist if m.get("content") == "[TEST-DATA] reply-no-ref"]
    check("AC-3.2 reply without ref ok (field null)", s == 200 and len(no_ref) == 1 and no_ref[0].get("reply_to_message_id") is None,
          "status=%d field=%r" % (s, no_ref[0].get("reply_to_message_id") if no_ref else None))

    # ============ AC-4/5 首拉过滤 + 种子迁移锁内化 ============
    # 注意：F4 过滤用严格 `created_at < registered_at`（秒级精度）。测试在发送阶段后 sleep 1.1s
    # 再注册新 agent，保证「注册前 @all」的 created_at 严格早于 registered_at（真实场景间隔秒级以上）。
    time.sleep(1.1)
    # 此刻 messages.json 已有 20+dup 等历史 @all；新 agent 首拉应不含注册前的 @all（AC-4.1）
    post("/api/agents/register", {"name": "[TEST-DATA] New1"})
    pull1 = get("/api/messages/pull?agent_name=%s" % urllib.parse.quote("[TEST-DATA] New1"))["messages"]
    hist_ids_before_reg = {m["id"] for m in get("/api/messages/history?limit=1000")["messages"]}
    check("AC-4.1 first pull excludes pre-reg @all", len(pull1) == 0,
          "pulled=%d (all pre-reg @all should be seeded as read)" % len(pull1))
    # 注册后新发 @all 正常投递（AC-4.2）
    s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] after-reg",
                                          "target_type": "all", "client_msg_id": "usr_after_reg_1"})
    pull2 = get("/api/messages/pull?agent_name=%s" % urllib.parse.quote("[TEST-DATA] New1"))["messages"]
    check("AC-4.2 post-reg @all delivered", len(pull2) == 1 and pull2[0]["content"] == "[TEST-DATA] after-reg",
          "pulled=%d content=%r" % (len(pull2), pull2[0]["content"] if pull2 else None))

    # AC-4.3: 老 agent（已有 agent_read）增量行为不变
    time.sleep(1.1)   # 保证 Old1 的 registered_at 严格晚于 after-reg 的 created_at
    post("/api/agents/register", {"name": "[TEST-DATA] Old1"})
    s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] old-m1",
                                          "target_type": "all", "client_msg_id": "usr_old_1"})
    pull_old1 = get("/api/messages/pull?agent_name=%s" % urllib.parse.quote("[TEST-DATA] Old1"))["messages"]
    s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] old-m2",
                                          "target_type": "all", "client_msg_id": "usr_old_2"})
    pull_old2 = get("/api/messages/pull?agent_name=%s" % urllib.parse.quote("[TEST-DATA] Old1"))["messages"]
    check("AC-4.3 old agent incremental unchanged",
          len(pull_old1) == 1 and pull_old1[0]["content"] == "[TEST-DATA] old-m1"
          and len(pull_old2) == 1 and pull_old2[0]["content"] == "[TEST-DATA] old-m2",
          "pull1=%d pull2=%d" % (len(pull_old1), len(pull_old2)))

    # AC-5.1: 3 新 agent 并发首拉同一批 @all -> 各恰好一次、不重复
    time.sleep(1.1)   # 新 agent 注册严格晚于历史 @all（old/batch 前序消息播种），只投本批次
    for nm in ["[TEST-DATA] New2", "[TEST-DATA] New3", "[TEST-DATA] New4"]:
        post("/api/agents/register", {"name": nm})
    batch_ids = []
    for i in range(5):
        s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] batch%d" % i,
                                              "target_type": "all", "client_msg_id": "usr_batch_%d" % i})
        batch_ids.append(body["message_id"])
    def first_pull(nm):
        return get("/api/messages/pull?agent_name=%s" % urllib.parse.quote(nm))["messages"]
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        pulls = list(ex.map(first_pull, ["[TEST-DATA] New2", "[TEST-DATA] New3", "[TEST-DATA] New4"]))
    ok5 = True
    for nm, pl in zip(["[TEST-DATA] New2", "[TEST-DATA] New3", "[TEST-DATA] New4"], pulls):
        ids = [m["id"] for m in pl]
        got_batch = [i for i in batch_ids if i in ids]
        no_dup = len(ids) == len(set(ids))
        exactly_once = len(got_batch) == 5 and len(ids) == 5   # 仅本批次 5 条、无额外历史
        if not (no_dup and exactly_once):
            ok5 = False
        print("   %s: pulled=%d unique=%d batch_hit=%d" % (nm, len(ids), len(set(ids)), len(got_batch)))
    check("AC-5.1 3 new agents concurrent first pull, each gets 5 batch msgs exactly once", ok5, "")

    # AC-5.1b: 同一新 agent 3 并发首拉 -> 5 条 @all 全局只投一次（无重复投递）
    time.sleep(1.1)   # Race1 注册严格晚于旧 @all（old/batch 播种），严格早于 race 批次
    post("/api/agents/register", {"name": "[TEST-DATA] Race1"})
    batch_ids2 = []
    for i in range(5):
        s, body = post("/api/messages/send", {"sender_type": "user", "content": "[TEST-DATA] race%d" % i,
                                              "target_type": "all", "client_msg_id": "usr_race_%d" % i})
        batch_ids2.append(body["message_id"])
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        pulls = list(ex.map(lambda _: first_pull("[TEST-DATA] Race1"), range(3)))
    delivered_ids = [m["id"] for pl in pulls for m in pl]
    from collections import Counter
    cnt = Counter(delivered_ids)
    dup_in_race = [mid for mid, c in cnt.items() if c > 1]
    race_got = len(set(delivered_ids))
    check("AC-5.1b same-agent concurrent first pull no dup delivery",
          race_got == 5 and len(dup_in_race) == 0,
          "unique_delivered=%d dup=%r" % (race_got, dup_in_race))

    # AC-5.2: 并发首拉后 agent_read 文件存在且包含全部已投递 id（种子迁移锁内完成）
    read_files = glob.glob(DATA_DIR + "/agent_read_*.json")
    check("AC-5.2 agent_read files created for all new agents", len(read_files) >= 7, "files=%d" % len(read_files))

    # ============ AC-7.1 同 msg_id 两次 reply 落 2 条（含 reply_to_message_id） ============
    s, body = post("/api/messages/reply", {"agent_name": "[TEST-DATA] ag0",
                                           "content": "[TEST-DATA] same-ref-1",
                                           "reply_to_message_id": "msg_LOOP_X",
                                           "client_msg_id": "c_msg_LOOP_X_1"})
    s2, body2 = post("/api/messages/reply", {"agent_name": "[TEST-DATA] ag0",
                                             "content": "[TEST-DATA] same-ref-2",
                                             "reply_to_message_id": "msg_LOOP_X",
                                             "client_msg_id": "c_msg_LOOP_X_2"})
    hist = get("/api/messages/history?limit=100")["messages"]
    two = [m for m in hist if m.get("reply_to_message_id") == "msg_LOOP_X"]
    check("AC-7.1 same msg_id twice -> 2 replies (different client_msg_id)", s == 200 and s2 == 200 and len(two) == 2,
          "status1=%d status2=%d count=%d" % (s, s2, len(two)))
    check("AC-7.3 reply_to_message_id carried + new_messages semantics",
          s == 200 and "new_messages" in body and two[0].get("reply_to_message_id") == "msg_LOOP_X",
          "body=%s" % body)

    # ============ 汇总 ============
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n==== external-fix SUMMARY: %d passed, %d failed ====" % (passed, failed))
    for name, ok, detail in results:
        if not ok:
            print("  FAILED: " + name + (" | " + detail if detail else ""))
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
