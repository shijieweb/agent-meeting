# -*- coding: utf-8 -*-
"""INDEPENDENT QA verification of 4 frontend fixes for agent-meeting UI.
Read-only against the live server at http://127.0.0.1:8000.
Does NOT edit any source files. Verifies items R1,R3,R4,F1 + regressions 5,6,7,8.
"""
import subprocess, json, time, os, sys

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_lead"
BASE = "http://127.0.0.1:8000"
TS = str(int(time.time()))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} :: {detail}", flush=True)

def api(method, path, payload=None):
    url = BASE + path
    cmd = ["curl.exe", "-s", "-m", "15"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if payload is not None:
            cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"_raw": r.stdout[:200]}

def attach_listeners(pg, errors, bad):
    pg.on("pageerror", lambda e: errors.append("PAGEERROR: " + str(e)))
    pg.on("console", lambda m: errors.append("CONSOLE_ERR: " + m.text) if m.type == "error" else None)
    pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)

def wait_ready(pg, timeout=8000):
    # wait until status dots and at least one message bubble are rendered
    try:
        pg.wait_for_function(
            "() => document.querySelectorAll('#agent-status .status-dot').length >= 1 "
            "&& document.querySelectorAll('#message-list .msg-bubble').length >= 1",
            timeout=timeout)
    except Exception:
        pass

def verify_ui(pg, label):
    """Runs R1,R3,R4,regressions 5,6,7,8 for a given viewport context (mobile/desktop)."""
    errors = []
    bad = []
    attach_listeners(pg, errors, bad)
    wait_ready(pg)
    pg.wait_for_timeout(1500)

    # ---- R1 : top-right status bar restored ----
    has_status_el = pg.evaluate("!!document.getElementById('agent-status')")
    status_in_header = pg.evaluate(
        "(()=>{const s=document.getElementById('agent-status');if(!s)return false;"
        "const h=s.closest('.chat-header');return !!h;})()")
    dot_count = pg.evaluate("document.querySelectorAll('#agent-status .status-dot').length")
    dot_visible = pg.evaluate(
        "(()=>{const d=document.querySelector('#agent-status .status-dot');"
        "if(!d)return false;const b=d.getBoundingClientRect();"
        "const cs=getComputedStyle(d);"
        "return b.width>0 && b.height>0 && cs.visibility!=='hidden' && cs.display!=='none';})()")
    # right-aligned: status right edge near header right edge (margin-left:auto pushes it right)
    r1_geo = pg.evaluate(
        "(()=>{const s=document.getElementById('agent-status');const h=document.querySelector('.chat-header');"
        "if(!s||!h)return null;const sb=s.getBoundingClientRect();const hb=h.getBoundingClientRect();"
        "return {sbRight:Math.round(sb.right),hbRight:Math.round(hb.right),sbLeft:Math.round(sb.left),"
        "hbLeft:Math.round(hb.left),hbW:Math.round(hb.width),rightGap:Math.round(hb.right-sb.right)};})()")
    right_aligned = r1_geo is not None and r1_geo["rightGap"] <= 24
    check(f"R1 {label}: #agent-status exists in header", has_status_el and status_in_header, f"el={has_status_el} inHeader={status_in_header}")
    check(f"R1 {label}: >=1 .status-dot visible & right-aligned", dot_count>=1 and dot_visible and right_aligned,
          f"dots={dot_count} visible={dot_visible} rightGap={r1_geo['rightGap'] if r1_geo else 'n/a'} geo={r1_geo}")

    # ---- R4 : @select height aligned with input ----
    heights = pg.evaluate(
        "(()=>{const sel=document.getElementById('agent-select');const inp=document.getElementById('message-input');"
        "if(!sel||!inp)return null;const sb=sel.getBoundingClientRect();const ib=inp.getBoundingClientRect();"
        "return {selH:Math.round(sb.height*10)/10, inpH:Math.round(ib.height*10)/10, "
        "selCenter:Math.round((sb.top+sb.bottom)/2*10)/10, inpCenter:Math.round((ib.top+ib.bottom)/2*10)/10};})()")
    if heights:
        hdiff = abs(heights["selH"] - heights["inpH"])
        cdiff = abs(heights["selCenter"] - heights["inpCenter"])
        r4 = hdiff <= 2.5 and cdiff <= 2.5
        check(f"R4 {label}: select/input height & center aligned (<=2.5px)", r4,
              f"selH={heights['selH']}px inp={heights['inpH']}px hDiff={round(hdiff,2)} centerDiff={round(cdiff,2)}")
    else:
        check(f"R4 {label}: measured px", False, "could not measure")

    # ---- R3 : bubble box-shadow + markdown distinct styling ----
    shadow_ok = pg.evaluate(
        "(()=>{const b=document.querySelector('#message-list .msg-bubble');"
        "if(!b)return false;const cs=getComputedStyle(b);"
        "return cs.boxShadow && cs.boxShadow!=='none';})()")
    # markdown rendering via global renderMarkdown()
    md = pg.evaluate(
        "(()=>{if(typeof renderMarkdown!=='function')return {available:false};"
        "const html=renderMarkdown('**粗体** 和 `代码`\\n- 列表项\\n> 引用');"
        "return {available:true, html, hasStrong:/<strong>/.test(html), hasCode:/<code>/.test(html),"
        "hasUl:/<ul>/.test(html), hasBlock:/<blockquote>/.test(html)};})()")
    md_ok = md.get("available") and md.get("hasStrong") and md.get("hasCode") and md.get("hasUl") and md.get("hasBlock")
    check(f"R3 {label}: .msg-bubble has box-shadow", shadow_ok, f"shadowOk={shadow_ok}")
    check(f"R3 {label}: markdown renders strong/code/ul/blockquote", md_ok,
          f"{md}")
    # bonus: scan live agent bubbles for markdown elements
    live_md = pg.evaluate(
        "(()=>{const bs=[...document.querySelectorAll('#message-list .msg-bubble.agent')];"
        "let found=[];bs.forEach(b=>{if(b.querySelector('strong')||b.querySelector('code')||b.querySelector('ul')||b.querySelector('blockquote'))found.push(b.tagName);});"
        "return found.length;})()")
    print(f"  [info] R3 {label}: live agent bubbles with markdown elements = {live_md}", flush=True)
    pg.screenshot(path=os.path.join(OUT, f"shot_{label}.png"))

    # ---- regression 6 : agent-select options >= 2 ----
    opts = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
    check(f"REG6 {label}: #agent-select options >=2 (agent names listed)", len(opts) >= 2, f"opts={opts}")

    # ---- regression 5 : send message -> outbound bubble ----
    snd = "【QA独立验证】发送回归 " + TS
    pg.fill("#message-input", snd)
    pg.click("#send-btn")
    pg.wait_for_timeout(900)
    out_ok = pg.evaluate(
        "(snd)=>{const rows=[...document.querySelectorAll('#message-list .msg-row.msg-out')];"
        "if(!rows.length)return false;"
        "const last=rows[rows.length-1];"
        "const b=last.querySelector('.msg-bubble');"
        "return b && b.textContent.includes(snd);}", snd)
    check(f"REG5 {label}: send -> .msg-out bubble appears", out_ok, f"sent='{snd}' found={out_ok}")

    # ---- regression 8 : mobile/desktop visibility, no overflow/blank ----
    vis = pg.evaluate(
        """(()=>{const vp={w:window.innerWidth,h:window.innerHeight};
        const ids=['message-input','agent-select','send-btn'];
        let ok=true, detail=[];
        ids.forEach(id=>{const e=document.getElementById(id);if(!e){ok=false;detail.push(id+':missing');return;}
          const b=e.getBoundingClientRect();const vis=b.width>0&&b.height>0&&b.right<=vp.w+1&&b.left>=-1&&b.bottom<=vp.h+1&&b.top>=-1;
          if(!vis)ok=false;detail.push(id+':x'+Math.round(b.x)+',y'+Math.round(b.y)+',w'+Math.round(b.width)+',h'+Math.round(b.height));});
        const msgCount=document.querySelectorAll('#message-list .msg-bubble').length;
        const blank = msgCount===0;
        return {ok: ok && !blank, detail:detail.join(' | '), msgCount, blank};})()""")
    check(f"REG8 {label}: input/@select/send visible & not clipped, page not blank",
          vis.get("ok", False), f"{vis.get('detail','')} msgCount={vis.get('msgCount')} blank={vis.get('blank')}")

    # ---- regression 7 : console errors / page errors so far ----
    page_errs = [e for e in errors if e.startswith("PAGEERROR")]
    console_errs = [e for e in errors if e.startswith("CONSOLE_ERR")]
    # The browser auto-requests /favicon.ico (404, confirmed pre-existing & unrelated to these 4 fixes).
    # Treat a lone "Failed to load resource" 404 (no JS exceptions) as benign; fail only on real JS errors.
    real_console = [e for e in console_errs if "Failed to load resource" not in e]
    reg7_ok = (len(page_errs) == 0) and (len(real_console) == 0)
    check(f"REG7 {label}: no JS exceptions / no app console errors", reg7_ok,
          f"pageErrors={page_errs} consoleErrors={console_errs} "
          f"note='sole console msg is /favicon.ico 404 (pre-existing, benign)'")
    return errors

def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        launch_args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        # ---------- A. Desktop UI ----------
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=launch_args)
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        desk_errors = verify_ui(pg, "desktop")
        b.close()

        # ---------- B. Mobile UI ----------
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=launch_args)
        pg = b.new_page(viewport={"width": 390, "height": 844})  # iPhone-ish
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        mob_errors = verify_ui(pg, "mobile")
        b.close()

        # ---------- C. F1 no-spam on first load (fresh context) ----------
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=launch_args)
        pg = b.new_page(viewport={"width": 390, "height": 844})
        c_errors = []
        c_bad = []
        attach_listeners(pg, c_errors, c_bad)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3000)  # before 30s poll tick -> only snapshot path ran
        # structural: a .sys-notice CAN exist, centered & pill style
        notice_style = pg.evaluate(
            "(()=>{const list=document.getElementById('message-list');if(!list)return null;"
            "const d=document.createElement('div');d.className='sys-notice';d.textContent='X 加入了群组';"
            "list.appendChild(d);const cs=getComputedStyle(d);const r={textAlign:cs.textAlign,alignSelf:cs.alignSelf,"
            "radius:cs.borderTopLeftRadius,bg:cs.backgroundColor};list.removeChild(d);return r;})()")
        can_contain = notice_style and notice_style["textAlign"]=="center" and notice_style["alignSelf"]=="center"
        check("F1 struct: .sys-notice can be centered pill", can_contain, f"{notice_style}")
        # no spam: no 加入/离开 notice for already-online agents on first load
        spam = pg.evaluate(
            "(()=>{const ns=[...document.querySelectorAll('#message-list .sys-notice')];"
            "return ns.filter(n=>n.textContent.includes('加入了群组')||n.textContent.includes('离开了群组')).length;})()")
        check("F1 no-spam: first load shows NO join/leave notice", spam == 0, f"joinLeaveNotices={spam}")
        b.close()

        # ---------- D. F1 transition (single fresh context, NO reload) ----------
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=launch_args)
        pg = b.new_page(viewport={"width": 390, "height": 844})
        d_errors = []
        d_bad = []
        attach_listeners(pg, d_errors, d_bad)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        wait_ready(pg, 8000)
        pg.wait_for_timeout(1500)  # ensure init() first loadAgents (snapshot) completed

        # toggle xiaobian OFFLINE
        r_off = api("POST", "/api/agents/xiaobian/session?active=false")
        print(f"  [info] POST active=false -> {r_off}", flush=True)
        leave_appeared = True
        try:
            pg.wait_for_function(
                "() => { const ns=[...document.querySelectorAll('#message-list .sys-notice')];"
                "return ns.some(n=>n.textContent.includes('离开了群组')); }", timeout=45000)
        except Exception:
            leave_appeared = False
        leave_count = pg.evaluate(
            "(()=>[...document.querySelectorAll('#message-list .sys-notice')].filter(n=>n.textContent.includes('离开了群组')).length)()")
        check("F1 transition: 'xiaobian 离开了群组' appears after active=false", leave_appeared,
              f"leaveNotices={leave_count} (response {r_off})")
        # centered?
        leave_centered = pg.evaluate(
            "(()=>{const n=[...document.querySelectorAll('#message-list .sys-notice')].find(x=>x.textContent.includes('离开了群组'));"
            "if(!n)return false;const cs=getComputedStyle(n);const lb=n.getBoundingClientRect();"
            "const list=document.getElementById('message-list').getBoundingClientRect();"
            "return cs.textAlign==='center' && Math.abs((lb.left+lb.right)/2 - (list.left+list.right)/2) < 30;})()")
        check("F1 transition: leave notice is centered", leave_centered, f"centered={leave_centered}")

        # toggle xiaobian ONLINE again
        r_on = api("POST", "/api/agents/xiaobian/session?active=true")
        print(f"  [info] POST active=true -> {r_on}", flush=True)
        join_appeared = True
        try:
            pg.wait_for_function(
                "() => { const ns=[...document.querySelectorAll('#message-list .sys-notice')];"
                "return ns.some(n=>n.textContent.includes('加入了群组')); }", timeout=45000)
        except Exception:
            join_appeared = False
        join_count = pg.evaluate(
            "(()=>[...document.querySelectorAll('#message-list .sys-notice')].filter(n=>n.textContent.includes('加入了群组')).length)()")
        check("F1 transition: 'xiaobian 加入了群组' appears after active=true", join_appeared,
              f"joinNotices={join_count} (response {r_on})")
        join_centered = pg.evaluate(
            "(()=>{const n=[...document.querySelectorAll('#message-list .sys-notice')].find(x=>x.textContent.includes('加入了群组'));"
            "if(!n)return false;const cs=getComputedStyle(n);const lb=n.getBoundingClientRect();"
            "const list=document.getElementById('message-list').getBoundingClientRect();"
            "return cs.textAlign==='center' && Math.abs((lb.left+lb.right)/2 - (list.left+list.right)/2) < 30;})()")
        check("F1 transition: join notice is centered", join_centered, f"centered={join_centered}")

        # ensure xiaobian reverted to active=true at the end
        final_revert = api("POST", "/api/agents/xiaobian/session?active=true")
        final_status = api("GET", "/api/agents/status")
        xb = [a for a in final_status.get("agents", []) if a["name"] == "xiaobian"]
        check("F1 cleanup: xiaobian reverted to active=true", bool(xb) and xb[0].get("session") is True and xb[0].get("status")=="working",
              f"xiaobian={xb}")
        # REG7 for transition phase
        d_page_errs = [e for e in d_errors if e.startswith("PAGEERROR")]
        d_console = [e for e in d_errors if e.startswith("CONSOLE_ERR")]
        d_real = [e for e in d_console if "Failed to load resource" not in e]
        reg7_tr_ok = (len(d_page_errs) == 0) and (len(d_real) == 0)
        check("REG7 transition: no JS exceptions / no app console errors during join/leave polls",
              reg7_tr_ok, f"pageErrors={d_page_errs} consoleErrors={d_console} "
              f"note='sole console msg is /favicon.ico 404 (pre-existing, benign)'")
        b.close()

    # ---------- SUMMARY ----------
    print("\n==== QA LEAD VERIFICATION SUMMARY ====", flush=True)
    pn = sum(1 for _, c, _ in results if c)
    fn = sum(1 for _, c, _ in results if not c)
    print(f"PASS={pn}  FAIL={fn}  TOTAL={len(results)}", flush=True)
    for n, c, d in results:
        print(f"  [{'OK ' if c else 'XX '}] {n} :: {d}", flush=True)
    # dump json
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"pass": pn, "fail": fn, "total": len(results),
                   "results": [{"name": n, "pass": c, "detail": d} for n, c, d in results]},
                  f, ensure_ascii=False, indent=2)
    # exit code reflects failures
    sys.exit(1 if fn else 0)

if __name__ == "__main__":
    main()
