# -*- coding: utf-8 -*-
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
BASE = "http://127.0.0.1:8027"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 414, "height": 896},
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                                   "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: print("  [pageerror] " + str(e)))
    pg.on("console", lambda m: print("  [c." + m.type + "] " + m.text[:160]))
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_selector("#message-list .msg-row", timeout=8000)
    time.sleep(3)

    info = pg.evaluate(
        "() => ({ hasSend: typeof sendMessage,"
        " hasBtn: !!document.getElementById('send-btn'),"
        " btnRect: (()=>{const r=document.getElementById('send-btn').getBoundingClientRect();"
        " return {x:r.x,y:r.y,w:r.width,h:r.height};})(),"
        " IS_MOBILE: (typeof IS_MOBILE!=='undefined')?IS_MOBILE:'?' })")
    print("INFO:", info)

    # install a click counter
    pg.evaluate("() => { window.__clk=0; document.getElementById('send-btn').addEventListener('click', ()=>{window.__clk++;}); }")
    pg.fill("#message-input", "probe")
    # real Playwright mouse click
    try:
        pg.click("#send-btn", timeout=4000)
        print("real-click: OK (no exception)")
    except Exception as e:
        print("real-click EXCEPTION:", str(e)[:200])
    time.sleep(0.5)
    clk_after_real = pg.evaluate("() => window.__clk")
    print("window.__clk after REAL click =", clk_after_real)

    # DOM .click()
    pg.evaluate("() => document.getElementById('send-btn').click()")
    time.sleep(0.5)
    clk_after_dom = pg.evaluate("() => window.__clk")
    print("window.__clk after DOM .click() =", clk_after_dom)

    # what was dispatched? check sendMessage reached fetch by observing request
    b.close()
