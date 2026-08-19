# -*- coding: utf-8 -*-
"""T-agent-meeting-state-persist 前端 e2e（隔离实例 8025 + test_data_state_persist）。

覆盖 AC-1.1/1.2/1.3、AC-2.1/2.3/2.4、AC-3.4/4.1/4.2：
- AC-1.1: 发送后 ≤500ms 气泡出现、输入框清空（路由延迟 800ms 证明不等待响应）
- AC-1.2: 轮询/刷新不重复追加（insertedIds + tempId→serverId 升级幂等）
- AC-1.3: 乐观渲染内容与最终落盘一致（升级后与刷新后文本不变）
- AC-2.1: 落盘后刷新仍可见
- AC-2.3: 发送失败 → 气泡保留 + 「发送失败」+ 重试成功 / 删除
- AC-2.4: 连续两条同内容均落盘（两次独立 client_msg_id）
- AC-3.4/4.1/4.2: init/end/lost/reactivated 灰色居中系统消息、刷新可见、不弹 banner、无未读徽标
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8025"
CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
DATA_DIR = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/test_data_state_persist"

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" | " + detail) if detail else ""))

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def wipe_test_data():
    import os, glob
    for f in glob.glob(os.path.join(DATA_DIR, "*")):
        try:
            os.remove(f)
        except OSError:
            pass
    print("test data wiped:", DATA_DIR)

def main():
    # ---- 0) 干净数据 ----
    wipe_test_data()
    # 重启服务由外部脚本负责；此处仅等健康检查
    for _ in range(30):
        try:
            s, _ = api("GET", "/health")
            if s == 200:
                break
        except Exception:
            time.sleep(0.5)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 390, "height": 780})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))   # 仅真实 JS 异常
        # console error 仅记录（资源 400 属 AC-2.3 故意制造，不计入 JS 错误）

        # 路由延迟 /send 800ms：证明 UI 不等响应（AC-1.1）
        def delay_send(route):
            time.sleep(0.8)
            route.continue_()
        pg.route("**/api/messages/send", delay_send)

        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(1500)

        # ---- 1) API 准备：注册 + init 一个 agent（产生 init 系统消息）----
        s, _ = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Bob"})
        check("api register Bob", s == 200, "status=" + str(s))
        s, _ = api("POST", "/api/agents/[TEST-DATA]%20Bob/session?active=true", None)
        check("api init Bob (system msg)", s == 200, "status=" + str(s))
        pg.wait_for_timeout(2500)  # 让 poll 拉到 init 系统消息

        # ---- 2) AC-1.1 / 1.2 / 1.3 ----
        pg.fill("#message-input", "[TEST-DATA] e2e hello")
        # 页内计时：btn.click() 同步派发 click → sendMessage 在首个 await(fetch) 前同步渲染 temp 气泡。
        # 排除了 Playwright click 的动作性等待开销，测量的是真实用户点击→气泡出现耗时（AC-1.1 ≤500ms）。
        timing = pg.evaluate("""() => {
          const t0 = performance.now();
          document.getElementById('send-btn').click();
          const el = document.querySelector('[data-id^="temp_"]');
          return el ? (performance.now() - t0) : -1;
        }""")
        check("AC-1.1 temp bubble <=500ms", timing >= 0 and timing <= 500, "elapsed=%.0fms" % timing)
        input_val = pg.input_value("#message-input")
        check("AC-1.1 input cleared immediately", input_val == "", "value=" + repr(input_val))

        # 升级替换：temp_ 消失（延迟 800ms 后响应到达）
        try:
            pg.wait_for_function("() => !document.querySelector('[data-id^=\"temp_\"]')", timeout=6000)
            check("AC-1.2 upgrade temp->server", True)
        except Exception as e:
            check("AC-1.2 upgrade temp->server", False, str(e))

        # 轮询 2 个周期后不重复（AC-1.2）
        pg.wait_for_timeout(4500)
        user_bubbles = pg.evaluate("document.querySelectorAll('.msg-bubble.user').length")
        check("AC-1.2 no duplicate after poll", user_bubbles == 1, "user_bubbles=%d" % user_bubbles)

        # 内容一致（AC-1.3）
        bubble_text = pg.evaluate("""(()=>{const el=document.querySelector('.msg-bubble.user');return el?el.textContent:null;})()""")
        check("AC-1.3 optimistic content matches", bubble_text == "[TEST-DATA] e2e hello", "text=" + repr(bubble_text))

        # ---- 3) AC-2.1：落盘后刷新仍可见 ----
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        after_refresh = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] e2e hello').length;})()""")
        check("AC-2.1 visible after refresh", after_refresh == 1, "count=%d" % after_refresh)

        # ---- 4) AC-2.3：失败标记 / 重试 / 删除 ----
        # 4a) 向不存在 agent 发 single → 失败标记
        pg.evaluate("""(()=>{const sel=document.getElementById('agent-select');const opt=document.createElement('option');opt.value='[TEST-DATA] Ghost';opt.textContent='[TEST-DATA] Ghost';sel.appendChild(opt);sel.value='[TEST-DATA] Ghost';})()""")
        pg.fill("#message-input", "[TEST-DATA] fail-me")
        pg.click("#send-btn")
        pg.wait_for_timeout(1200)  # 等 fetch 失败（400 立即返回）
        failed_bar = pg.evaluate("document.querySelectorAll('.msg-failed').length")
        failed_tip = pg.evaluate("""(()=>{const el=document.querySelector('.msg-failed-tip');return el?el.textContent:null;})()""")
        retained = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] fail-me').length;})()""")
        check("AC-2.3 failed bar shown", failed_bar == 1, "bars=%d" % failed_bar)
        check("AC-2.3 failed tip text", failed_tip == "⚠ 发送失败", "tip=" + repr(failed_tip))
        check("AC-2.3 bubble retained on failure", retained == 1, "retained=%d" % retained)

        # 4b) 注册 Ghost 后点重试 → 升级成功
        s, _ = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Ghost"})
        check("api register Ghost for retry", s == 200, "status=" + str(s))
        pg.click(".msg-failed-btn:has-text('重试')")
        try:
            pg.wait_for_function("() => document.querySelectorAll('.msg-failed').length === 0", timeout=6000)
            check("AC-2.3 retry success (fail bar gone)", True)
        except Exception as e:
            check("AC-2.3 retry success (fail bar gone)", False, str(e))
        # 重试后同一内容仍只有 1 条（client_msg_id 幂等，未重复）
        after_retry = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] fail-me').length;})()""")
        check("AC-2.3 retry no duplicate", after_retry == 1, "count=%d" % after_retry)

        # 4c) 再发一条失败 → 点删除 → 气泡移除
        pg.evaluate("""(()=>{const sel=document.getElementById('agent-select');const opt=document.createElement('option');opt.value='[TEST-DATA] Ghost2';opt.textContent='[TEST-DATA] Ghost2';sel.appendChild(opt);sel.value='[TEST-DATA] Ghost2';})()""")
        pg.fill("#message-input", "[TEST-DATA] fail-me-2")
        pg.click("#send-btn")
        pg.wait_for_timeout(1200)
        retained2 = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] fail-me-2').length;})()""")
        check("AC-2.3 second failure retained", retained2 == 1, "retained=%d" % retained2)
        pg.click(".msg-failed-btn:has-text('删除')")
        pg.wait_for_timeout(300)
        after_del = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] fail-me-2').length;})()""")
        check("AC-2.3 delete removes local bubble", after_del == 0, "count=%d" % after_del)

        # ---- 5) AC-2.4：连续两条同内容均落盘 ----
        pg.evaluate("""(()=>{const sel=document.getElementById('agent-select');sel.value='all';})()""")
        pg.fill("#message-input", "[TEST-DATA] same")
        pg.click("#send-btn")
        pg.fill("#message-input", "[TEST-DATA] same")
        pg.click("#send-btn")
        pg.wait_for_timeout(2500)  # 两条都升级
        same_bubbles = pg.evaluate("""(()=>{const els=[...document.querySelectorAll('.msg-bubble.user')];return els.filter(e=>e.textContent==='[TEST-DATA] same').length;})()""")
        check("AC-2.4 two same-content messages", same_bubbles == 2, "count=%d" % same_bubbles)
        # 服务端 messages.json 也有两条
        with open(DATA_DIR + "/messages.json", encoding="utf-8") as f:
            msgs = json.load(f)
        same_persisted = [m for m in msgs if m.get("content") == "[TEST-DATA] same"]
        check("AC-2.4 both persisted server-side", len(same_persisted) == 2, "persisted=%d" % len(same_persisted))

        # ---- 6) AC-4.2：系统消息不弹 banner（页面置顶，poll 只带回系统消息）----
        pg.evaluate("document.getElementById('message-list').scrollTop = 0")
        pg.wait_for_timeout(200)
        s, _ = api("POST", "/api/agents/register", {"name": "[TEST-DATA] Dave"})
        s, _ = api("POST", "/api/agents/[TEST-DATA]%20Dave/session?active=true", None)
        pg.wait_for_timeout(2500)  # poll 拉到 Dave init 系统消息
        banner_display = pg.evaluate("""(()=>{const el=document.getElementById('new-msg-banner');return el?getComputedStyle(el).display:null;})()""")
        sys_notices = pg.evaluate("document.querySelectorAll('.sys-notice').length")
        check("AC-4.2 no banner for system msg", banner_display != "block", "banner_display=" + repr(banner_display))
        check("AC-4.2 system notice rendered", sys_notices >= 1, "sys_notices=%d" % sys_notices)
        # 灰色居中样式
        sys_style = pg.evaluate("""(()=>{const el=document.querySelector('.sys-notice');if(!el)return null;const cs=getComputedStyle(el);return {color:cs.color,textAlign:cs.textAlign,alignSelf:cs.alignSelf};})()""")
        check("AC-4.2 sys-notice gray centered", bool(sys_style) and sys_style["color"] == "rgb(153, 153, 153)" and sys_style["textAlign"] == "center", "style=" + repr(sys_style))
        # 系统消息无未读徽标（无 read-status 节点）
        sys_badges = pg.evaluate("""(()=>{const list=document.getElementById('message-list');let bad=0;list.querySelectorAll('.sys-notice').forEach(n=>{if(n.nextElementSibling&&n.nextElementSibling.classList.contains('read-status'))bad++;});return bad;})()""")
        check("AC-4.1 system msg no read badge", sys_badges == 0, "badges=%d" % sys_badges)

        # ---- 7) AC-3.4/4.1：刷新后系统消息仍可见 ----
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        sys_after_refresh = pg.evaluate("document.querySelectorAll('.sys-notice').length")
        check("AC-3.4 system msgs visible after refresh", sys_after_refresh >= 1, "sys_notices=%d" % sys_after_refresh)

        # ---- 8) 页面无 JS 错误 ----
        check("no page JS errors", len(errors) == 0, "; ".join(errors[:3]))

        pg.screenshot(path=DATA_DIR + "/../e2e_state_persist_final.png")
        b.close()

    # ---- 汇总 ----
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n==== e2e SUMMARY: %d passed, %d failed ====" % (passed, failed))
    for name, ok, detail in results:
        if not ok:
            print("  FAILED: " + name + (" | " + detail if detail else ""))
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
