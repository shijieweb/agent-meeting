# -*- coding: utf-8 -*-
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
BASE = "http://127.0.0.1:8027"

def run(ua, label):
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 414, "height": 896}, user_agent=ua)
        pg = ctx.new_page()
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_selector("#message-list .msg-row", timeout=8000)
        time.sleep(3)
        pg.fill("#message-input", "probe")
        time.sleep(0.6)  # allow ime-top transition (350ms)
        info = pg.evaluate(
            "() => { const btn=document.getElementById('send-btn');"
            " const r=btn.getBoundingClientRect();"
            " const cx=r.x+r.width/2, cy=r.y+r.height/2;"
            " const top=document.elementFromPoint(cx,cy);"
            " const ia=document.querySelector('.input-area');"
            " const cs=getComputedStyle(ia);"
            " return { isMobile: IS_MOBILE, btnRect:{x:r.x,y:r.y,w:r.width,h:r.height},"
            "  cx, cy, topTag: top?top.tagName:'null', topId: top?top.id:'null',"
            "  topClass: top?top.className:'null',"
            "  iaPos: cs.position, iaTop: cs.top, imeTop: ia.classList.contains('ime-top'),"
            "  header: (()=>{const h=document.querySelector('.chat-header'); const hr=h.getBoundingClientRect();"
            "   return {y:hr.y,h:hr.height,bottom:hr.bottom};})() }; }")
        print("[" + label + "]", info)
        # real click
        pg.evaluate("() => { window.__clk=0; document.getElementById('send-btn').addEventListener('click',()=>window.__clk++); }")
        try:
            pg.click("#send-btn", timeout=4000)
            print("  [" + label + "] real-click OK")
        except Exception as e:
            print("  [" + label + "] real-click EXC:", str(e)[:120])
        print("  [" + label + "] __clk(real)=", pg.evaluate("()=>window.__clk"))
        # Enter key path
        pg.fill("#message-input", "probe2")
        try:
            pg.press("#message-input", "Enter", timeout=4000)
            print("  [" + label + "] Enter OK")
        except Exception as e:
            print("  [" + label + "] Enter EXC:", str(e)[:120])
        b.close()

# mobile UA
run("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", "MOBILE")
# desktop UA (IS_MOBILE false)
run("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36", "DESKTOP")
