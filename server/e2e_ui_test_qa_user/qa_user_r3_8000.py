# -*- coding: utf-8 -*-
"""QA round-3 (user-angle, browser) on SINGLE ENV 8000 — D-2 / EXT-2 / EXT-3 / regression.

Identity: qa_user. All created messages tagged '[TEST-DATA by qa_user]'.
Never calls cleanup endpoint. D-2 lost agent (qa_d2) was added to 8000 agents.json
from a pre-taken backup and will be restored afterwards (net-zero agent pollution).
"""
from playwright.sync_api import sync_playwright
import subprocess, json, time, os

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://localhost:8000"
TS = str(int(time.time()))
TAG = "[TEST-DATA by qa_user]"

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
    body, _, code = raw.rpartition("\n__CODE__")
    try:
        return json.loads(body), code.strip()
    except Exception:
        return {"_raw": body[:200]}, code.strip()


def dots(pg):
    return pg.evaluate("[...document.querySelectorAll('#agent-status .status-dot')].map(d=>d.textContent.trim())")


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        viewports = [{"w": 480, "h": 900, "label": "mobile"}, {"w": 1280, "h": 900, "label": "desktop"}]
        for vp in viewports:
            print(f"\n########## VIEWPORT {vp['label']} {vp['w']}x{vp['h']} ##########", flush=True)
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(4500)   # >1 loadAgentStatus cycle (3s)

            # ===== D-2: reawaken hint shows REAL lost agent name (not hardcoded 阿编) =====
            hint = pg.evaluate("""(() => {
                const h=document.getElementById('reawaken-hint');
                const n=document.getElementById('lost-agent-name');
                return {disp:getComputedStyle(h).display, full:h.textContent.trim(), lost:n?n.textContent.trim():null};
            })()""")
            dot_txt = dots(pg)
            d2_ok = (hint["disp"] != "none" and hint["lost"] == "qa_d2"
                     and "qa_d2" in hint["full"] and "阿编" not in hint["full"]
                     and any(d == "qa_d2·已掉线·需重唤" for d in dot_txt))
            check("D-2", f"[{vp['label']}] reawaken 显示真实名 qa_d2(非阿编)+dot 已掉线", d2_ok,
                  f"hint={hint} dots={dot_txt}")
            pg.screenshot(path=os.path.join(OUT, f"qa3_d2_{vp['label']}.png"))

            # ===== EXT-3: textarea auto-grow + cap 120 + reset on send =====
            base = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight, oy:cs.overflowY}; })()""")
            # 5 lines
            pg.evaluate(f"(t)=>{{const el=document.getElementById('message-input');el.value=t;el.dispatchEvent(new Event('input',{{bubbles:true}}));}}",
                        TAG + " EXT3 多行\n第二行\n第三行\n第四行\n第五行")
            pg.wait_for_timeout(300)
            mid = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight, oy:cs.overflowY}; })()""")
            grew = mid["h"] > base["h"] and mid["h"] <= 120 and mid["oy"] == "hidden"
            check("EXT-3", f"[{vp['label']}] 多行增高且≤120、未封顶时 overflowY=hidden", grew,
                  f"base={base['h']:.0f}px mid={mid['h']:.0f}px oy={mid['oy']}")
            # 12 lines -> cap 120 + internal scroll
            pg.evaluate(f"(t)=>{{const el=document.getElementById('message-input');el.value=t;el.dispatchEvent(new Event('input',{{bubbles:true}}));}}",
                        "\n".join([TAG + " EXT3 line %d" % i for i in range(1, 13)]))
            pg.wait_for_timeout(300)
            capped = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight, oy:cs.overflowY}; })()""")
            cap_ok = abs(capped["h"] - 120) < 1 and capped["oy"] == "auto" and capped["sh"] > 120
            check("EXT-3", f"[{vp['label']}] 封顶~120px 后内部滚动(overflowY=auto)", cap_ok,
                  f"h={capped['h']:.0f}px scrollH={capped['sh']}px oy={capped['oy']}")
            pg.screenshot(path=os.path.join(OUT, f"qa3_ext3_grown_{vp['label']}.png"))
            # send -> reset to 1 line
            pg.click("#send-btn")
            pg.wait_for_timeout(500)
            after = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {val:el.value, h:parseFloat(cs.height), sh:el.scrollHeight}; })()""")
            reset_ok = after["val"] == "" and abs(after["h"] - base["h"]) < 3
            check("EXT-3", f"[{vp['label']}] 发送后复位到1行(清空+高度复原)", reset_ok,
                  f"val={after['val']!r} h={after['h']:.0f}px base={base['h']:.0f}px")

            # ===== EXT-2: focus triggers scrollIntoView (keyboard-avoidance listener mounted) =====
            spy = pg.evaluate("""(() => { window.__siv=0; const o=Element.prototype.scrollIntoView;
                Element.prototype.scrollIntoView=function(...a){ window.__siv++; return o.apply(this,a); }; return true; })()""")
            pg.focus("#message-input")
            pg.wait_for_timeout(700)
            siv = pg.evaluate("() => window.__siv")
            check("EXT-2", f"[{vp['label']}] 聚焦触发 scrollIntoView(监听已挂载)", spy and siv >= 1,
                  f"scrollIntoView 调用次数={siv} (真机输入法 visualViewport 避让需老板点眼终验)")
            pg.screenshot(path=os.path.join(OUT, f"qa3_ext2_focus_{vp['label']}.png"))
            pg.close()

        b.close()

    # ===================== REGRESSION (API, 8000) =====================
    print("\n########## REGRESSION F2/F7/F8/F11/F12 (8000) ##########", flush=True)
    # F2 lock user
    _, c1 = api("POST", "/api/messages/send", {"sender_type": "agent", "content": "x", "target_type": "all"})
    _, c2 = api("POST", "/api/messages/send", {"sender_type": "admin", "content": "x", "target_type": "all"})
    d3, c3 = api("POST", "/api/messages/send", {"content": TAG + " F2 default user", "target_type": "all"})
    check("F2", "锁 user: agent/admin→422, 默认user→200", c1 == "422" and c2 == "422" and c3 == "200",
          f"agent={c1} admin={c2} user={c3}")

    # F7 set_session unregistered -> 400, no ghost
    _, c7 = api("POST", "/api/agents/NotRegisteredQA/session?active=false")
    lst7, _ = api("GET", "/api/agents")
    no_ghost = "NotRegisteredQA" not in lst7.get("agents", [])
    check("F7", "未注册 set_session→400 且无幽灵 agent", c7 == "400" and no_ghost,
          f"code={c7} agents={lst7.get('agents')}")

    # F8 soft protection: qa_d2 end -> 200 (not 4xx), has_unread field present
    d8, c8 = api("POST", "/api/agents/qa_d2/session?active=false")
    soft_ok = c8 == "200" and isinstance(d8, dict) and ("has_unread" in d8)
    check("F8", "软保护: end→200 且返回 has_unread(不阻断收工)", soft_ok,
          f"code={c8} body={json.dumps(d8, ensure_ascii=False)}")

    # F11 long reply (≥~500 chars) accepted, sender_type=agent
    longc = TAG + " F11 长回复 " + ("甲" * 500)
    d11, c11 = api("POST", "/api/messages/reply", {"agent_name": "qa_d2", "content": longc})
    hist11, _ = api("GET", "/api/messages/history?limit=200")
    msg11 = next((m for m in hist11.get("messages", []) if TAG + " F11" in (m.get("content") or "")), None)
    f11_ok = c11 == "200" and msg11 and len(msg11.get("content", "")) >= 500 and msg11.get("sender_type") == "agent"
    check("F11", "长回复~500字→200 且入库 sender_type=agent(红线不被拒)", f11_ok,
          f"code={c11} len={len(msg11['content']) if msg11 else 0} st={msg11.get('sender_type') if msg11 else None}")

    # F12 cleanup endpoint EXISTS but called with {} -> 400, NO deletion
    before_cnt = len(hist11.get("messages", []))
    d12, c12 = api("POST", "/api/messages/cleanup", {})
    hist12, _ = api("GET", "/api/messages/history?limit=200")
    after_cnt = len(hist12.get("messages", []))
    f12_ok = c12 == "400" and "archived" not in d12 and before_cnt == after_cnt
    check("F12", "cleanup 端点存在({}→400 且不删数据)", f12_ok,
          f"code={c12} body={json.dumps(d12, ensure_ascii=False)} before={before_cnt} after={after_cnt}")

    print("\n==== QA(user) round-3 8000 SUMMARY ====", flush=True)
    pn = sum(1 for *_x, c, _d in [(a, n, c, d) for a, n, c, d in results] if c)
    print(f"PASS={pn} FAIL={len(results)-pn} TOTAL={len(results)}")
    for a, n, c, d in results:
        print(f"  [{'OK ' if c else 'XX '}] {a} {n} :: {d}")


if __name__ == "__main__":
    main()
