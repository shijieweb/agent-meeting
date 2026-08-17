from playwright.sync_api import sync_playwright
import subprocess, json, time, os

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test"
BASE = "http://localhost:8000"
TS = str(int(time.time()))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} :: {detail}")

def api(method, path, payload=None):
    url = BASE + path
    cmd = ["curl.exe", "-s", "-m", "10"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(payload, ensure_ascii=False)]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"_raw": r.stdout[:200]}

def last_user_read_status(pg):
    return pg.evaluate("""(() => {
        const all=[...document.querySelectorAll('#message-list .message-bubble.user')];
        if(!all.length) return null;
        const last=all[all.length-1];
        let n=last.nextElementSibling;
        while(n && !n.classList.contains('read-status')) n=n.nextElementSibling;
        return n ? n.textContent.trim() : null;
    })()""")

def find_msg_by_content(content):
    d = api("GET", "/api/messages/history?limit=10000")
    for m in d.get("messages", []):
        if content in (m.get("content") or ""):
            return m
    return None

def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width":480,"height":900})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3500)

        # ── T1 页面加载 ──
        title = pg.title()
        bubbles = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        agents = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
        check("T1 标题=Agent Hub", title == "Agent Hub", title)
        check("T1 首屏渲染30条", bubbles == 30, f"bubbles={bubbles}")
        # 动态匹配：下拉=[@所有人]+活跃agent（不再硬编码占位名，防僵尸过滤后误判）
        real = api("GET", "/api/agents")
        expected = ["all"] + real.get("agents", [])
        check("T1 agent下拉动态匹配活跃agent", agents == expected, f"got={agents} expected={expected}")
        pg.screenshot(path=os.path.join(OUT,"shot_1_load.png"))

        # ── T2 发送消息 + 初始○未读（先选 single 目标，验证 single 路径刷新 bug）──
        pg.select_option("#agent-select", "WorkBuddy")
        t2 = "【E2E测试】发送功能验证 " + TS
        pg.fill("#message-input", t2)
        pg.click("#send-btn")
        pg.wait_for_timeout(900)
        bubbles2 = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        st2 = last_user_read_status(pg)
        check("T2 发送后新增气泡", bubbles2 == 31, f"bubbles={bubbles2}")
        check("T2 初始状态=○未读(single)", st2 is not None and "未读" in st2 and "已读" not in st2, str(st2))
        m2 = find_msg_by_content(t2)
        check("T2 后端落库+read_by空+target=WorkBuddy", m2 is not None and (m2.get("read_by") or []) == [] and m2.get("target_agent_name")=="WorkBuddy",
              f"id={m2.get('id') if m2 else None} read_by={m2.get('read_by') if m2 else None} target={m2.get('target_agent_name') if m2 else None}")
        pg.screenshot(path=os.path.join(OUT,"shot_2_sent.png"))

        # ── T3 已读回执刷新（核心 bug 修复）──
        api("GET", "/api/messages/pull?agent_name=WorkBuddy")  # 模拟本人读取 → 后端持久化已读
        pg.wait_for_timeout(6500)  # > 5s 刷新周期，确保 refreshReadReceipts 跑到
        st3 = last_user_read_status(pg)
        check("T3 界面翻✓已读", st3 is not None and "已读" in st3 and "未读" not in st3, str(st3))
        m3 = find_msg_by_content(t2)
        check("T3 后端read_by含WorkBuddy", m3 is not None and "WorkBuddy" in (m3.get("read_by") or []), f"read_by={m3.get('read_by') if m3 else None}")
        pg.screenshot(path=os.path.join(OUT,"shot_3_read.png"))

        # ── T4 增量加载 loadOlder + scrollTop 补偿 ──
        pg.evaluate("document.getElementById('message-list').scrollTop = 0")
        pg.wait_for_timeout(400)    # 触发 onListScroll → loadOlder（异步 fetch）
        pg.wait_for_timeout(2200)   # 等 loadOlder 完成
        bubbles4 = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        st4 = pg.evaluate("document.getElementById('message-list').scrollTop")
        check("T4 触顶加载更早消息", bubbles4 > 30, f"bubbles={bubbles4}")
        check("T4 scrollTop补偿(锚定不跳0)", st4 > 0, f"scrollTop={st4}")
        pg.screenshot(path=os.path.join(OUT,"shot_4_older.png"))

        # ── T5 浮动提示出现 + 1000ms消失 + 点击跳底 ──
        pg.evaluate("document.getElementById('message-list').scrollTop = 0")  # 置于顶部(非底部)
        pg.wait_for_timeout(400)
        t5 = "【E2E测试】浮动提示验证 " + TS
        api("POST", "/api/messages/send", {
            "sender_type":"user","content":t5,"target_type":"single","target_agent_name":"WorkBuddy"})
        # 主动轮询捕获 banner 出现（出现后仅 ~1s 可见，必须主动等待窗口）
        appeared = True
        try:
            pg.wait_for_function("() => { const b=document.getElementById('new-msg-banner'); return b && b.style.display!=='none'; }", timeout=4000)
        except Exception:
            appeared = False
        banner_text = pg.evaluate("(()=>{const b=document.getElementById('new-msg-banner');return b?b.textContent:null;})()")
        check("T5 浮动提示出现", appeared, str(banner_text))
        pg.screenshot(path=os.path.join(OUT,"shot_5_banner.png"))
        # 等待 1000ms 自动消失
        disappeared = True
        try:
            pg.wait_for_function("() => { const b=document.getElementById('new-msg-banner'); return !b || b.style.display==='none'; }", timeout=2000)
        except Exception:
            disappeared = False
        check("T5 1000ms自动消失", disappeared)
        # 重新触发一条并立即点击跳底
        t5b = "【E2E测试】跳底验证 " + TS
        api("POST", "/api/messages/send", {
            "sender_type":"user","content":t5b,"target_type":"single","target_agent_name":"WorkBuddy"})
        clicked = False
        try:
            pg.wait_for_function("() => { const b=document.getElementById('new-msg-banner'); return b && b.style.display!=='none'; }", timeout=4000)
            clicked = pg.evaluate("(()=>{const b=document.getElementById('new-msg-banner');if(b&&b.style.display!=='none'){b.click();return true;}return false;})()")
        except Exception:
            pass
        pg.wait_for_timeout(500)
        at_bottom = pg.evaluate("(()=>{const l=document.getElementById('message-list');return l.scrollTop+l.clientHeight>=l.scrollHeight-4;})()")
        check("T5 点击banner跳到底部", clicked and at_bottom, f"clicked={clicked} atBottom={at_bottom}")
        pg.screenshot(path=os.path.join(OUT,"shot_5_jump.png"))

        # ── T6 动态注册并激活 agent，验证下拉选型驱动发送目标（不再硬编码 AgentX）──
        api("POST", "/api/agents/register", {"name": "E2EAgent"})
        api("GET", "/api/messages/pull?agent_name=E2EAgent")  # 激活（推进 last_seen，脱离僵尸）
        pg.reload(wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3500)
        pg.select_option("#agent-select", "E2EAgent")
        t6 = "【E2E测试】选E2EAgent发送 " + TS
        pg.fill("#message-input", t6)
        pg.click("#send-btn")
        pg.wait_for_timeout(700)
        m6 = find_msg_by_content(t6)
        check("T6 选E2EAgent发送→target_agent_name=E2EAgent", m6 is not None and m6.get("target_agent_name")=="E2EAgent" and m6.get("target_type")=="single",
              f"target={m6.get('target_agent_name') if m6 else None} type={m6.get('target_type') if m6 else None}")
        pg.screenshot(path=os.path.join(OUT,"shot_6_agent.png"))

        b.close()

    # 汇总
    print("\n==== E2E SUMMARY ====")
    pn = sum(1 for _,c,_ in results if c)
    fn = sum(1 for _,c,_ in results if not c)
    print(f"PASS={pn}  FAIL={fn}  TOTAL={len(results)}")
    for n,c,d in results:
        print(f"  [{'OK ' if c else 'XX '}] {n} :: {d}")

if __name__ == "__main__":
    main()
