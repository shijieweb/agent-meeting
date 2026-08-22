# -*- coding: utf-8 -*-
"""T-collab-01: Agent 协作基础能力 · 隔离测试（每个AC独立进程）。

AC-1: 新字段回退兼容 (role/capabilities/team)
AC-2: target_type=all 路由
AC-3: workflow 关联过滤

运行：python tests_storage_v6/test_collab_01.py
"""
import json
import os
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label} — {detail}")


def run_ac_test(ac_num, setup_sql, test_code):
    """运行独立AC测试"""
    iso_dir = tempfile.mkdtemp(prefix=f"ac{ac_num}_")

    # 创建测试脚本
    script = Path(__file__).parent / f"_ac{ac_num}_test.py"
    script.write_text(f'''
import sys, os, json, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path("{Path(__file__).parent.parent.absolute()}").absolute()))

os.environ["DATA_DIR"] = "{iso_dir}"
os.environ["SWEEP_INTERVAL"] = "0"

# 初始化DB
db_path = Path("{iso_dir}") / "agent_meeting.db"
conn = sqlite3.connect(str(db_path))
c = conn.cursor()
c.execute("""CREATE TABLE agents (name TEXT PRIMARY KEY, registered_at TEXT, last_seen TEXT, status TEXT, session INTEGER, token_hash TEXT, description TEXT, read_scope TEXT, role TEXT, capabilities TEXT, team TEXT)""")
c.execute("""CREATE TABLE messages (id TEXT PRIMARY KEY, seq INTEGER, content TEXT, sender_type TEXT, sender_agent_name TEXT, target_type TEXT, target_agent_name TEXT, reply_to_message_id TEXT, visible INTEGER, message_type TEXT, event TEXT, created_at TEXT, client_msg_id TEXT, workflow_id TEXT)""")
c.execute("""CREATE TABLE reads (message_id TEXT, agent_name TEXT, read_at TEXT, PRIMARY KEY (message_id, agent_name))""")
c.execute("""CREATE TABLE agent_read (agent_name TEXT, message_id TEXT, PRIMARY KEY (agent_name, message_id))""")
c.execute("""CREATE TABLE agent_read_init (agent_name TEXT PRIMARY KEY)""")
{setup_sql}
conn.commit()
conn.close()

# 执行测试代码
os.environ["DATA_DIR"] = "{iso_dir}"
from app.services import agent_store, message_store
{test_code}
''', encoding='utf-8')

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=30
    )
    
    shutil.rmtree(iso_dir, ignore_errors=True)
    script.unlink(missing_ok=True)
    
    return result.stdout.strip(), result.returncode


def test_ac1():
    """AC-1: 新字段回退兼容"""
    print("\n=== AC-1: 新字段回退兼容 ===")
    
    setup = '''
c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("old-agent", "2026-08-20T10:00:00", "2026-08-20T10:00:00", "waiting", 0, None, "老agent", "all", None, None, None))
c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("pm-agent", "2026-08-20T10:01:00", "2026-08-20T10:01:00", "waiting", 0, None, "产品经理", "all", "pm", "[\\"write_prd\\"]", "project-alpha"))
'''
    
    code = '''
agents = agent_store.manage_list()
by_name = {a["name"]: a for a in agents}
old = by_name.get("old-agent")
pm = by_name.get("pm-agent")
result = {
    "old_role": old.get("role") if old else None,
    "pm_role": pm.get("role") if pm else None,
    "pm_team": pm.get("team") if pm else None
}
print(json.dumps(result))
'''
    
    stdout, rc = run_ac_test(1, setup, code)
    if rc != 0:
        check("AC-1执行成功", False, stdout)
        return
    
    try:
        r = json.loads(stdout)
        check("old-agent.role==general", r.get("old_role") == "general", str(r))
        check("pm-agent.role==pm", r.get("pm_role") == "pm", str(r))
        check("pm-agent.team==project-alpha", r.get("pm_team") == "project-alpha", str(r))
    except Exception as e:
        check("解析结果", False, str(e))


def test_ac2():
    """AC-2: target_type=all 路由"""
    print("\n=== AC-2: target_type=all 路由 ===")
    
    setup = '''
c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("pm-agent", "2026-08-20T10:01:00", "2026-08-20T10:01:00", "waiting", 0, None, "产品经理", "all", "pm", "[]", ""))
c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("eng-agent", "2026-08-20T10:02:00", "2026-08-20T10:02:00", "waiting", 0, None, "工程师", "all", "engineer", "[]", ""))
c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("msg_w1", 1, "test", "user", None, "all", None, None, 1, None, None, "2026-08-20T10:00:00", "c_001", None))
c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("msg_w2", 2, "test", "user", None, "single", "pm-agent", None, 1, None, None, "2026-08-20T10:01:00", "c_002", None))
c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("msg_w3", 3, "test", "user", None, "single", "eng-agent", None, 1, None, None, "2026-08-20T10:02:00", "c_003", None))
'''
    
    code = '''
msgs_pm = message_store.pull_messages("pm-agent")
ids_pm = [m["id"] for m in msgs_pm]
msgs_eng = message_store.pull_messages("eng-agent")
ids_eng = [m["id"] for m in msgs_eng]
result = {"pm_ids": ids_pm, "eng_ids": ids_eng}
print(json.dumps(result))
'''
    
    stdout, rc = run_ac_test(2, setup, code)
    if rc != 0:
        check("AC-2执行成功", False, stdout)
        return
    
    try:
        r = json.loads(stdout)
        check("pm收到msg_w2", "msg_w2" in r.get("pm_ids", []), str(r))
        check("eng收到msg_w3", "msg_w3" in r.get("eng_ids", []), str(r))
    except Exception as e:
        check("解析结果", False, str(e))


def test_ac3():
    """AC-3: workflow 关联过滤"""
    print("\n=== AC-3: workflow 关联 ===")
    
    setup = '''
c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("pm-agent", "2026-08-20T10:01:00", "2026-08-20T10:01:00", "waiting", 0, None, "产品经理", "all", "pm", "[]", ""))
c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("msg_w4", 4, "test", "user", None, "all", None, None, 1, None, None, "2026-08-20T10:03:00", "c_004", "workflow-001"))
'''
    
    code = '''
try:
    msgs = message_store.pull_messages("pm-agent", workflow_id="workflow-001")
    ids = [m["id"] for m in msgs]
    result = {"ids": ids, "success": True}
except Exception as e:
    result = {"error": str(e), "success": False}
print(json.dumps(result))
'''
    
    stdout, rc = run_ac_test(3, setup, code)
    if rc != 0:
        check("AC-3执行成功", False, stdout)
        return
    
    try:
        r = json.loads(stdout)
        if not r.get("success"):
            check("workflow过滤实现", False, r.get("error", "unknown"))
        else:
            check("workflow过滤只返回msg_w4", r.get("ids") == ["msg_w4"], str(r))
    except Exception as e:
        check("解析结果", False, str(e))


if __name__ == "__main__":
    try:
        test_ac1()
        test_ac2()
        test_ac3()
    finally:
        pass

    print(f"\n{'='*40}")
    print(f"结果: ✅ {PASS} 通过  ❌ {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
