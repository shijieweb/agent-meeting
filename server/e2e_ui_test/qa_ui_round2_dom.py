# -*- coding: utf-8 -*-
"""QA round-2 INDEPENDENT UI/DOM verification for T-agent-meeting-bugfix-12.

Angle: presentation/DOM correctness (dynamic names, no ghost nodes, bounded polling).
READ-ONLY on source. Runs against an ISOLATED server (port 8023 + own DATA_DIR).
"""
from playwright.sync_api import sync_playwright
import subprocess, json, time, os, sys, urllib.parse

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test/qa_ui_shots"
DATA_DIR = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/test_data_qa_ui"
BASE = "http://127.0.0.1:8023"
TS = str(int(time.time()))
os.makedirs(OUT, exist_ok=True)

results = []
def check(ac, name, cond, detail=""):
    results.append((ac, name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {ac} {name} :: {detail}", flush=True)

def api(method, path, payload=None):
    url = BASE + path
    cmd = ["curl.exe", "-s", "-m", "10", "-w", "\n__CODE__%{http_code}"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if payload is not None:
            cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    raw, _, code = (r.stdout or "").rpartition("__CODE__")
    try:
        body = json.loads(raw.strip())
    except Exception:
        body = {"_raw": raw.strip()[:300]}
    return body, code.strip()

def limit_of(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "limit" in q:
        try:
            return int(q["limit"][0])
        except Exception:
            return -1
    return None  # no explicit limit (server default = 30)

def dots(pg):
    return pg.evaluate("[...document.querySelectorAll('#agent-status .status-dot')].map(d=>({t:d.textContent,c:d.className}))")

def user_bubbles(pg):
    return pg.evaluate("[...document.querySelectorAll('#message-list .message-bubble.user')].map(b=>b.textContent)")

def last_read_status(pg):
    return pg.evaluate("""(() => {
        const all=[...document.querySelectorAll('#message-list .message-bubble.user')];
        if(!all.length) return null;
        const last=all[all.length-1];
        let n=last.nextElementSibling;
        while(n && !n.classList.contains('read-status')) n=n.nextElementSibling;
        return n ? n.textContent.trim() : null;
    })()""")

def main():
    # ---------- pre: isolated server sanity + agents registered, chat MUST be empty ----------
    h, hc = api("GET", "/health")
    print("health:", hc, h, flush=True)
    hist0, _ = api("GET", "/api/messages/history?limit=200")
    print("pre-existing messages:", len(hist0.get("messages", [])), flush=True)
    api("POST", "/api/agents/register", {"name": "WorkBuddy"})
    api("POST", "/api/agents/register", {"name": "Claude"})
    ag, _ = api("GET", "/api/agents")
    print("registered agents:", ag, flush=True)

    net = []          # (t, method, url)
    net_resp = []     # (url, status)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 520, "height": 900})
        pg.on("request", lambda r: net.append((time.time(), r.method, r.url)))
        pg.on("response", lambda r: net_resp.append((r.url, r.status)))
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3500)

        print("\n########## PHASE A : F10 empty-chat polling (AC-10.1/10.2) ##########", flush=True)
        rs_size = pg.evaluate("(typeof readStatusNodes!=='undefined') ? readStatusNodes.size : 'undef'")
        bub0 = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        print(f"empty page: bubbles={bub0} readStatusNodes.size={rs_size}", flush=True)
        t0 = time.time()
        pg.wait_for_timeout(6000)   # > one 5s refreshReadReceipts period
        win = [(t, u) for (t, m, u) in net if t >= t0 and "/api/messages/history" in u]
        win_refresh = [u for (t, u) in win if limit_of(u) == 200]
        win_other = [u for (t, u) in win if limit_of(u) != 200]
        print("history requests in empty 6s window:", len(win), flush=True)
        for u in win:
            print("   ", u[1].replace(BASE, ""), flush=True)
        check("AC-10.1", "empty chat: NO refreshReadReceipts(limit=200) request",
              len(win_refresh) == 0,
              f"limit200_count={len(win_refresh)}; other_history={len(win_other)} (pollNew) e.g. {win_other[:2]}")
        check("AC-10.1-literal", "empty chat: ZERO /api/messages/history request at all (strict PRD evidence wording)",
              len(win) == 0,
              f"total_history_in_window={len(win)} -> {[u.replace(BASE,'') for _,u in win][:3]}")
        pg.screenshot(path=os.path.join(OUT, "A_empty.png"))

        # AC-10.2: user message appears -> polling resumes
        pg.select_option("#agent-select", "all")
        m102 = "QAUI-10.2-resume " + TS
        pg.fill("#message-input", m102)
        pg.click("#send-btn")
        pg.wait_for_timeout(600)
        t1 = time.time()
        pg.wait_for_timeout(6000)
        win2 = [u for (t, m, u) in net if t >= t1 and "/api/messages/history" in u and limit_of(u) == 200]
        check("AC-10.2", "after user msg: refreshReadReceipts(limit=200) resumes",
              len(win2) >= 1, f"limit200_requests_after_send={len(win2)} e.g. {win2[:1]}")

        print("\n########## PHASE C-start : register NewAgent (F6 clock starts) ##########", flush=True)
        reg_body, reg_code = api("POST", "/api/agents/register", {"name": "NewAgent"})
        t_reg = time.time()
        opts_before = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
        print(f"register NewAgent -> {reg_code} {reg_body}; options_before={opts_before}", flush=True)

        print("\n########## PHASE B : F1 multi-agent status (AC-1.3/1.4) ##########", flush=True)
        r, c = api("POST", "/api/agents/Claude/session?active=true")
        print("set Claude session active=true ->", c, r, flush=True)
        pg.wait_for_timeout(3600)
        d = dots(pg)
        st, _ = api("GET", "/api/agents/status")
        texts = [x["t"] for x in d]
        print("DOM #agent-status dots:", json.dumps(d, ensure_ascii=False), flush=True)
        print("API /api/agents/status:", json.dumps(st, ensure_ascii=False), flush=True)
        api_names = [a["name"] for a in st.get("agents", [])]
        check("AC-1.3", "#agent-status shows 'Claude·处理中' within 3.6s",
              any("Claude·处理中" in t for t in texts), f"dots={texts}")
        check("AC-1.4a", "one dot rendered per agent in /api/agents/status",
              len(d) == len(api_names), f"dom_dots={len(d)} api_agents={len(api_names)} names={api_names}")
        check("AC-1.4b", "every API agent name appears in DOM (no agent invisible to boss)",
              all(any(n in t for t in texts) for n in api_names), f"api={api_names} dom={texts}")
        pg.screenshot(path=os.path.join(OUT, "B_status_claude_working.png"))

        # independent states: WorkBuddy end-of-shift + Claude working simultaneously
        r2, c2 = api("POST", "/api/agents/WorkBuddy/session?active=false")
        print("set WorkBuddy session active=false ->", c2, r2, flush=True)
        pg.wait_for_timeout(3600)
        d2 = dots(pg)
        t2s = [x["t"] for x in d2]
        st2, _ = api("GET", "/api/agents/status")
        print("DOM dots:", json.dumps(d2, ensure_ascii=False), flush=True)
        print("API status:", json.dumps(st2, ensure_ascii=False), flush=True)
        check("AC-1.4c", "independent states: WorkBuddy·已收工 AND Claude·处理中 co-exist",
              any("WorkBuddy·已收工" in t for t in t2s) and any("Claude·处理中" in t for t in t2s),
              f"dots={t2s}")
        # DOM vs API consistency (offline->已收工 / working->处理中)
        mismatch = []
        for a in st2.get("agents", []):
            want = None
            if a.get("status") == "offline":
                want = a["name"] + "·已收工"
            elif a.get("status") == "working":
                want = a["name"] + "·处理中"
            elif a.get("status") == "waiting":
                want = a["name"] + "·待命中"
            if want and not any(want == t for t in t2s):
                mismatch.append((a["name"], a.get("status"), want))
        check("AC-1.4d", "DOM text matches GET /api/agents/status for every agent",
              not mismatch, f"mismatches={mismatch} dom={t2s}")
        pg.screenshot(path=os.path.join(OUT, "B_status_independent.png"))

        print("\n########## PHASE D : F3.3/F4.1 ghost-message DOM (AC-3.3/4.1/4.2) ##########", flush=True)
        # AC-3.3 : same client_msg_id retried -> exactly 1 bubble
        retry_txt = "QAUI-3.3-retry " + TS
        retry = pg.evaluate("""async (txt) => {
            const cid = 'usr_qaui_' + Date.now();
            const body = {sender_type:'user', content:txt, target_type:'all', target_agent_name:null, client_msg_id:cid};
            const opt = {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
            const r1 = await fetch('/api/messages/send', opt); const d1 = await r1.json();
            const r2 = await fetch('/api/messages/send', opt); const d2 = await r2.json();   // network-jitter retry
            const mk = (id) => ({id:id, content:txt, sender_type:'user', sender_agent_name:null,
                                 target_type:'all', target_agent_name:null, created_at:'', client_msg_id:null, read_by:[]});
            appendMessage(mk(d1.message_id));   // optimistic append, 1st attempt
            appendMessage(mk(d2.message_id));   // optimistic append, retry
            return {cid:cid, s1:r1.status, s2:r2.status, id1:d1.message_id, id2:d2.message_id};
        }""", retry_txt)
        pg.wait_for_timeout(3000)  # let a pollNew cycle run too
        cnt = len([t for t in user_bubbles(pg) if retry_txt in t])
        srv = [m for m in api("GET", "/api/messages/history?limit=200")[0].get("messages", []) if retry_txt in (m.get("content") or "")]
        check("AC-3.3", "retry with same client_msg_id -> exactly 1 .message-bubble.user",
              cnt == 1, f"dom_bubbles={cnt} server_rows={len(srv)} retry={retry}")

        # AC-4.1/4.2 : send to non-existent agent (400) -> no ghost bubble, input cleared
        before = user_bubbles(pg)
        pg.evaluate("""() => {
            const s = document.getElementById('agent-select');
            const o = document.createElement('option'); o.value='FakeAgent'; o.textContent='FakeAgent';
            s.appendChild(o); s.value='FakeAgent';
        }""")
        ghost_txt = "QAUI-4.1-ghost " + TS
        pg.fill("#message-input", ghost_txt)
        net_mark = len(net_resp)
        pg.click("#send-btn")
        pg.wait_for_timeout(1500)
        after = user_bubbles(pg)
        send_resp = [(u.replace(BASE, ""), s) for (u, s) in net_resp[net_mark:] if "/api/messages/send" in u]
        inp = pg.evaluate("document.getElementById('message-input').value")
        check("AC-4.1", "failed send (FakeAgent 400) -> user bubble count unchanged, no ghost",
              len(after) == len(before) and not any(ghost_txt in t for t in after),
              f"before={len(before)} after={len(after)} send_resp={send_resp}")
        check("AC-4.2", "failed send -> #message-input cleared, no mismatched bubble",
              inp == "", f"input={inp!r}")
        pg.screenshot(path=os.path.join(OUT, "D_no_ghost.png"))
        pg.evaluate("""() => { const s=document.getElementById('agent-select');
            [...s.options].filter(o=>o.value==='FakeAgent').forEach(o=>o.remove()); s.value='all'; }""")

        print("\n########## PHASE E : F5 read badge + bounded polling (AC-5.1/5.2) ##########", flush=True)
        pg.select_option("#agent-select", "Claude")
        read_txt = "QAUI-5.2-read " + TS
        pg.fill("#message-input", read_txt)
        pg.click("#send-btn")
        pg.wait_for_timeout(800)
        st_before = last_read_status(pg)
        check("AC-5.2-pre", "fresh single-target user msg badge = ○未读",
              st_before is not None and "未读" in st_before, f"badge={st_before!r}")
        api("GET", "/api/messages/pull?agent_name=Claude")
        t_pull = time.time()
        flipped = True
        try:
            pg.wait_for_function("""() => {
                const all=[...document.querySelectorAll('#message-list .message-bubble.user')];
                if(!all.length) return false;
                let n=all[all.length-1].nextElementSibling;
                while(n && !n.classList.contains('read-status')) n=n.nextElementSibling;
                return n && n.textContent.includes('已读') && !n.textContent.includes('未读');
            }""", timeout=5000)
        except Exception:
            flipped = False
        elapsed = round(time.time() - t_pull, 2)
        st_after = last_read_status(pg)
        check("AC-5.2", "badge flips to ✓已读 within 5s of agent pull",
              flipped, f"badge_before={st_before!r} badge_after={st_after!r} elapsed={elapsed}s")
        pg.screenshot(path=os.path.join(OUT, "E_read_badge.png"))

        print("\n########## PHASE C-check : F6 dropdown dynamic refresh (AC-6.1/6.2) ##########", flush=True)
        found = False
        deadline = t_reg + 35
        while time.time() < deadline:
            opts = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
            if "NewAgent" in opts:
                found = True
                break
            pg.wait_for_timeout(1000)
        elapsed_reg = round(time.time() - t_reg, 1)
        opts_after = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
        check("AC-6.1", "NewAgent <option> appears in #agent-select within 30s of register",
              found and elapsed_reg <= 32, f"found={found} elapsed={elapsed_reg}s options_after={opts_after}")
        check("AC-6.2", "pre-existing options preserved after dynamic refresh",
              set(opts_before).issubset(set(opts_after)),
              f"before={opts_before} after={opts_after}")
        pg.screenshot(path=os.path.join(OUT, "C_dropdown.png"))

        print("\n########## PHASE F : F9 all four state labels use REAL agent name (AC-9.1/9.2) ##########", flush=True)
        agents_file = os.path.join(DATA_DIR, "agents.json")
        backup = open(agents_file, "r", encoding="utf-8").read()
        now = time.time()
        iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        crafted = [
            {"name": "IdleOne", "registered_at": iso(now), "last_seen": None, "status": "waiting", "session": False},
            {"name": "DoneOne", "registered_at": iso(now), "last_seen": iso(now), "status": "offline", "session": True},
            {"name": "LostOne", "registered_at": iso(now - 5000), "last_seen": iso(now - 1200), "status": "working", "session": True},
            {"name": "GoneOne", "registered_at": iso(now - 5000), "last_seen": iso(now - 600), "status": "waiting", "session": False},
            {"name": "BusyOne", "registered_at": iso(now), "last_seen": iso(now), "status": "working", "session": True},
            {"name": "WaitOne", "registered_at": iso(now), "last_seen": iso(now), "status": "waiting", "session": False},
        ]
        open(agents_file, "w", encoding="utf-8").write(json.dumps(crafted, ensure_ascii=False, indent=2))
        stc, _ = api("GET", "/api/agents/status")
        print("crafted API status:", json.dumps(stc, ensure_ascii=False), flush=True)
        pg.wait_for_timeout(3800)
        d9 = dots(pg)
        t9 = [x["t"] for x in d9]
        print("DOM dots (4 states):", json.dumps(d9, ensure_ascii=False), flush=True)
        want9 = {
            "待命(no last_seen)": "IdleOne·待命",
            "已收工(offline)": "DoneOne·已收工",
            "掉线(session+stale)": "LostOne·已掉线·需重唤",
            "离线(no session+stale)": "GoneOne·离线",
            "处理中(working)": "BusyOne·处理中",
            "待命中(waiting)": "WaitOne·待命中",
        }
        missing = {k: v for k, v in want9.items() if not any(v == t for t in t9)}
        check("AC-9.2", "all state labels prefixed with the REAL agent name",
              not missing, f"missing={missing} dom={t9}")
        status_html = pg.inner_html("#agent-status")
        check("AC-9.1", "no '阿编' hardcoded anywhere in rendered status DOM",
              not any("阿编" in t for t in t9) and "阿编" not in status_html,
              f"status_html={status_html[:420]}")
        reawaken = pg.evaluate("(()=>{const h=document.getElementById('reawaken-hint');return h?h.style.display:'absent';})()")
        check("AC-9.2b", "lost agent also toggles #reawaken-hint visible", reawaken == "block", f"display={reawaken}")
        hint_txt = pg.evaluate("(()=>{const h=document.getElementById('reawaken-hint');return h?h.textContent.trim():null;})()")
        check("AC-9.1-extra", "VISIBLE reawaken hint must not hardcode '阿编' (LostOne is the lost agent)",
              hint_txt is not None and "阿编" not in hint_txt and "LostOne" in (hint_txt or ""),
              f"visible_hint_text={hint_txt!r}")
        # header layout observation (multi-agent dots crammed into one header span)
        layout = pg.evaluate("""(()=>{const c=document.getElementById('agent-status');const h=c.closest('header');
            return {status_w:Math.round(c.getBoundingClientRect().width), header_w:Math.round(h.getBoundingClientRect().width),
                    overflow_px:Math.round(c.scrollWidth-c.clientWidth), dots:c.children.length,
                    container_own_class:c.className};})()""")
        print("header layout with 6 agents:", layout, flush=True)
        pg.screenshot(path=os.path.join(OUT, "F_four_states.png"))
        open(agents_file, "w", encoding="utf-8").write(backup)   # restore isolated data

        print("\n########## GLOBAL : bounded polling over whole run (AC-5.1) ##########", flush=True)
        hist_urls = [u for (t, m, u) in net if "/api/messages/history" in u]
        over = [u for u in hist_urls if (limit_of(u) or 0) > 200]
        big = [u for u in hist_urls if limit_of(u) == 10000]
        uniq = sorted(set(u.replace(BASE, "") for u in hist_urls))
        print(f"total history requests={len(hist_urls)}; unique shapes:", flush=True)
        for u in uniq[:12]:
            print("   ", u, flush=True)
        check("AC-5.1", "every /api/messages/history request has limit<=200 (no limit=10000)",
              not over and not big, f"total={len(hist_urls)} over200={len(over)} limit10000={len(big)}")
        pg.screenshot(path=os.path.join(OUT, "G_final.png"))
        b.close()

    print("\n==== QA-UI ROUND2 SUMMARY ====", flush=True)
    pn = sum(1 for _, _, c, _ in results if c)
    fn = sum(1 for _, _, c, _ in results if not c)
    print(f"PASS={pn} FAIL={fn} TOTAL={len(results)}", flush=True)
    for ac, n, c, dd in results:
        print(f"  [{'OK ' if c else 'XX '}] {ac} {n} :: {dd}", flush=True)

if __name__ == "__main__":
    main()
