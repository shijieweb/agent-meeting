# -*- coding: utf-8 -*-
"""QA round-4 (user-angle, browser) on LIVE 8000 --reload.

Zero-pollution discipline: NO test messages / NO agent edits created.
All state-triggering items inject target state via request interception (page.route)
so the REAL frontend code path is exercised without any prod mutation.
 - EXT-1 : inject agent with has_unread=true, not working, not lost -> expect '处理任务'.
 - D-2   : inject agent in lost state -> expect reawaken shows real name (not hardcoded AI/阿编) + '开会'.
 - EXT-2/EXT-3 : live UI, no data created (reset exercised via input-clear = same code path as send).
"""
from playwright.sync_api import sync_playwright
import json, time, os, datetime

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://127.0.0.1:8000"

results = []


def check(ac, name, cond, detail=""):
    results.append((ac, name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {ac} {name} :: {detail}", flush=True)


def now_minus(sec):
    return (datetime.datetime.now() - datetime.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S")


MOCK_TASK = {"agents": [{"name": "DemoTask", "last_seen": now_minus(10), "status": "waiting", "session": True, "has_unread": True}]}
MOCK_LOST = {"agents": [{"name": "DemoLost", "last_seen": now_minus(700), "status": "working", "session": True, "has_unread": False}]}


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        for vp in [{"w": 480, "h": 900, "label": "mobile"}, {"w": 1280, "h": 900, "label": "desktop"}]:
            print(f"\n########## VIEWPORT {vp['label']} ##########", flush=True)

            # ---- EXT-1 via interception: unread + not working + not lost -> 处理任务 ----
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.route("**/api/agents/status", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(MOCK_TASK)))
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(4500)
            dots = pg.evaluate("[...document.querySelectorAll('#agent-status .status-dot')].map(d=>d.textContent.trim())")
            check("EXT-1", f"[{vp['label']}] 有未读非working agent 显示「处理任务」", any("处理任务" in d for d in dots), f"dots={dots}")
            pg.screenshot(path=os.path.join(OUT, f"qa4_ext1_{vp['label']}.png"))
            pg.close()

            # ---- D-2 via interception: lost state -> reawaken real name ----
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.route("**/api/agents/status", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(MOCK_LOST)))
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(4500)
            hint = pg.evaluate("""(() => { const h=document.getElementById('reawaken-hint');
                const n=document.getElementById('lost-agent-name');
                return {disp:getComputedStyle(h).display, full:h.textContent.trim(), lost:n?n.textContent.trim():null}; })()""")
            d2 = (hint["disp"] != "none" and hint["lost"] == "DemoLost"
                  and "DemoLost" in hint["full"] and "开会" in hint["full"]
                  and "阿编" not in hint["full"] and hint["lost"] != "AI")
            check("D-2", f"[{vp['label']}] 掉线提示显示真实名 DemoLost(非硬编码AI/阿编)+开会", d2, f"hint={hint}")
            pg.screenshot(path=os.path.join(OUT, f"qa4_d2_{vp['label']}.png"))
            pg.close()

            # ---- EXT-2 focus -> scrollIntoView ----
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)
            pg.evaluate("""(() => { window.__siv=0; const o=Element.prototype.scrollIntoView;
                Element.prototype.scrollIntoView=function(...a){ window.__siv++; return o.apply(this,a); }; })()""")
            pg.focus("#message-input")
            pg.wait_for_timeout(700)
            siv = pg.evaluate("() => window.__siv")
            check("EXT-2", f"[{vp['label']}] 聚焦触发 scrollIntoView(输入法避让监听挂载)", siv >= 1, f"scrollIntoView calls={siv}")
            pg.close()

            # ---- EXT-3 textarea grow + cap 120 + reset ----
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)
            base = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight}; })()""")
            # 3 lines -> should grow but stay under 120 (not capped)
            pg.evaluate("""(t)=>{const el=document.getElementById('message-input');el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));}""",
                        "第一行\n第二行\n第三行")
            pg.wait_for_timeout(300)
            mid = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight, oy:cs.overflowY}; })()""")
            grew = mid["h"] > base["h"] and mid["h"] <= 120 and mid["oy"] == "hidden"
            check("EXT-3", f"[{vp['label']}] 多行增高且≤120(未封顶 overflowY=hidden)", grew, f"base={base['h']:.0f}px mid={mid['h']:.0f}px oy={mid['oy']}")
            # 20 lines -> cap 120 + internal scroll
            pg.evaluate("""(t)=>{const el=document.getElementById('message-input');el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));}""",
                        "\n".join(["第%d行" % i for i in range(1, 21)]))
            pg.wait_for_timeout(300)
            capped = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {h:parseFloat(cs.height), sh:el.scrollHeight, oy:cs.overflowY}; })()""")
            cap_ok = abs(capped["h"] - 120) < 1 and capped["oy"] == "auto" and capped["sh"] > 120
            check("EXT-3", f"[{vp['label']}] 封顶~120px 后内部滚动(overflowY=auto)", cap_ok, f"h={capped['h']:.0f}px scrollH={capped['sh']}px oy={capped['oy']}")
            pg.screenshot(path=os.path.join(OUT, f"qa4_ext3_{vp['label']}.png"))
            # reset via input-clear (same code path sendMessage uses: input.value='' + autoGrowInput)
            pg.evaluate("""(() => { const el=document.getElementById('message-input'); el.value=''; el.dispatchEvent(new Event('input',{bubbles:true})); })()""")
            pg.wait_for_timeout(300)
            reset = pg.evaluate("""(() => { const el=document.getElementById('message-input');
                const cs=getComputedStyle(el); return {val:el.value, h:parseFloat(cs.height)}; })()""")
            check("EXT-3", f"[{vp['label']}] 清空后高度复位到1行(发送复位同路径)", reset["val"] == "" and abs(reset["h"] - base["h"]) < 3,
                  f"val={reset['val']!r} h={reset['h']:.0f}px base={base['h']:.0f}px")
            pg.close()

        b.close()

    print("\n==== QA(user) round-4 8000 SUMMARY ====", flush=True)
    pn = sum(1 for *_x, c, _d in [(a, n, c, d) for a, n, c, d in results] if c)
    print(f"PASS={pn} FAIL={len(results)-pn} TOTAL={len(results)}")
    for a, n, c, d in results:
        print(f"  [{'OK ' if c else 'XX '}] {a} {n} :: {d}")


if __name__ == "__main__":
    main()
