# -*- coding: utf-8 -*-
"""qa_user acceptance — commit 9f0e67f (前端换皮 + IME 修复 + 19:44 状态).

Isolated live server: http://127.0.0.1:8022  (DATA_DIR=D:/tmp/am-qa-user, Python314 uvicorn)
Browser: Playwright iPhone 12 device emulation (390x844, isMobile, hasTouch).

Covers:
  IME核心  : 聚焦滚动到底(a) + 输入栏在视口内 dvh+sticky(b) + 失焦幂等
  交互回归 : B1 发消息入列 / B2 @所有人下拉 / B3 已读回执 / B4 浮动新消息提示 / B5 触顶加载更早
  19:44    : C 处理中(不再掉线) / D 处理任务
Zero-pollution is checked separately (server/data 8000 不被写).
"""
import os, sys, json, uuid, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from playwright.sync_api import sync_playwright

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
BASE = "http://127.0.0.1:8022"
SHOTS = "D:/tmp/am-qa-user"
DATA = "D:/tmp/am-qa-user"

results = []


def check(ac, name, cond, detail=""):
    results.append((ac, name, bool(cond), str(detail)))
    print("[%s] %s :: %s" % ("PASS" if cond else "FAIL", ac + " " + name, detail), flush=True)


def iso_minus(sec):
    return (datetime.datetime.now() - datetime.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S")


def main():
    with sync_playwright() as p:
        device = p.devices["iPhone 12"]
        browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        context = browser.new_context(**device)
        page = context.new_page()

        # ============ Phase A — IME 核心 ============
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        page.focus("#message-input")
        page.wait_for_timeout(600)  # > 350ms 聚焦滚动定时器

        ime_a_detail = page.evaluate("""() => { const l=document.getElementById('message-list');
            return {st:l.scrollTop, ch:l.clientHeight, sh:l.scrollHeight}; }""")
        ime_a = (ime_a_detail["st"] + ime_a_detail["ch"]) >= (ime_a_detail["sh"] - 4)
        check("IME-a", "聚焦后消息列表滚到底(最新可见)", ime_a, ime_a_detail)

        ime_b_detail = page.evaluate("""() => { const ia=document.querySelector('.input-area');
            return {bottom: Math.round(ia.getBoundingClientRect().bottom), ih: window.innerHeight}; }""")
        ime_b = ime_b_detail["bottom"] <= (ime_b_detail["ih"] + 1)
        check("IME-b", "输入栏底<=视口高(dvh+sticky抗IME)", ime_b, ime_b_detail)

        # 失焦 → 列表仍在；再聚焦验证幂等
        page.evaluate("document.getElementById('message-input').blur()")
        page.wait_for_timeout(250)
        list_ok = page.evaluate("""() => { const l=document.getElementById('message-list');
            return !!l && l.children.length>0; }""")
        check("IME-blur", "失焦后列表仍在(无异常)", list_ok, "")

        page.focus("#message-input")
        page.wait_for_timeout(600)
        ime_a2_detail = page.evaluate("""() => { const l=document.getElementById('message-list');
            return {st:l.scrollTop, ch:l.clientHeight, sh:l.scrollHeight}; }""")
        ime_a2 = (ime_a2_detail["st"] + ime_a2_detail["ch"]) >= (ime_a2_detail["sh"] - 4)
        check("IME-idem", "再次聚焦仍滚到底(幂等)", ime_a2, ime_a2_detail)
        page.screenshot(path=os.path.join(SHOTS, "qa_ime_mobile.png"))

        # ============ Phase B2 — @所有人 下拉 ============
        opt = page.evaluate("""() => {
            const s=document.getElementById('agent-select');
            const o=s.querySelector('option[value=\"all\"]');
            return o ? o.textContent : null; }""")
        check("B2", "@所有人下拉项存在且文案正确", opt is not None and "@所有人" in opt, "opt=" + str(opt))

        # ============ Phase B1 + B3 — 发消息入列 + 已读回执 ============
        cid1 = "usr_" + str(uuid.uuid4())
        content1 = "[QA] 验收回归消息 reg1 " + cid1[:8]
        page.evaluate("""async (o) => {
            const c=o.c, cid=o.cid;
            await fetch('/api/messages/send', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({sender_type:'user', target_type:'all', target_agent_name:null, content:c, client_msg_id:cid})});
        }""", {"c": content1, "cid": cid1})
        page.evaluate("window.pollNew()")
        found = False
        for _ in range(25):  # <=2.5s
            found = page.evaluate("""(c) => [...document.querySelectorAll('#message-list .msg-bubble')].some(b => b.textContent.includes(c))""", content1)
            if found:
                break
            page.wait_for_timeout(100)
        check("B1", "API发消息2.5s内出现在#message-list", found, "content=" + content1[:28])

        rs = page.evaluate("""(c) => {
            const rows=[...document.querySelectorAll('#message-list .msg-row.msg-out')];
            for (const r of rows){ const b=r.querySelector('.msg-bubble');
                if(b && b.textContent.includes(c)){ const sib=r.nextElementSibling;
                    return (sib && sib.classList.contains('read-status')) ? sib.textContent : null; } }
            return null; }""", content1)
        b3 = rs is not None and ('已读' in rs or '✓' in rs or '○' in rs)
        check("B3", "user消息渲染.read-status(✓/○/已读)", b3, "read-status=" + str(rs))
        page.screenshot(path=os.path.join(SHOTS, "qa_send_mobile.png"))

        # ============ Phase B4 — 浮动新消息提示 ============
        page.evaluate("""() => { const l=document.getElementById('message-list'); l.scrollTop = 0; window.onListScroll(); }""")
        page.wait_for_timeout(200)
        cid2 = "usr_" + str(uuid.uuid4())
        content2 = "[QA] 验收回归消息 reg2 " + cid2[:8]
        page.evaluate("""async (o) => {
            const c=o.c, cid=o.cid;
            await fetch('/api/messages/send', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({sender_type:'user', target_type:'all', target_agent_name:null, content:c, client_msg_id:cid})});
        }""", {"c": content2, "cid": cid2})
        page.evaluate("window.pollNew()")
        banner_seen = False
        banner_text = None
        for _ in range(12):  # <=1.2s 抓浮动提示(1s 自动消失)
            bt = page.evaluate("""() => { const b=document.getElementById('new-msg-banner');
                return b ? {disp:getComputedStyle(b).display, txt:b.textContent} : null; }""")
            if bt and bt["disp"] != "none" and "条新消息" in bt["txt"]:
                banner_seen = True
                banner_text = bt["txt"]
                break
            page.wait_for_timeout(100)
        check("B4", "非底部新消息弹浮动提示(含'条新消息')", banner_seen, "banner=" + str(banner_text))
        page.screenshot(path=os.path.join(SHOTS, "qa_banner_mobile.png"))

        # ============ Phase B5 — 触顶加载更早 ============
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        before = page.evaluate("""() => {
            const first=document.querySelector('#message-list .msg-row');
            if(!first) return null;
            const b=first.querySelector('.msg-bubble');
            const m=b? b.textContent.match(/历史消息 #0*(\\d+)/) : null;
            return m? parseInt(m[1],10) : null; }""")
        page.evaluate("""() => { const l=document.getElementById('message-list'); l.scrollTop = 0; window.onListScroll(); }""")
        page.wait_for_timeout(1500)
        after = page.evaluate("""() => {
            const first=document.querySelector('#message-list .msg-row');
            if(!first) return null;
            const b=first.querySelector('.msg-bubble');
            const m=b? b.textContent.match(/历史消息 #0*(\\d+)/) : null;
            return m? parseInt(m[1],10) : null; }""")
        b5 = before is not None and after is not None and after < before
        check("B5", "触顶加载更早消息(clientOldestId前移)", b5, "before=%s after=%s" % (before, after))
        page.screenshot(path=os.path.join(SHOTS, "qa_older_mobile.png"))

        # ============ Phase C — 19:44 处理中(不再掉线) ============
        agents = [{"name": "MeetingBot", "registered_at": iso_minus(1100),
                   "last_seen": iso_minus(1000), "status": "working", "session": True}]
        with open(os.path.join(DATA, "agents.json"), "w", encoding="utf-8") as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3200)  # 等 loadAgentStatus 刷新
        status = page.evaluate("""async () => { const r=await fetch('/api/agents/status'); return await r.json(); }""")
        meeting = next((a for a in status.get("agents", []) if a["name"] == "MeetingBot"), None)
        c_session = meeting is not None and meeting.get("session") is True
        check("C-status", "GET /api/agents/status: MeetingBot.session=true", c_session, str(meeting))
        agent_text = page.evaluate("""() => document.getElementById('agent-status').textContent""")
        c_disp = ("处理中" in agent_text) and ("掉线" not in agent_text) and ("需重唤" not in agent_text)
        check("C-disp", "页面#agent-status含'处理中'且不含'掉线/需重唤'", c_disp, "text=" + agent_text)
        page.screenshot(path=os.path.join(SHOTS, "qa_status_mobile.png"))

        # ============ Phase D — 19:44 处理任务 ============
        page.evaluate("""async () => { await fetch('/api/agents/register', {method:'POST',
            headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:'TaskAgent'})}); }""")
        cid3 = "usr_" + str(uuid.uuid4())
        page.evaluate("""async (cid) => {
            await fetch('/api/messages/send', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({sender_type:'user', target_type:'single', target_agent_name:'TaskAgent',
                    content:'[QA] 任务消息给TaskAgent', client_msg_id:cid})});
        }""", cid3)
        page.evaluate("window.loadAgentStatus()")
        page.wait_for_timeout(600)
        status = page.evaluate("""async () => { const r=await fetch('/api/agents/status'); return await r.json(); }""")
        task = next((a for a in status.get("agents", []) if a["name"] == "TaskAgent"), None)
        d_unread = task is not None and task.get("has_unread") is True
        check("D-status", "GET /api/agents/status: TaskAgent.has_unread=true", d_unread, str(task))
        agent_text = page.evaluate("""() => document.getElementById('agent-status').textContent""")
        d_disp = "处理任务" in agent_text
        check("D-disp", "页面#agent-status含'处理任务'", d_disp, "text=" + agent_text)
        page.screenshot(path=os.path.join(SHOTS, "qa_task_mobile.png"))

        browser.close()

    print("\n==== qa_user acceptance 9f0e67f SUMMARY ====", flush=True)
    pn = sum(1 for _a, _n, c, _d in results if c)
    print("PASS=%d FAIL=%d TOTAL=%d" % (pn, len(results) - pn, len(results)))
    for a, n, c, d in results:
        print("  [%s] %s %s :: %s" % ("OK " if c else "XX ", a, n, d))


if __name__ == "__main__":
    main()
