# -*- coding: utf-8 -*-
"""QA 独立验收（用户角/浏览器角度）· T-agent-meeting-state-persist

环境：隔离端口 8027 + 独立 DATA_DIR test_data_qa_user_persist（新建，禁碰生产 8000/data）
所有测试数据带 [TEST-DATA] 标记。不改任何源码/数据文件，只验证 + 截图 + 报告。

覆盖（浏览器/用户角）：
- AC-1.1 发送 → 气泡 ≤500ms 出现、输入框立即清空、右侧 user 样式
- AC-1.2 乐观 + 落盘只出现一次（轮询/刷新不重复）
- AC-1.3 乐观内容/目标与落盘一致、刷新无变化
- AC-2.1 落盘后刷新仍可见
- AC-2.2 重启进程后 history 仍返回该消息（浏览器刷新可见）
- AC-2.3 向不存在 agent 发 single → 气泡保留 + 「⚠ 发送失败」+ 重试/删除；失败不污染游标（轮询不全量回放）
- AC-2.4 异步落盘期间连发两条同内容 → 两条均落盘可见、互不覆盖
- AC-3.4/4.2 init/end/lost/reactivated → 灰色居中 .sys-notice；刷新后仍在；不弹 banner；无未读徽标
- AC-4.1 系统消息不入 reads.json、不入 pull
- AC-4.3 四类事件含时间戳与 event 可追溯
- AC-3.1/3.2/3.3 agents.json 持久化 + 失联清扫不复活
- 兼容：index.html ?v= bump + Cache-Control no-cache 强制新 JS（无 system 渲染缺失）

用法：python e2e_state_persist_qa_user/qa_persist_verify.py
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8027"
CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-meeting/server
DATA_DIR = os.path.join(SERVER_DIR, "test_data_qa_user_persist")
SHOTS = os.path.join(SERVER_DIR, "e2e_state_persist_qa_user")
PY = r"C:/Users/67972/AppData/Local/Programs/Python/Python314/python.exe"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + detail) if detail else ""))


def api(method, path, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload.decode("utf-8")))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:
            return e.code, payload


def wipe_test_data():
    import glob
    for f in glob.glob(os.path.join(DATA_DIR, "*")):
        try:
            os.remove(f)
        except OSError:
            pass
    print("wiped:", DATA_DIR)


def find_port_pid(port=8027):
    """Windows netstat 找监听 8027 的 PID。"""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if ("LISTENING" in line) and (":%d " % port in line or ":%d " % port in line):
            parts = line.split()
            if parts:
                return parts[-1]
    return None


def restart_server():
    """杀掉 8027 当前实例，用相同 DATA_DIR 重启（AC-2.2 重启持久化验证）。"""
    pid = find_port_pid(8027)
    if pid:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True)
        time.sleep(1.5)
    env = dict(os.environ)
    env["DATA_DIR"] = DATA_DIR.replace("/", "\\")
    env["SWEEP_INTERVAL"] = "0"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    logf = open(os.path.join(SERVER_DIR, "qa_user_persist_8027.log"), "a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8027", "--log-level", "warning"],
        cwd=SERVER_DIR, env=env, stdout=logf, stderr=logf, creationflags=flags,
    )
    for _ in range(40):
        try:
            s, _ = api("GET", "/health")
            if s == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def load_json(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    wipe_test_data()
    if not restart_server():
        print("FATAL: server not healthy after restart")
        sys.exit(2)
    time.sleep(1)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 390, "height": 780})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))

        # ---------- AC-1.1：路由延迟 800ms 证明 UI 不等响应 ----------
        def delay_send(route):
            time.sleep(0.8)
            route.continue_()
        pg.route("**/api/messages/send", delay_send)

        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(1500)

        # 准备 agent（init 系统消息等 AC-3.4 再验，先只注册不 init 以免干扰 1.1 计数）
        s, _ = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Bob"})
        check("prep register Bob", s == 200, "status=%d" % s)

        # 发送：页内计时（click → temp 气泡出现）
        pg.fill("#message-input", "[TEST-DATA] qa hello")
        timing = pg.evaluate("""() => {
          const t0 = performance.now();
          document.getElementById('send-btn').click();
          const el = document.querySelector('[data-id^="temp_"]');
          return el ? (performance.now() - t0) : -1;
        }""")
        check("AC-1.1 temp bubble <=500ms (UI before response)", 0 <= timing <= 500, "elapsed=%.0fms" % timing)
        input_val = pg.input_value("#message-input")
        check("AC-1.1 input cleared immediately", input_val == "", "value=%r" % input_val)

        # 右侧 user 样式：.msg-row.msg-out flex-direction row-reverse；.msg-bubble.user 绿底
        user_style = pg.evaluate("""(() => {
          const row = document.querySelector('.msg-row.msg-out');
          if (!row) return null;
          const bubble = row.querySelector('.msg-bubble.user');
          const csRow = getComputedStyle(row);
          const csBubble = bubble ? getComputedStyle(bubble) : null;
          return {
            flexDirection: csRow.flexDirection,
            bubbleBg: csBubble ? csBubble.backgroundColor : null,
            hasUserBubble: !!bubble,
            hasDataId: !!row.dataset.id
          };
        })()""")
        check("AC-1.1 user bubble right-side style",
              bool(user_style) and user_style["flexDirection"] == "row-reverse"
              and user_style["hasUserBubble"] and user_style["bubbleBg"] == "rgb(239, 253, 222)",
              "style=%r" % (user_style,))
        pg.screenshot(path=os.path.join(SHOTS, "01_ac11_optimistic.png"))

        # ---------- AC-1.2：升级替换 + 轮询/刷新不重复 ----------
        try:
            pg.wait_for_function("() => !document.querySelector('[data-id^=\"temp_\"]')", timeout=6000)
            check("AC-1.2 upgrade temp->server id", True)
        except Exception as e:
            check("AC-1.2 upgrade temp->server id", False, str(e))
        pg.wait_for_timeout(4500)  # 2+ 轮询周期
        n1 = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] qa hello').length)()""")
        check("AC-1.2 no duplicate after poll", n1 == 1, "count=%d" % n1)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        n2 = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] qa hello').length)()""")
        check("AC-1.2 no duplicate after refresh", n2 == 1, "count=%d" % n2)

        # ---------- AC-1.3：内容/目标一致 + 刷新无错位 ----------
        txt = pg.evaluate("""(()=>{const el=document.querySelector('.msg-bubble.user');return el?el.textContent:null;})()""")
        check("AC-1.3 content same after refresh", txt == "[TEST-DATA] qa hello", "text=%r" % txt)
        hist = api("GET", "/api/messages/history?limit=30")[1]["messages"]
        m = [x for x in hist if x["content"] == "[TEST-DATA] qa hello"]
        check("AC-1.3 target_type persisted=all", bool(m) and m[-1]["target_type"] == "all",
              "target=%r" % (m[-1]["target_type"] if m else None))
        # 顺序：该消息应位于列表尾部（无错位）
        check("AC-1.3 persisted last in history", bool(m) and hist[-1]["id"] == m[-1]["id"],
              "hist_last=%r msg_last=%r" % (hist[-1]["id"] if hist else None, m[-1]["id"] if m else None))

        # ---------- AC-2.1：落盘后刷新仍可见（已在上方 refresh 覆盖） ----------
        pg.screenshot(path=os.path.join(SHOTS, "02_ac12_after_refresh.png"))
        check("AC-2.1 visible after refresh (dup check above)", n2 == 1, "count=%d" % n2)

        # ---------- AC-2.3：失败标记 / 重试 / 删除 + 游标不污染 ----------
        # 向不存在 agent 发 single → 400 → 失败态
        pg.evaluate("""(()=>{const sel=document.getElementById('agent-select');const o=document.createElement('option');o.value='[TEST-DATA] Ghost';o.textContent='[TEST-DATA] Ghost';sel.appendChild(o);sel.value='[TEST-DATA] Ghost';})()""")
        pg.fill("#message-input", "[TEST-DATA] fail-me")
        pg.click("#send-btn")
        pg.wait_for_timeout(1500)
        fb = pg.evaluate("document.querySelectorAll('.msg-failed').length")
        tip = pg.evaluate("""(()=>{const el=document.querySelector('.msg-failed-tip');return el?el.textContent:null;})()""")
        retained = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] fail-me').length)()""")
        has_retry = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-failed-btn')].some(b=>b.textContent==='重试'))()""")
        has_del = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-failed-btn')].some(b=>b.textContent==='删除'))()""")
        check("AC-2.3 failed bar shown", fb == 1, "bars=%d" % fb)
        check("AC-2.3 tip text", tip == "⚠ 发送失败", "tip=%r" % tip)
        check("AC-2.3 bubble retained on failure", retained == 1, "retained=%d" % retained)
        check("AC-2.3 retry+delete buttons present", has_retry and has_del, "retry=%s del=%s" % (has_retry, has_del))
        pg.screenshot(path=os.path.join(SHOTS, "03_ac23_failed.png"))

        # 游标不污染：失败后等 2+ 轮询周期，不应全量回放（气泡数不变，无重复）
        before_poll = pg.evaluate("document.querySelectorAll('.msg-bubble.user').length")
        pg.wait_for_timeout(4500)
        after_poll = pg.evaluate("document.querySelectorAll('.msg-bubble.user').length")
        check("AC-2.3 failure does not pollute cursor (no full replay)", after_poll == before_poll,
              "before=%d after=%d" % (before_poll, after_poll))

        # 重试：注册 Ghost 后点重试 → 成功 sent，fail 消失，不重复
        s, _ = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Ghost"})
        check("prep register Ghost for retry", s == 200)
        pg.click(".msg-failed-btn:has-text('重试')")
        try:
            pg.wait_for_function("() => document.querySelectorAll('.msg-failed').length === 0", timeout=6000)
            check("AC-2.3 retry success (fail bar gone)", True)
        except Exception as e:
            check("AC-2.3 retry success (fail bar gone)", False, str(e))
        after_retry = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] fail-me').length)()""")
        check("AC-2.3 retry no duplicate", after_retry == 1, "count=%d" % after_retry)
        pg.screenshot(path=os.path.join(SHOTS, "04_ac23_retry_ok.png"))

        # 第二次失败 → 删除 → 本地移除；刷新后也不回来（未落盘）
        pg.evaluate("""(()=>{const sel=document.getElementById('agent-select');const o=document.createElement('option');o.value='[TEST-DATA] Ghost2';o.textContent='[TEST-DATA] Ghost2';sel.appendChild(o);sel.value='[TEST-DATA] Ghost2';})()""")
        pg.fill("#message-input", "[TEST-DATA] fail-me-2")
        pg.click("#send-btn")
        pg.wait_for_timeout(1500)
        retained2 = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] fail-me-2').length)()""")
        check("AC-2.3 second failure retained", retained2 == 1, "retained=%d" % retained2)
        pg.click(".msg-failed-btn:has-text('删除')")
        pg.wait_for_timeout(400)
        after_del = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] fail-me-2').length)()""")
        check("AC-2.3 delete removes local bubble", after_del == 0, "count=%d" % after_del)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        after_del_refresh = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] fail-me-2').length)()""")
        check("AC-2.3 deleted msg not resurrect after refresh (not persisted)", after_del_refresh == 0,
              "count=%d" % after_del_refresh)

        # ---------- AC-2.4：异步落盘期间连发两条同内容 ----------
        pg.evaluate("""(()=>{document.getElementById('agent-select').value='all';})()""")
        pg.fill("#message-input", "[TEST-DATA] same")
        pg.click("#send-btn")
        pg.fill("#message-input", "[TEST-DATA] same")
        pg.click("#send-btn")
        pg.wait_for_timeout(3000)
        same_bubbles = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] same').length)()""")
        check("AC-2.4 two same-content bubbles visible", same_bubbles == 2, "count=%d" % same_bubbles)
        msgs = load_json("messages.json")
        same_persisted = [x for x in msgs if x.get("content") == "[TEST-DATA] same"]
        cmids = {x.get("client_msg_id") for x in same_persisted}
        check("AC-2.4 both persisted server-side, distinct client_msg_id",
              len(same_persisted) == 2 and len(cmids) == 2,
              "persisted=%d cmid_unique=%d" % (len(same_persisted), len(cmids)))
        pg.screenshot(path=os.path.join(SHOTS, "05_ac24_two_same.png"))

        # ---------- AC-3.4/4.2：init/end/lost/reactivated 系统消息 ----------
        # 先滚动到顶部，随后触发 init → poll 只带回系统消息时不应弹 banner
        pg.evaluate("document.getElementById('message-list').scrollTop = 0")
        pg.wait_for_timeout(300)
        s, _ = api("POST", "/api/agents/[TEST-DATA]%20Bob/session?active=true", None)
        check("api init Bob", s == 200)
        pg.wait_for_timeout(2500)
        # AC-3.1：init 后 agents.json 持久化 session=true/status=working；状态栏显示在线
        agents1 = {a["name"]: a for a in load_json("agents.json")}
        bob1 = agents1.get("[TEST-DATA] Bob", {})
        check("AC-3.1 init persisted session=true/status=working",
              bob1.get("session") is True and bob1.get("status") == "working",
              "session=%r status=%r" % (bob1.get("session"), bob1.get("status")))
        pg.wait_for_timeout(3500)   # 等 loadAgentStatus 周期刷新状态栏
        status_txt1 = pg.evaluate("document.getElementById('agent-status').textContent")
        check("AC-3.1 status bar shows Bob online after init", "[TEST-DATA] Bob" in status_txt1,
              "status_text=%r" % status_txt1)
        banner_display = pg.evaluate("""(()=>{const el=document.getElementById('new-msg-banner');return el?getComputedStyle(el).display:null;})()""")
        sys_notices = pg.evaluate("document.querySelectorAll('.sys-notice').length")
        check("AC-4.2 no 'N 条新消息' banner for system msg", banner_display != "block", "banner=%r" % banner_display)
        check("AC-3.4 init sys-notice rendered", sys_notices >= 1, "sys_notices=%d" % sys_notices)
        sys_style = pg.evaluate("""(()=>{const el=document.querySelector('.sys-notice');if(!el)return null;const cs=getComputedStyle(el);return {color:cs.color,textAlign:cs.textAlign,alignSelf:cs.alignSelf,bg:cs.backgroundColor};})()""")
        check("AC-4.2 sys-notice gray centered",
              bool(sys_style) and sys_style["color"] == "rgb(153, 153, 153)" and sys_style["textAlign"] == "center"
              and sys_style["alignSelf"] == "center",
              "style=%r" % (sys_style,))
        # 系统消息无未读徽标
        sys_badges = pg.evaluate("""(()=>{const list=document.getElementById('message-list');let bad=0;list.querySelectorAll('.sys-notice').forEach(n=>{if(n.nextElementSibling&&n.nextElementSibling.classList.contains('read-status'))bad++;});return bad;})()""")
        check("AC-4.1/4.2 system msg no read badge", sys_badges == 0, "badges=%d" % sys_badges)
        pg.screenshot(path=os.path.join(SHOTS, "06_ac34_sys_init.png"))

        # end
        s, _ = api("POST", "/api/agents/[TEST-DATA]%20Bob/session?active=false", None)
        check("api end Bob", s == 200)
        pg.wait_for_timeout(2500)
        # AC-3.2：end 后 agents.json 持久化 session=false/status=offline；状态栏不再显示
        agents2 = {a["name"]: a for a in load_json("agents.json")}
        bob2 = agents2.get("[TEST-DATA] Bob", {})
        check("AC-3.2 end persisted session=false/status=offline",
              bob2.get("session") is False and bob2.get("status") == "offline",
              "session=%r status=%r" % (bob2.get("session"), bob2.get("status")))
        pg.wait_for_timeout(3500)   # 等 loadAgentStatus 周期刷新状态栏
        status_txt2 = pg.evaluate("document.getElementById('agent-status').textContent")
        check("AC-3.2 status bar hides Bob after end", "[TEST-DATA] Bob" not in status_txt2,
              "status_text=%r" % status_txt2)
        end_visible = pg.evaluate("""(()=>[...document.querySelectorAll('.sys-notice')].some(e=>e.textContent.includes('[TEST-DATA] Bob 下线了')))()""")
        check("AC-4.3 end sys-notice rendered", end_visible, "")

        # lost：先 init 置 working，再把 last_seen 改 30min 前 → status 触发 SWEEP_INTERVAL=0 清扫
        s, _ = api("POST", "/api/agents/[TEST-DATA]%20Bob/session?active=true", None)
        check("api re-init Bob for lost", s == 200)
        agents = load_json("agents.json")
        import datetime
        old = (datetime.datetime.now() - datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
        for a in agents:
            if a.get("name") == "[TEST-DATA] Bob":
                a["last_seen"] = old
        with open(os.path.join(DATA_DIR, "agents.json"), "w", encoding="utf-8") as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
        api("GET", "/api/agents/status")   # 触发清扫
        pg.wait_for_timeout(2500)
        lost_visible = pg.evaluate("""(()=>[...document.querySelectorAll('.sys-notice')].some(e=>e.textContent.includes('[TEST-DATA] Bob 已离线（失联超时）')))()""")
        check("AC-4.3 lost sys-notice rendered (sweep)", lost_visible, "")
        # AC-3.3：清扫后刷新，agent 不复活为在线
        st = api("GET", "/api/agents/status")[1]["agents"]
        bob = [x for x in st if x["name"] == "[TEST-DATA] Bob"]
        check("AC-3.3 swept agent not resurrected online", bool(bob) and bob[0]["presence"] == "offline",
              "presence=%r" % (bob[0]["presence"] if bob else None))
        pg.screenshot(path=os.path.join(SHOTS, "07_ac34_sys_lost.png"))

        # reactivated：同名 register 唤醒
        s, r = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Bob"})
        check("api reactivated Bob", s == 200 and r.get("reactivated") is True, "reactivated=%r" % r.get("reactivated"))
        pg.wait_for_timeout(2500)
        react_visible = pg.evaluate("""(()=>[...document.querySelectorAll('.sys-notice')].some(e=>e.textContent.includes('[TEST-DATA] Bob 重新上线了')))()""")
        check("AC-4.3 reactivated sys-notice rendered", react_visible, "")
        pg.screenshot(path=os.path.join(SHOTS, "08_ac43_sys_reactivated.png"))

        # ---------- AC-3.4：刷新后系统消息仍在（history 回显） ----------
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        sys_after_refresh = pg.evaluate("document.querySelectorAll('.sys-notice').length")
        check("AC-3.4 sys-notices visible after refresh (history echo)", sys_after_refresh >= 4,
              "sys_notices=%d" % sys_after_refresh)
        pg.screenshot(path=os.path.join(SHOTS, "09_ac34_after_refresh.png"))

        # ---------- AC-4.1：系统消息不入 reads.json、不入 pull ----------
        reads = load_json("reads.json")
        sys_ids = {x["id"] for x in load_json("messages.json") if x.get("sender_type") == "system"}
        reads_ids = {r["message_id"] for r in reads}
        check("AC-4.1 system msgs not in reads.json", len(sys_ids & reads_ids) == 0,
              "sys_ids=%d reads_ids=%d overlap=%d" % (len(sys_ids), len(reads_ids), len(sys_ids & reads_ids)))
        pulled = api("GET", "/api/messages/pull?agent_name=[TEST-DATA]%20Bob")[1]["messages"]
        pulled_sys = [x for x in pulled if x.get("sender_type") == "system"]
        check("AC-4.1 pull returns no system msgs", len(pulled_sys) == 0, "pulled_sys=%d" % len(pulled_sys))

        # ---------- AC-4.3：事件含时间戳与 event 可追溯（messages.json + status_events.jsonl） ----------
        ev = {}
        for x in load_json("messages.json"):
            if x.get("sender_type") == "system" and x.get("event"):
                ev.setdefault(x["event"], []).append(x)
        have_ts = all(x.get("created_at") for events in ev.values() for x in events)
        check("AC-4.3 all 4 event types persisted with created_at",
              set(ev.keys()) >= {"init", "end", "lost", "reactivated"} and have_ts,
              "events=%r" % sorted(ev.keys()))
        se_path = os.path.join(DATA_DIR, "status_events.jsonl")
        se_lines = open(se_path, encoding="utf-8").read().strip().splitlines() if os.path.exists(se_path) else []
        se_events = {json.loads(x)["event"] for x in se_lines}
        check("AC-4.3 status_events.jsonl traceable", {"session_on", "session_off", "lost", "reactivated"} <= se_events,
              "events=%r" % sorted(se_events))

        # ---------- 兼容：?v= bump + no-cache 强制新 JS ----------
        s, html = api("GET", "/", raw=True)
        html_txt = html.decode("utf-8", "ignore")
        v_app = re.search(r'app\.js\?v=([\w.-]+)', html_txt)
        v_css = re.search(r'styles\.css\?v=([\w.-]+)', html_txt)
        check("compat index.html ?v= bump present", bool(v_app) and bool(v_css),
              "app.js?v=%s styles.css?v=%s" % (v_app.group(1) if v_app else None, v_css.group(1) if v_css else None))
        # no-cache 头
        req = urllib.request.Request(BASE + "/")
        with urllib.request.urlopen(req, timeout=10) as r:
            cc = r.headers.get("Cache-Control", "")
        check("compat / Cache-Control no-cache", "no-cache" in cc, "cc=%r" % cc)
        # 静态 app.js 含 system 渲染分支（新 JS 无 system 渲染缺失）
        s, js = api("GET", "/static/app.js?v=" + (v_app.group(1) if v_app else "x"), raw=True)
        js_txt = js.decode("utf-8", "ignore")
        check("compat new app.js has system branch (no system rendering missing)",
              "sender_type === 'system'" in js_txt and "sys-notice" in js_txt,
              "has_system_branch=%s has_sys_notice=%s" % ("sender_type === 'system'" in js_txt, "sys-notice" in js_txt))

        # ---------- 页面无 JS 错误 ----------
        check("no page JS errors", len(errors) == 0, "; ".join(errors[:3]))

        b.close()

    # ---------- AC-2.2：重启进程持久化 ----------
    print("\n-- AC-2.2: restart server process --")
    if restart_server():
        hist2 = api("GET", "/api/messages/history?limit=30")[1]["messages"]
        contents = [x["content"] for x in hist2]
        check("AC-2.2 messages survive process restart (API history)",
              "[TEST-DATA] qa hello" in contents and "[TEST-DATA] same" in contents,
              "total=%d has_hello=%s has_same=%s" % (len(hist2), "[TEST-DATA] qa hello" in contents, "[TEST-DATA] same" in contents))
        # 浏览器刷新也可见
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, headless=True,
                                  args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 390, "height": 780})
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)
            hello = pg.evaluate("""(()=>[...document.querySelectorAll('.msg-bubble.user')].filter(e=>e.textContent==='[TEST-DATA] qa hello').length)()""")
            sysn = pg.evaluate("document.querySelectorAll('.sys-notice').length")
            check("AC-2.2 browser refresh after restart shows persisted msg + sys notices",
                  hello == 1 and sysn >= 4, "hello=%d sys_notices=%d" % (hello, sysn))
            pg.screenshot(path=os.path.join(SHOTS, "10_ac22_after_restart.png"))
            b.close()
    else:
        check("AC-2.2 server restart", False, "restart failed")

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n==== QA SUMMARY: %d passed, %d failed ====" % (passed, failed))
    for name, ok, detail in results:
        if not ok:
            print("  FAILED: " + name + ((" | " + detail) if detail else ""))
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
