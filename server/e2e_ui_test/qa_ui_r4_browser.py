# -*- coding: utf-8 -*-
"""Round-4 READ-ONLY browser probe of live 8000 (qa_ui angle).
Loads the page (GET only) — never clicks send / never POSTs.
Verifies: textarea#message-input, #lost-agent-name, #reawaken-hint,
EXT-3 autoGrow cap at 120, no [TEST-DATA]/QAUI residue in rendered DOM.
"""
from playwright.sync_api import sync_playwright
import json, time

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
BASE = "http://127.0.0.1:8000"
out = {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
    pg = b.new_page(viewport={"width": 390, "height": 780})  # mobile-ish viewport
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append("CONSOLE:" + m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(3500)  # let loadAgentStatus run once

    out["message_input_tag"] = pg.evaluate("(()=>{const e=document.getElementById('message-input');return e?e.tagName:null;})()")
    out["has_lost_agent_name"] = pg.evaluate("!!document.getElementById('lost-agent-name')")
    out["has_reawaken_hint"] = pg.evaluate("!!document.getElementById('reawaken-hint')")
    # hint text when hidden (loadAgentStatus sets it to real name or 'AI' fallback)
    out["lost_name_text"] = pg.evaluate("(()=>{const e=document.getElementById('lost-agent-name');return e?e.textContent:null;})()")
    out["hint_display"] = pg.evaluate("(()=>{const e=document.getElementById('reawaken-hint');return e?getComputedStyle(e).display:null;})()")

    # EXT-3: type a long string into the textarea, trigger input -> height must cap at 120
    long_txt = "字" * 400
    pg.evaluate("""(t)=>{const e=document.getElementById('message-input');e.value=t;e.dispatchEvent(new Event('input',{bubbles:true}));}""", long_txt)
    pg.wait_for_timeout(200)
    grown = pg.evaluate("""(()=>{const e=document.getElementById('message-input');
        const cs=getComputedStyle(e);
        return {scrollHeight:e.scrollHeight, height:Math.round(parseFloat(cs.height)), overflowY:cs.overflowY, maxConst:120};})()""")
    out["ext3_grown"] = grown
    out["ext3_capped_ok"] = (grown["height"] <= 120) and (grown["scrollHeight"] > 120)
    # reset via clearing + autoGrow (simulate sendMessage reset path)
    pg.evaluate("""()=>{const e=document.getElementById('message-input');e.value='';if(typeof autoGrowInput==='function'){autoGrowInput();}else{e.dispatchEvent(new Event('input',{bubbles:true}));}}""")
    pg.wait_for_timeout(200)
    reset = pg.evaluate("(()=>{const e=document.getElementById('message-input');return {height:Math.round(parseFloat(getComputedStyle(e).height)), rows:e.rows};})()")
    out["ext3_reset"] = reset

    # EXT-2: setupKeyboardHandling ran without throwing? check listeners attached (focus handler exists)
    out["ext2_input_area_exists"] = pg.evaluate("!!document.querySelector('.input-area')")
    # verify focus path triggers scrollIntoView without error (call handler directly is not exposed;
    # instead confirm function defined in served bundle already ran on load -> no pageerror)
    out["page_errors"] = errors

    # Pollution: rendered message-list must not contain QA test markers
    dom_text = pg.evaluate("document.getElementById('message-list').innerText")
    out["rendered_has_testdata"] = ("QAUI" in dom_text) or ("[TEST-DATA]" in dom_text)
    out["rendered_message_count"] = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
    b.close()

print(json.dumps(out, ensure_ascii=False, indent=2))
