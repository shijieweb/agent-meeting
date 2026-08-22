# -*- coding: utf-8 -*-
"""T-collab-01: Workflow和Task的SQLite持久化层。

替代内存存储，支持多进程/重启后数据保留。
"""
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("am_db_collab")


class CollabDB:
    """协作功能专用SQLite封装（workflows + tasks）。"""

    def __init__(self, data_dir: str):
        self.db_path = Path(data_dir) / "collab.db"
        self._init_tables()

    def _init_tables(self):
        """建表（幂等）。"""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            participants TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}',
            archived INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            assignee TEXT NOT NULL,
            reporter TEXT DEFAULT 'boss',
            project_id INTEGER,
            parent_task_id TEXT,
            priority TEXT DEFAULT 'medium',
            deadline TEXT,
            progress INTEGER DEFAULT 0,
            comments TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)")
        conn.commit()
        conn.close()
        logger.info("collab DB initialized: %s", self.db_path)

    # ---- Workflow CRUD ----

    def workflow_create(self, wf_id: str, name: str, description: str,
                        participants: List[str], metadata: dict) -> dict:
        now = time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}Z"
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute(
            "INSERT INTO workflows (id, name, description, participants, metadata, archived, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (wf_id, name, description, json.dumps(participants, ensure_ascii=False),
             json.dumps(metadata), now, now)
        )
        conn.commit()
        conn.close()
        return self.workflow_get(wf_id)

    def workflow_list(self, archived: bool = False, participant: Optional[str] = None) -> List[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if archived:
            c.execute("SELECT * FROM workflows ORDER BY created_at DESC")
        else:
            c.execute("SELECT * FROM workflows WHERE archived = 0 ORDER BY created_at DESC")
        rows = c.fetchall()
        conn.close()
        result = []
        for r in rows:
            wf = dict(r)
            wf["participants"] = json.loads(wf["participants"])
            wf["metadata"] = json.loads(wf["metadata"])
            if participant and participant not in wf["participants"]:
                continue
            result.append(wf)
        return result

    def workflow_get(self, wf_id: str) -> Optional[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,))
        r = c.fetchone()
        conn.close()
        if not r:
            return None
        wf = dict(r)
        wf["participants"] = json.loads(wf["participants"])
        wf["metadata"] = json.loads(wf["metadata"])
        return wf

    def workflow_update(self, wf_id: str, **kwargs) -> Optional[dict]:
        allowed = {"name", "description", "archived", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.workflow_get(wf_id)
        updates["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}Z"
        if "metadata" in updates:
            updates["metadata"] = json.dumps(updates["metadata"], ensure_ascii=False)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [wf_id]
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(f"UPDATE workflows SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()
        return self.workflow_get(wf_id)

    def workflow_delete(self, wf_id: str) -> bool:
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("DELETE FROM workflows WHERE id = ?", (wf_id,))
        deleted = c.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def workflow_count_messages(self, wf_id: str) -> int:
        """统计该workflow关联的消息数（需调用message_store）。"""
        try:
            from app.services import message_store
            msgs = message_store.load_messages()
            return sum(1 for m in msgs if m.get("workflow_id") == wf_id)
        except Exception:
            return 0

    # ---- Task CRUD ----

    def task_create(self, task_id: str, title: str, description: str,
                    assignee: str, reporter: str = "boss", **kwargs) -> dict:
        now = time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}Z"
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("""INSERT INTO tasks (
            id, title, description, status, assignee, reporter,
            project_id, parent_task_id, priority, deadline, progress,
            comments, metadata, created_at, updated_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, '[]', ?, ?, ?)""",
            (task_id, title, description, assignee, reporter,
             kwargs.get("project_id"), kwargs.get("parent_task_id"),
             kwargs.get("priority", "medium"), kwargs.get("deadline"),
             json.dumps(kwargs.get("metadata", {})), now, now))
        conn.commit()
        conn.close()
        return self.task_get(task_id)

    def task_list(self, status: Optional[str] = None, assignee: Optional[str] = None,
                  project_id: Optional[int] = None, limit: int = 50) -> List[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if assignee:
            sql += " AND assignee = ?"
            params.append(assignee)
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        c.execute(sql, params)
        rows = c.fetchall()
        conn.close()
        result = []
        for r in rows:
            t = dict(r)
            t["comments"] = json.loads(t["comments"])
            t["metadata"] = json.loads(t["metadata"])
            result.append(t)
        return result

    def task_get(self, task_id: str) -> Optional[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        r = c.fetchone()
        conn.close()
        if not r:
            return None
        t = dict(r)
        t["comments"] = json.loads(t["comments"])
        t["metadata"] = json.loads(t["metadata"])
        return t

    def task_update(self, task_id: str, **kwargs) -> Optional[dict]:
        allowed = {"status", "assignee", "progress", "comment"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.task_get(task_id)
        # 处理comment：追加到comments数组
        if "comment" in updates:
            t = self.task_get(task_id)
            if t:
                comments = t.get("comments", [])
                comments.append({
                    "content": updates.pop("comment"),
                    "author": "user",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                updates["comments_json"] = json.dumps(comments, ensure_ascii=False)
        updates["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}Z"
        set_clause = ", ".join(f"{k} = ?" for k in updates if k != "comments_json")
        if "comments_json" in updates:
            set_clause += ", comments = ?"
        values = [updates[k] for k in updates if k != "comments_json"]
        if "comments_json" in updates:
            values.append(updates["comments_json"])
        values.append(task_id)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()
        return self.task_get(task_id)

    def task_delete(self, task_id: str) -> bool:
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        deleted = c.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
