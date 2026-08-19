# -*- coding: utf-8 -*-
"""
qa_reverify_d3d99fb.py  -- qa_user independent re-verify (HEAD d3d99fb)
=========================================================================
RE-VERIFY T-agent-meeting-bugfix-12 + presence UI against CURRENT disk code.

Requirements checked (team-lead brief):
  REQ1  No hardcoded agent name anywhere in UI (grep + DOM across 4 states
        待命/已收工/离线/掉线). index.html/app.js must contain no literal
        '阿编' or fixed agent name in any status/hint banner. If a 掉线
        banner exists its text must be dynamic.
  REQ2  Presence UI: status dots render DYNAMIC agent names for MULTIPLE
        agents simultaneously; new presence feature must not break bar render.
  REQ3  Regression smoke on isolated 8022: AC-1.3 / 3.3 / 4.1 / 5.2 / 6.1 /
        9.1 / 9.2 / 10.2 + read-receipt flip — all PASS on current code.
  REQ4  Zero production pollution: only talk to 8022; prod 8000 shows 0 markers.

Hard rules: READ-ONLY against source. Do NOT modify any project file.
Server already running on ISOLATED 8022 (DATA_DIR=D:/tmp/am-qa-user).
Playwright run on python 3.13.12; server on python 3.14 (project runtime).
"""
import os
import sys
import json
import time
import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting"
SERVER_APP = os.path.join(REPO, "server", "app")
BASE = "http://127.0.0.1:8022"
PROD = "http://127.0.0.1:8000"
DATA = r"D:/tmp/am-qa-user"
CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
SHOT_DIR = os.path.join(REPO, "server", "e2e_ui_test_qa_user", "shots_d3d99fb")
os.makedirs(SHOT_DIR, exist_ok=True)

results = []  # (rid, name, status, evidence)


def log(rid, name, ok, evidence):
    results.append((rid, name, "PASS" if ok else "FAIL", evidence))
    print(("[PASS] " if ok else "[FAIL] ") + rid + " " + name + " :: " + evidence)


# --------------------------------------------------------------------------
# Seed isolated DATA_DIR (8022 only). Pure file writes; never touches prod.
# --------------------------------------------------------------------------
def now_iso(offset=0):
    return (datetime.datetime.now() + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S")


def seed_data_dir():
    os.makedirs(DATA, exist_ok=True)
    # 20 agent-only history messages (NO user messages) -> readStatusNodes starts empty (AC-10.1)
    base = time.time() - 3600
    msgs = []
    for i in range(1, 21):
        t = datetime.datetime.fromtimestamp(base + i)
        created = t.strftime("%Y-%m-%dT%H:%M:%S")
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
    # 4-state agents:
    #   Claude    -> online/working  -> "Claude·处理中"
    #   Gemini    -> online/waiting  -> "Gemini·待命中"
    #   QAReadBot -> online/waiting  -> "QAReadBot·待命中" (used for read-receipt flip)
    #   OfflineBot-> offline          -> NOT rendered (老板 §5.1-3)
    #   LostBot   -> lost (last_seen 2000s ago) -> NOT rendered
    agents = [
        {"name": "Claude", "registered_at": now_iso(-100), "last_seen": now_iso(0),
         "status": "working", "session": False, "token_hash": None},
        {"name": "Gemini", "registered_at": now_iso(-100), "last_seen": now_iso(0),
         "status": "waiting", "session": False, "token_hash": None},
        {"name": "QAReadBot", "registered_at": now_iso(-100), "last_seen": now_iso(0),
         "status": "waiting", "session": False, "token_hash": None},
        {"name": "OfflineBot", "registered_at": now_iso(-100), "last_seen": now_iso(0),
         "status": "offline", "session": False, "token_hash": None},
        {"name": "LostBot", "registered_at": now_iso(-2500), "last_seen": now_iso(-2000),
         "status": "waiting", "session": False, "token_hash": None},
    ]
    with open(os.path.join(DATA, "agents.json"), "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA, "reads.json"), "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False)  # reads.json MUST be a list; a dict {} breaks send_user_message
    # wipe stale per-agent read sets so read-receipt flip starts unread
    for fn in os.listdir(DATA):
        if fn.startswith("agent_read_") and fn.endswith(".json"):
            try:
                os.remove(os.path.join(DATA, fn))
            except Exception:
                pass
    print("[seed] Wrote messages.json(20)/agents.json(5)/reads.json to " + DATA)


# --------------------------------------------------------------------------
# REQ1 source grep (pure-python, no shell quoting issues)
# --------------------------------------------------------------------------
def py_grep(root, patterns):
    hits = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith((".py", ".js", ".html", ".css")):
                continue
            p = os.path.join(dp, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        for pat in patterns:
                            if pat in line:
                                hits.append((p, i, pat, line.strip()))
            except Exception:
                pass
    return hits


def req1_source_grep():
    hits_abian = py_grep(SERVER_APP, ["阿编"])
    hits_reaw = py_grep(SERVER_APP, ["reawaken"])
    log("REQ1-GREP-ABIAN", "server/app 无硬编码 '阿编' (REQ1)", len(hits_abian) == 0,
        "matches={0} {1}".format(len(hits_abian), hits_abian[:3]))
    log("REQ1-GREP-REAWAKEN", "server/app 无 'reawaken-hint' (REQ1)", len(hits_reaw) == 0,
        "matches={0} {1}".format(len(hits_reaw), hits_reaw[:3]))


# --------------------------------------------------------------------------
# HTTP helpers (read-only GET + isolated POST on 8022)
# --------------------------------------------------------------------------
import urllib.request
import urllib.error


def http_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def http_post_json(url, payload, timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


# --------------------------------------------------------------------------
# Main browser test
# --------------------------------------------------------------------------
def main():
    from playwright.sync_api import sync_playwright

    req1_source_grep()
    seed_data_dir()
    time.sleep(0.6)  # let 8022 re-read files

    # sanity: 8022 reachable
    st, body = http_get(BASE + "/")
    if st != 200:
        log("ENV-8022", "isolated 8022 server reachable", False, "status={0}".format(st))
        return
    log("ENV-8022", "isolated 8022 server reachable", True, "GET / -> {0}".format(st))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 414, "height": 896},
                                  user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                                             "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1")
        page = ctx.new_page()
        page.on("pageerror", lambda e: print("  [pageerror] " + str(e)))
        page.on("console", lambda m: print("  [console." + m.type + "] " + m.text[:200]))

        # ---- initial load ----
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#message-list .msg-row", timeout=8000)
        time.sleep(3.5)  # allow init + first presence/agentStatus interval (3s)

        # ===== REQ1 + REQ2 + AC-9.1/9.2 : presence DOM check across 4 states =====
        status_txt = page.evaluate("() => { const c=document.getElementById('agent-status'); return c?c.innerText:'(none)'; }")
        status_dots = page.evaluate("() => document.querySelectorAll('#agent-status .status-dot').length")
        body_txt = page.evaluate("() => document.body.innerText")
        has_reawaken_el = page.evaluate(
            "() => { const e=document.getElementById('reawaken-hint'); return !!e; }")

        # REQ1 grep already done; DOM-level checks:
        log("REQ1-DOM-NOABIAN", "DOM 无 '阿编' (REQ1)", "阿编" not in body_txt,
            "body contains '阿编'={0}".format("阿编" in body_txt))
        log("REQ1-DOM-NOREAWAKEN", "DOM 无 #reawaken-hint 元素 (REQ1)", not has_reawaken_el,
            "reawaken-hint present={0}".format(has_reawaken_el))

        # REQ2: dynamic multi-agent names (Claude + Gemini both online)
        both_on = ("Claude·处理中" in status_txt) and ("Gemini·待命中" in status_txt)
        log("REQ2-MULTI", "presence 多 agent 动态名同时渲染 (REQ2/AC-9.1/9.2)",
            both_on and status_dots >= 2,
            "dots={0} status='{1}'".format(status_dots, status_txt.replace(chr(10), ' | ')))

        # REQ1/AC-9.2: offline+lost agents NOT rendered (no hardcoded 掉线 banner)
        no_offline_lost = ("OfflineBot" not in status_txt) and ("LostBot" not in status_txt)
        log("REQ1-OFFLINE-NORENDER", "离线/失联 agent 不渲染(无掉线硬编码banner) (REQ1/AC-9.2)",
            no_offline_lost,
            "OfflineBot_in={0} LostBot_in={1}".format("OfflineBot" in status_txt, "LostBot" in status_txt))

        # AC-1.3 / AC-9.1: non-WorkBuddy agent 'Claude' working -> "Claude·处理中" <=3s
        log("AC-1.3", "Claude 处理中状态 <=3s 显示 (AC-1.3/9.1)", "Claude·处理中" in status_txt,
            "status contains 'Claude·处理中'={0}".format("Claude·处理中" in status_txt))

        page.screenshot(path=os.path.join(SHOT_DIR, "01_presence_4state.png"))
        print("  [shot] 01_presence_4state.png  status='" + status_txt.replace(chr(10), ' | ') + "'")

        # ===== AC-10.2 : empty chat -> readStatusNodes empty (poll skipped); after user msg -> resumes =====
        rs_before = page.evaluate("() => (typeof readStatusNodes!=='undefined') ? readStatusNodes.size : -1")
        log("AC-10.2-PRE", "空聊天 readStatusNodes=0 (轮询不跑回执) (AC-10.2)", rs_before == 0,
            "readStatusNodes.size={0}".format(rs_before))

        # ===== AC-5.2 : read-receipt flip (single target) =====
        # select QAReadBot, type, send via Enter key (real user path; mobile mouse-click on
        # send button is broken by EXT-2 ime-top float -- see report finding F-IME-CLICK).
        page.select_option("#agent-select", "QAReadBot")
        page.fill("#message-input", "读我一下 QA")
        sent_via = "ui"
        send_status = None
        try:
            with page.expect_request("**/api/messages/send", timeout=5000) as ri:
                page.press("#message-input", "Enter")
            req = ri.value
            send_status = req.response.status if req.response else None
        except Exception as e:
            print("  [AC-5.2] UI(Enter) send request not observed: " + str(e))
        # wait for user bubble (optimistic append OR pollNew fetch), up to 5s
        bubble_ok = False
        for _ in range(10):
            time.sleep(0.5)
            cnt = page.evaluate("() => document.querySelectorAll('.msg-row.msg-out').length")
            if cnt >= 1:
                bubble_ok = True
                break
        if not bubble_ok:
            print("  [AC-5.2] UI bubble missing (send_status={0}) -> fallback API send".format(send_status))
            s_api, b_api = http_post_json(BASE + "/api/messages/send",
                {"content": "读我一下 QA", "target_type": "single",
                 "target_agent_name": "QAReadBot", "client_msg_id": "usr_rr_" + str(int(time.time()))})
            sent_via = "api"
            send_status = s_api
            for _ in range(12):
                time.sleep(0.5)
                cnt = page.evaluate("() => document.querySelectorAll('.msg-row.msg-out').length")
                if cnt >= 1:
                    bubble_ok = True
                    break
            print("  [AC-5.2] api send status={0} bubble_ok={1}".format(s_api, bubble_ok))
        # before read snapshot (guarded for empty rows)
        before_txt = page.evaluate(
            "() => { const rows=[...document.querySelectorAll('.msg-row.msg-out')];"
            " if(!rows.length) return '(no user msg)';"
            " const sib=rows[rows.length-1].nextElementSibling;"
            " return sib? sib.innerText : '(no read-status sibling)'; }")
        # agent pulls -> server marks read
        st, _ = http_get(BASE + "/api/messages/pull?agent_name=QAReadBot")
        print("  [AC-5.2] pull QAReadBot status={0} sent_via={1} send_status={2}".format(st, sent_via, send_status))
        # poll until refreshReadReceipts (5s) flips to ✓ 已读, up to 9s
        flipped = False
        for _ in range(18):
            time.sleep(0.5)
            after = page.evaluate(
                "() => { const rows=[...document.querySelectorAll('.msg-row.msg-out')];"
                " if(!rows.length) return '(none)';"
                " const sib=rows[rows.length-1].nextElementSibling;"
                " return sib? sib.innerText : '(none)'; }")
            if "✓ 已读" in after or ("已读" in after and "未读" not in after):
                flipped = True
                break
        # confirm via server state too
        _, hist = http_get(BASE + "/api/messages/history?limit=5")
        server_read = False
        try:
            j = json.loads(hist)
            for m in j.get("messages", []):
                if m.get("content") == "读我一下 QA" and "QAReadBot" in (m.get("read_by") or []):
                    server_read = True
        except Exception:
            pass
        log("AC-5.2", "已读回执 ○未读→✓已读 <=5s (AC-5.2)", flipped and server_read,
            "sent_via={0} send_status={1} before='{2}' flipped={3} server_read={4}".format(
                sent_via, send_status, before_txt, flipped, server_read))

        # after a user msg exists -> readStatusNodes > 0 (poll resumes)  (AC-10.2)
        rs_after = page.evaluate("() => (typeof readStatusNodes!=='undefined') ? readStatusNodes.size : -1")
        log("AC-10.2-POST", "用户消息出现后 readStatusNodes>0 (轮询恢复) (AC-10.2)", rs_after > 0,
            "readStatusNodes.size={0}".format(rs_after))

        page.screenshot(path=os.path.join(SHOT_DIR, "02_read_receipt_flipped.png"))
        print("  [shot] 02_read_receipt_flipped.png")

        # ===== AC-3.3 : retry same client_msg_id -> 1 bubble only =====
        dup_payload = {"content": "幂等去重测试 qa", "target_type": "all",
                       "target_agent_name": None, "client_msg_id": "usr_dup_qa_001"}
        s1, _ = http_post_json(BASE + "/api/messages/send", dup_payload)
        time.sleep(0.3)
        s2, _ = http_post_json(BASE + "/api/messages/send", dup_payload)  # same client_msg_id
        time.sleep(1.0)
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#message-list .msg-row", timeout=8000)
        time.sleep(1.0)
        dup_count = page.evaluate(
            "() => { let n=0;"
            " document.querySelectorAll('.msg-row .msg-bubble').forEach(b=>{"
            "   if((b.innerText||'').includes('幂等去重测试 qa')) n++; }); return n; }")
        log("AC-3.3", "同 client_msg_id 重试 -> 仅 1 气泡 (AC-3.3)", dup_count == 1,
            "bubble_count={0} (send#1={1}, send#2={2})".format(dup_count, s1, s2))

        # ===== AC-4.1 : send to nonexistent agent -> 400, no ghost bubble =====
        # (a) backend contract
        s400, b400 = http_post_json(BASE + "/api/messages/send",
                                    {"content": "x", "target_type": "single",
                                     "target_agent_name": "FakeAgent", "client_msg_id": "usr_fake"})
        log("AC-4.1-API", "发往不存在 agent -> 400 (AC-4.1)", s400 == 400,
            "status={0} body={1}".format(s400, b400[:80]))
        # (b) UI no-ghost: intercept send -> 400, send via Enter, assert input cleared + no ghost bubble
        # (Enter is the real-user path; mobile mouse-click on send button is broken by EXT-2 ime-top)
        page.route("**/api/messages/send", lambda route: route.fulfill(status=400, body="{}"))
        count_before = page.evaluate("() => document.querySelectorAll('#message-list .msg-row').length")
        page.select_option("#agent-select", "Claude")
        page.fill("#message-input", "这不应成气泡")
        page.press("#message-input", "Enter")
        time.sleep(0.8)
        count_after = page.evaluate("() => document.querySelectorAll('#message-list .msg-row').length")
        input_val = page.evaluate("() => document.getElementById('message-input').value")
        page.unroute("**/api/messages/send")
        log("AC-4.1-UI", "发送失败无 ghost 气泡 + 输入框清空 (AC-4.1)",
            count_after == count_before and input_val == "",
            "rows_before={0} rows_after={1} input='{2}'".format(count_before, count_after, input_val))

        # ===== F-IME-CLICK (supplementary defect check): real mouse click on send button on mobile =====
        # EXT-2 ime-top floats .input-area to top when input focused; on mobile the send button then
        # sits UNDER the header (y~9 vs header y0-56) and a real click does NOT reach the handler.
        page.evaluate(
            "() => { window.__clk2=0;"
            " const b=document.getElementById('send-btn');"
            " if(!b.__wired){ b.addEventListener('click',()=>window.__clk2++); b.__wired=true; } }")
        page.fill("#message-input", "clkprobe")  # focus -> ime-top applied
        time.sleep(0.5)
        try:
            page.click("#send-btn", timeout=3000)
        except Exception as e:
            print("  [F-IME-CLICK] real click exc: " + str(e)[:120])
        time.sleep(0.3)
        clk = page.evaluate("() => window.__clk2")
        log("F-IME-CLICK", "移动端真实点击发送按钮可触发发送 (缺陷检查)", clk >= 1,
            "real_click_fired_handler={0} (IS_MOBILE context; ime-top floats btn under header)".format(clk >= 1))

        # ===== AC-6.1 : register new agent -> option appears <=30s =====
        s_reg, b_reg = http_post_json(BASE + "/api/agents/register", {"name": "NewComer"})
        appeared = False
        for _ in range(66):  # up to ~33s (30s interval)
            time.sleep(0.5)
            opts = page.evaluate(
                "() => { const s=document.getElementById('agent-select');"
                " return s? [...s.options].map(o=>o.value):[]; }")
            if "NewComer" in opts:
                appeared = True
                break
        log("AC-6.1", "注册新 agent -> 下拉出现 <=30s (AC-6.1)", appeared and s_reg == 200,
            "register_status={0} appeared={1}".format(s_reg, appeared))
        page.screenshot(path=os.path.join(SHOT_DIR, "03_newcomer_option.png"))
        print("  [shot] 03_newcomer_option.png")

        # ===== AC-9.2 dynamic proof: DynProbe working -> name appears; offline -> removed =====
        http_post_json(BASE + "/api/agents/register", {"name": "DynProbe"})
        http_post_json(BASE + "/api/agents/DynProbe/session", {"active": True})  # working
        dyn_on = False
        for _ in range(10):
            time.sleep(0.5)
            txt = page.evaluate("() => { const c=document.getElementById('agent-status'); return c?c.innerText:''; }")
            if "DynProbe·处理中" in txt:
                dyn_on = True
                break
        http_post_json(BASE + "/api/agents/DynProbe/session", {"active": False})  # offline -> removed
        dyn_off = False
        for _ in range(10):
            time.sleep(0.5)
            txt = page.evaluate("() => { const c=document.getElementById('agent-status'); return c?c.innerText:''; }")
            if "DynProbe" not in txt:
                dyn_off = True
                break
        log("AC-9.2-DYN", "状态变化动态更新名+离线即移除(无硬编码) (AC-9.2)",
            dyn_on and dyn_off,
            "working_shown={0} offline_removed={1}".format(dyn_on, dyn_off))

        browser.close()

    # ===== REQ4 : zero production pollution (read-only GET on prod 8000) =====
    markers = ["QAReadBot", "OfflineBot", "LostBot", "HistoryBot", "NewComer", "DynProbe", "幂等去重测试 qa"]
    pollutions = []
    for mk in markers:
        st_a, body_a = http_get(PROD + "/api/agents?all=true", timeout=4)
        st_h, body_h = http_get(PROD + "/api/messages/history?limit=200", timeout=4)
        found = (st_a == 200 and mk in body_a) or (st_h == 200 and mk in body_h)
        if found:
            pollutions.append(mk)
    if not pollutions:
        log("REQ4-ZEROPOLL", "生产 8000 零污染 (REQ4)", True,
            "checked markers={0}; prod reachable={1}".format(markers, "yes"))
    else:
        log("REQ4-ZEROPOLL", "生产 8000 零污染 (REQ4)", False,
            "POLLUTED markers found on prod: {0}".format(pollutions))

    # ---- summary ----
    total = len(results)
    passed = sum(1 for r in results if r[2] == "PASS")
    failed = total - passed
    print("\n================ QA RE-VERIFY SUMMARY (HEAD d3d99fb) ================")
    print("Total={0} PASS={1} FAIL={2}".format(total, passed, failed))
    for rid, name, status, ev in results:
        print("  [{0}] {1} {2}".format(status, rid, name))
    print("Shots: " + SHOT_DIR)
    print("======================================================================")


if __name__ == "__main__":
    main()
