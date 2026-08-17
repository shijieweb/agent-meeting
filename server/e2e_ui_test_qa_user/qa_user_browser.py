# -*- coding: utf-8 -*-
"""QA round-2 INDEPENDENT browser verification (END-USER angle) for T-agent-meeting-bugfix-12.

Read-only against source. Own isolated server on 127.0.0.1:8022 + own DATA_DIR.
Verifies browser/DOM/network ACs the engineer deferred:
  AC-3.1/3.3, AC-4.1/4.2, AC-5.1/5.2, AC-6.1/6.2, AC-1.3/1.4, AC-9.1/9.2, AC-10.1/10.2
"""
from playwright.sync_api import sync_playwright
import subprocess, json, time, os

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://127.0.0.1:8022"
TS = str(int(time.time()))

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
    raw = r.stdout or ""
    code = None
    if "__CODE__" in raw:
        body, _, c = raw.rpartition("\n__CODE__")
        code = c.strip()
    else:
        body = raw
    try:
        return json.loads(body), code
    except Exception:
        return {"_raw": body[:300]}, code


def bubbles_with(pg, content):
    return pg.evaluate(
        """(c) => [...document.querySelectorAll('#message-list .message-bubble.user')]
                  .filter(b => b.textContent.includes(c)).length""", content)


def user_bubble_count(pg):
    return pg.evaluate("document.querySelectorAll('#message-list .message-bubble.user').length")


def read_status_of(pg, content):
    """Find the .read-status sibling that follows the user bubble containing `content`."""
    return pg.evaluate(
        """(c) => {
            const all=[...document.querySelectorAll('#message-list .message-bubble.user')];
            const b=all.find(x=>x.textContent.includes(c));
            if(!b) return null;
            let n=b.nextElementSibling;
            while(n && !n.classList.contains('read-status')) n=n.nextElementSibling;
            return n ? n.textContent.trim() : null;
        }""", content)


def status_dots(pg):
    return pg.evaluate(
        """[...document.querySelectorAll('#agent-status .status-dot')]
             .map(d => ({text:d.textContent.trim(), cls:d.className}))""")


def options(pg):
    return pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")


def main():
    reqs = []          # every request the page makes: (t, method, url, post_data)
    resps = []         # (t, status, url)

    # ── Pre-arrange: register two agents (NO messages created yet -> chat stays empty for AC-10.1)
    print("=== SETUP ===", flush=True)
    print("register WorkBuddy:", api("POST", "/api/agents/register", {"name": "WorkBuddy"}), flush=True)
    print("register Claude   :", api("POST", "/api/agents/register", {"name": "Claude"}), flush=True)
    hist0, _ = api("GET", "/api/messages/history?limit=30")
    print("messages in fresh DB:", len(hist0.get("messages", [])), flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 480, "height": 900})

        pg.on("request", lambda r: reqs.append((time.time(), r.method, r.url,
                                                (r.post_data or "") if r.method == "POST" else "")))
        pg.on("response", lambda r: resps.append((time.time(), r.status, r.url)))

        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(1500)
        print("title:", pg.title(), "| initial user bubbles:", user_bubble_count(pg), flush=True)

        # ═══════════ F10 · AC-10.1 empty chat must NOT poll read receipts ═══════════
        print("\n=== F10 AC-10.1 : empty-chat 12s observation window ===", flush=True)
        t_start = time.time()
        pg.wait_for_timeout(12000)          # >2 full 5s refreshReadReceipts cycles
        t_end = time.time()
        win = [(t, m, u) for (t, m, u, _) in reqs if t_start <= t <= t_end]
        hist_win = [u for (t, m, u) in win if "/api/messages/history" in u]
        rr_win = [u for u in hist_win if "limit=200" in u]      # refreshReadReceipts signature
        poll_win = [u for u in hist_win if "limit=200" not in u]  # pollNew (2s incremental)
        empty_before = user_bubble_count(pg)
        check("AC-10.1", "空聊天 refreshReadReceipts(limit=200) 不发请求", len(rr_win) == 0,
              f"user_bubbles={empty_before}; limit=200 请求数={len(rr_win)}; 窗口12s")
        check("AC-10.1-strict", "空聊天 5s 窗口内『无任何 history 请求』(PRD 字面)", len(hist_win) == 0,
              f"实测 12s 内 history 请求 {len(hist_win)} 条(全部来自 pollNew 2s 轮询), 样本={poll_win[:3]}")
        pg.screenshot(path=os.path.join(OUT, "qa_1_empty.png"))

        # ═══════════ F1/F9 · AC-1.3 / 1.4 / 9.1 / 9.2 dynamic status ═══════════
        print("\n=== F1/F9 AC-1.3/1.4/9.1/9.2 : dynamic agent status ===", flush=True)
        print("set Claude working  :", api("POST", "/api/agents/Claude/session?active=true"), flush=True)
        print("set WorkBuddy end   :", api("POST", "/api/agents/WorkBuddy/session?active=false"), flush=True)
        pg.wait_for_timeout(4000)           # loadAgentStatus interval = 3s
        dots = status_dots(pg)
        texts = [d["text"] for d in dots]
        st_json, _ = api("GET", "/api/agents/status")
        claude_ok = any(t == "Claude·处理中" for t in texts)
        wb_ok = any(t == "WorkBuddy·已收工" for t in texts)
        check("AC-1.3", "#agent-status 显示 Claude·处理中(非默认待命)", claude_ok, f"dots={texts}")
        check("AC-9.1", "状态文案用真实 name, 无硬编码 阿编/WorkBuddy", claude_ok and not any("阿编" in t for t in texts),
              f"dots={texts}")
        check("AC-1.4", "多 Agent 各自独立状态 且与 /api/agents/status 一致", claude_ok and wb_ok and len(dots) == 2,
              f"dots={texts} | api={[(a['name'], a['status'], a['session']) for a in st_json.get('agents', [])]}")
        pg.screenshot(path=os.path.join(OUT, "qa_2_status.png"))

        # AC-9.2 : 待命/已收工/离线/掉线 four states all carry dynamic name
        api("GET", "/api/messages/pull?agent_name=Claude")      # got_data=False -> waiting
        pg.wait_for_timeout(4000)
        texts_waiting = [d["text"] for d in status_dots(pg)]
        check("AC-9.2", "待命中/已收工 两类态均拼真实 name", 
              any(t == "Claude·待命中" for t in texts_waiting) and any(t == "WorkBuddy·已收工" for t in texts_waiting),
              f"dots={texts_waiting} (离线/掉线态需 >120s/>600s 静默, 见报告说明)")

        # ═══════════ F3 · AC-3.1 client_msg_id in payload ═══════════
        print("\n=== F3 AC-3.1 : client_msg_id in POST /send payload ===", flush=True)
        pg.select_option("#agent-select", "Claude")
        m31 = f"QA-AC31-client-msg-id-{TS}"
        pg.fill("#message-input", m31)
        pg.click("#send-btn")
        pg.wait_for_timeout(1200)
        sends = [(t, u, pd) for (t, m, u, pd) in reqs if m == "POST" and "/api/messages/send" in u]
        payload31 = json.loads(sends[-1][2]) if sends else {}
        cmid = payload31.get("client_msg_id")
        check("AC-3.1", "UI 发送 payload 含非空 client_msg_id",
              isinstance(cmid, str) and len(cmid) > 0,
              f"payload={json.dumps(payload31, ensure_ascii=False)}")

        # ═══════════ F10 · AC-10.2 poll resumes once a user msg exists ═══════════
        t_start2 = time.time()
        pg.wait_for_timeout(6500)
        rr_after = [u for (t, m, u, _) in reqs
                    if t >= t_start2 and "/api/messages/history" in u and "limit=200" in u]
        check("AC-10.2", "有 user 消息后 read 轮询恢复(limit=200 请求出现)", len(rr_after) >= 1,
              f"6.5s 内 limit=200 请求 {len(rr_after)} 条, 样本={rr_after[:2]}")

        # ═══════════ F5 · AC-5.2 read badge flips within 5s ═══════════
        print("\n=== F5 AC-5.2 : read badge ○ -> ✓ after agent pull ===", flush=True)
        st_before = read_status_of(pg, m31)
        api("GET", "/api/messages/pull?agent_name=Claude")       # Claude reads it (server persists)
        t0 = time.time()
        flipped = True
        try:
            pg.wait_for_function(
                """(c) => {
                    const all=[...document.querySelectorAll('#message-list .message-bubble.user')];
                    const b=all.find(x=>x.textContent.includes(c));
                    if(!b) return false;
                    let n=b.nextElementSibling;
                    while(n && !n.classList.contains('read-status')) n=n.nextElementSibling;
                    return !!(n && n.textContent.includes('已读'));
                }""", arg=m31, timeout=9000)
        except Exception:
            flipped = False
        elapsed = time.time() - t0
        st_after = read_status_of(pg, m31)
        check("AC-5.2", "已读徽标 ≤5s 由 ○未读 翻 ✓已读", flipped and elapsed <= 5.0 + 1.0,
              f"before={st_before!r} after={st_after!r} 实测 {elapsed:.2f}s (刷新周期5s)")
        pg.screenshot(path=os.path.join(OUT, "qa_3_read.png"))

        # ═══════════ F5 · AC-5.1 bounded limit, never 10000 ═══════════
        all_hist = [u for (t, m, u, _) in reqs if "/api/messages/history" in u]
        has10000 = [u for u in all_hist if "limit=10000" in u]
        import re
        limits = sorted({int(x) for u in all_hist for x in re.findall(r"[?&]limit=(\d+)", u)})
        check("AC-5.1", "history 请求无 limit=10000 且 limit≤200",
              len(has10000) == 0 and all(l <= 200 for l in limits),
              f"抓到 history 请求 {len(all_hist)} 条; 出现过的 limit 值={limits}; limit=10000 命中={len(has10000)}")

        # ═══════════ F3 · AC-3.3 retry with SAME client_msg_id -> 1 bubble ═══════════
        print("\n=== F3 AC-3.3 : retry same client_msg_id -> single bubble ===", flush=True)
        fixed = f"usr_qa-fixed-{TS}"
        pg.evaluate("(id) => { window.genClientMsgId = () => id; }", fixed)
        m33 = f"QA-AC33-retry-dedup-{TS}"
        pg.fill("#message-input", m33)
        pg.click("#send-btn")          # attempt 1
        pg.wait_for_timeout(1200)
        cnt_first = bubbles_with(pg, m33)
        pg.fill("#message-input", m33)
        pg.click("#send-btn")          # attempt 2 == network-retry (same client_msg_id)
        pg.wait_for_timeout(1500)
        cnt_after = bubbles_with(pg, m33)
        sends33 = [pd for (t, m, u, pd) in reqs if m == "POST" and "/api/messages/send" in u
                   and fixed in (pd or "")]
        hist_all, _ = api("GET", "/api/messages/history?limit=200")
        backend_cnt = sum(1 for x in hist_all.get("messages", []) if x.get("client_msg_id") == fixed)
        check("AC-3.3", "同 client_msg_id 重试后气泡数==1", cnt_after == 1,
              f"首次={cnt_first} 重试后={cnt_after}; 前端发出 send 请求 {len(sends33)} 次(同 id); 后端该 client_msg_id 落库 {backend_cnt} 条")
        pg.wait_for_timeout(2500)      # let pollNew run: server copy must not create a 2nd bubble
        check("AC-3.3-poll", "轮询回灌后仍为 1 个气泡(无重影)", bubbles_with(pg, m33) == 1,
              f"轮询后计数={bubbles_with(pg, m33)}")
        pg.screenshot(path=os.path.join(OUT, "qa_4_dedup.png"))

        # ═══════════ F4 · AC-4.1 / 4.2 no ghost bubble on failure ═══════════
        print("\n=== F4 AC-4.1/4.2 : FakeAgent send fails -> no ghost bubble ===", flush=True)
        pg.evaluate("""() => {
            const s=document.getElementById('agent-select');
            const o=document.createElement('option');
            o.value='FakeAgent'; o.textContent='FakeAgent'; s.appendChild(o); s.value='FakeAgent';
        }""")
        before_cnt = user_bubble_count(pg)
        m41 = f"QA-AC41-ghost-{TS}"
        n_resp_before = len(resps)
        pg.fill("#message-input", m41)
        pg.click("#send-btn")
        pg.wait_for_timeout(1800)
        send_resp = [(s, u) for (t, s, u) in resps[n_resp_before:] if "/api/messages/send" in u]
        after_cnt = user_bubble_count(pg)
        ghost = bubbles_with(pg, m41)
        input_val = pg.evaluate("document.getElementById('message-input').value")
        got400 = any(s == 400 for s, u in send_resp)
        check("AC-4.1", "失败(400)后 user 气泡数不变 且无该内容气泡",
              got400 and after_cnt == before_cnt and ghost == 0,
              f"HTTP={[s for s, u in send_resp]} 前={before_cnt} 后={after_cnt} 该内容气泡={ghost}")
        check("AC-4.2", "失败后 #message-input 被清空", input_val == "", f"input.value={input_val!r}")
        pg.wait_for_timeout(3000)      # ensure no delayed ghost via poll
        check("AC-4.1-delay", "3s 后仍无幽灵气泡(轮询未回灌)", bubbles_with(pg, m41) == 0,
              f"延迟检查计数={bubbles_with(pg, m41)}")
        pg.screenshot(path=os.path.join(OUT, "qa_5_ghost.png"))

        # ═══════════ F6 · AC-6.1 / 6.2 dropdown dynamic refresh ═══════════
        print("\n=== F6 AC-6.1/6.2 : register NewAgent while page open (≤30s) ===", flush=True)
        pg.evaluate("() => { const s=document.getElementById('agent-select'); "
                    "[...s.options].filter(o=>o.value==='FakeAgent').forEach(o=>o.remove()); s.value='all'; }")
        opts_before = options(pg)
        print("register NewAgent:", api("POST", "/api/agents/register", {"name": "NewAgent"}), flush=True)
        t_reg = time.time()
        appeared = True
        try:
            pg.wait_for_function(
                "() => [...document.querySelectorAll('#agent-select option')].some(o=>o.value==='NewAgent')",
                timeout=35000)
        except Exception:
            appeared = False
        dt = time.time() - t_reg
        opts_after = options(pg)
        check("AC-6.1", "≤30s 内 #agent-select 出现 NewAgent 选项", appeared and dt <= 31,
              f"耗时 {dt:.1f}s; before={opts_before} after={opts_after}")
        check("AC-6.2", "既有 Agent 选项未丢失(before ⊆ after)",
              set(opts_before).issubset(set(opts_after)),
              f"before={opts_before} after={opts_after}")
        pg.screenshot(path=os.path.join(OUT, "qa_6_dropdown.png"))

        b.close()

    print("\n==== QA(user-angle) SUMMARY ====", flush=True)
    pn = sum(1 for *_x, c, _d in [(a, n, c, d) for a, n, c, d in results] if c)
    fn = len(results) - pn
    print(f"PASS={pn}  FAIL={fn}  TOTAL={len(results)}")
    for a, n, c, d in results:
        print(f"  [{'OK ' if c else 'XX '}] {a} {n} :: {d}")


if __name__ == "__main__":
    main()
