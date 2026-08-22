# -*- coding: utf-8 -*-
"""T-collab-01 Task4: SQLite持久化验证。

每个AC用独立临时目录，避免Windows文件锁问题。
直接用sqlite3，不走模块单例。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = 0
FAIL = 0


def setup(tmp_dir: str):
    """建立隔离SQLite环境（直接建表，不走单例）。"""
    db_path = Path(tmp_dir) / "agent_meeting.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS agents (
        name TEXT PRIMARY KEY, registered_at TEXT NOT NULL,
        last_seen TEXT, status TEXT NOT NULL DEFAULT 'waiting',
        session INTEGER NOT NULL DEFAULT 0, token_hash TEXT,
        description TEXT NOT NULL DEFAULT '', read_scope TEXT NOT NULL DEFAULT 'all'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, seq INTEGER NOT NULL, content TEXT NOT NULL,
        sender_type TEXT NOT NULL, sender_agent_name TEXT, target_type TEXT,
        target_agent_name TEXT, reply_to_message_id TEXT, visible INTEGER,
        message_type TEXT, event TEXT, created_at TEXT NOT NULL, client_msg_id TEXT
    )""")
    c.execute("CREATE TABLE IF NOT EXISTS reads (message_id TEXT NOT NULL, agent_name TEXT NOT NULL, read_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS agent_read (agent_name TEXT PRIMARY KEY, read_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS agent_read_init (agent_name TEXT PRIMARY KEY, read_at TEXT)")
    conn.commit()
    conn.close()

    from app.services.collab_db import CollabDB
    CollabDB(tmp_dir)


def insert_agent(tmp_dir: str, name: str):
    conn = sqlite3.connect(str(Path(tmp_dir) / "agent_meeting.db"))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO agents (name, registered_at, status, token_hash) VALUES (?, ?, 'offline', ?)",
        (name, now, "x"),
    )
    conn.commit()
    conn.close()


def run_test(name: str, test_fn):
    global PASS, FAIL
    tmp = tempfile.mkdtemp(prefix="collab_test_")
    try:
        setup(tmp)
        insert_agent(tmp, "test_agent")
        test_fn(tmp)
        print(f"  ✅ {name}")
        PASS += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        import traceback
        traceback.print_exc()
        FAIL += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_create_list(tmp_dir):
    from app.services.collab_db import CollabDB
    db = CollabDB(tmp_dir)
    wf = db.workflow_create("wf_test", "Test WF", "desc", ["a", "b"], {})
    assert wf["id"] == "wf_test"
    assert wf["name"] == "Test WF"
    assert wf["participants"] == ["a", "b"]

    tasks = db.task_list()
    assert len(tasks) == 0

    task = db.task_create("t1", "Do X", "detail", "test_agent")
    assert task["title"] == "Do X"
    assert task["status"] == "pending"

    tasks = db.task_list()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "t1"


def test_persistence_across_instances(tmp_dir):
    """跨实例重启后数据仍在（核心测试）。"""
    from app.services.collab_db import CollabDB
    db1 = CollabDB(tmp_dir)
    db1.workflow_create("wf_persist", "Persist WF", "", [], {})
    db1.task_create("t_persist", "Persist Task", "", "test_agent")

    # 模拟重启：重建实例（读取同一文件）
    db2 = CollabDB(tmp_dir)
    wfs = db2.workflow_list()
    tasks = db2.task_list()
    assert len(wfs) == 1
    assert wfs[0]["name"] == "Persist WF"
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Persist Task"


def test_workflow_crud(tmp_dir):
    from app.services.collab_db import CollabDB
    db = CollabDB(tmp_dir)
    wf = db.workflow_create("wf_crud", "CRUD WF", "desc", ["x"], {})
    assert wf is not None

    got = db.workflow_get("wf_crud")
    assert got["name"] == "CRUD WF"

    updated = db.workflow_update("wf_crud", name="Updated")
    assert updated["name"] == "Updated"

    deleted = db.workflow_delete("wf_crud")
    assert deleted is True
    assert db.workflow_get("wf_crud") is None


def test_task_status_flow(tmp_dir):
    from app.services.collab_db import CollabDB
    db = CollabDB(tmp_dir)
    task = db.task_create("t_status", "Status Flow", "", "test_agent")
    assert task["status"] == "pending"

    updated = db.task_update("t_status", status="in_progress")
    assert updated["status"] == "in_progress"

    updated = db.task_update("t_status", status="completed")
    assert updated["status"] == "completed"

    # comment追加
    updated = db.task_update("t_status", comment="First comment")
    assert len(updated["comments"]) == 1
    assert updated["comments"][0]["content"] == "First comment"

    updated = db.task_update("t_status", comment="Second comment")
    assert len(updated["comments"]) == 2


def test_filtering(tmp_dir):
    from app.services.collab_db import CollabDB
    db = CollabDB(tmp_dir)
    db.task_create("t1", "Task 1", "", "test_agent", priority="high")
    time.sleep(0.01)
    db.task_create("t2", "Task 2", "", "other_agent", priority="low")
    time.sleep(0.01)
    db.task_create("t3", "Task 3", "", "test_agent", priority="medium")

    by_assignee = db.task_list(assignee="test_agent")
    assert len(by_assignee) == 2
    assignees = {t["assignee"] for t in by_assignee}
    assert assignees == {"test_agent"}

    by_time = db.task_list(limit=10)
    assert len(by_time) == 3
    # 最新创建的应该排在最前
    assert by_time[0]["title"] == "Task 3"


if __name__ == "__main__":
    tests = [
        ("创建+列表", test_create_list),
        ("跨实例持久化", test_persistence_across_instances),
        ("Workflow CRUD", test_workflow_crud),
        ("任务状态流", test_task_status_flow),
        ("过滤查询", test_filtering),
    ]
    print("\n=== T-collab-01 Task4: SQLite持久化 ===\n")
    for name, fn in tests:
        run_test(name, fn)
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
