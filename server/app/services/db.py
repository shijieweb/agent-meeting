# -*- coding: utf-8 -*-
"""SQLite 连接 / 建表 / 事务 / 表↔list 转换封装（二期 v6 存储层）。

设计锚点：design.md §1.2 DDL / §1.3 连接事务 / §1.5 稀疏字段 / §二 接口契约。
- 单文件库落 DATA_DIR（config.DB_FILENAME）。
- 连接：check_same_thread=False + 全局 RLock（storage._lock 保留，所有读写在锁内）。
- 事务：autocommit（isolation_level=None）+ 显式 BEGIN IMMEDIATE / COMMIT（坑8）。
- 无外键（坑5：孤儿引用照存，不补进 agents 表）。

本模块函数均为「持锁调用方」视角（lock-free）：串行化由 storage._lock 保证。
storage.py 负责持锁并调用本模块；migrate.py / rollback.py 在单进程脚本上下文中直接调用。
"""
import os
import re
import sqlite3

from app.config import DATA_DIR, DB_FILENAME

# 模块级单例连接（uvicorn 单进程多线程；storage._lock 串行化保证同时仅一线程触碰）。
_CONN = None

# agent_read 文件名转义正则（与 storage.agent_read_set_file 完全一致，坑6 撞名语义保真）。
_AGENT_KEY_RE = re.compile(r'[\\/:*?"<>|\s]+')


def escape_agent_key(name):
    """把 agent 名转义为安全文件名片段（与 storage.agent_read_set_file 同正则）。"""
    return _AGENT_KEY_RE.sub('_', str(name))


def _db_dir():
    return os.path.dirname(_db_path())


def _db_path():
    return os.path.join(DATA_DIR, DB_FILENAME)


def get_conn():
    """返回模块级单例连接（惰性创建）；autocommit + row_factory=Row。"""
    global _CONN
    if _CONN is None:
        path = _db_path()
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # autocommit：写事务由 BEGIN IMMEDIATE / COMMIT 显式控制（坑8，避免 deferred 提交瞬间争锁）。
        conn.isolation_level = None
        _CONN = conn
    return _CONN


def init_db():
    """建五表（CREATE TABLE IF NOT EXISTS）+ 索引；幂等可重跑。"""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            name           TEXT    PRIMARY KEY,
            registered_at  TEXT    NOT NULL,
            last_seen      TEXT,
            status         TEXT    NOT NULL DEFAULT 'waiting',
            session        INTEGER NOT NULL DEFAULT 0,
            token_hash     TEXT,
            description    TEXT    NOT NULL DEFAULT '',
            read_scope     TEXT    NOT NULL DEFAULT 'all',
            role           TEXT    NOT NULL DEFAULT 'general',
            capabilities   TEXT    NOT NULL DEFAULT '[]',
            team           TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id                  TEXT    PRIMARY KEY,
            seq                 INTEGER NOT NULL,
            content             TEXT    NOT NULL,
            sender_type         TEXT    NOT NULL,
            sender_agent_name   TEXT,
            target_type         TEXT,
            target_agent_name   TEXT,
            reply_to_message_id TEXT,
            visible             INTEGER,
            message_type        TEXT,
            event               TEXT,
            created_at          TEXT    NOT NULL,
            client_msg_id       TEXT,
            workflow_id         TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_seq ON messages(created_at, seq)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_agent_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_target ON messages(target_type, target_agent_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages(reply_to_message_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reads (
            message_id  TEXT    NOT NULL,
            agent_name  TEXT    NOT NULL,
            read_at     TEXT,
            PRIMARY KEY (message_id, agent_name)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reads_agent ON reads(agent_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reads_msg ON reads(message_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_read (
            agent_name  TEXT    NOT NULL,
            message_id  TEXT    NOT NULL,
            PRIMARY KEY (agent_name, message_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_read_agent ON agent_read(agent_name)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_read_init (
            agent_name  TEXT    PRIMARY KEY
        )
        """
    )
    # ---- 文档协作系统·一期（T-agent-meeting-upload，design v2.5 §三/§七）----
    # 建表唯一入口：documents / document_changes 走独立 SQLite 表，背后无任何 JSON 文件，
    # 因此永不进入 _dispatch()/write_table_from_list() 的整表 DELETE+INSERT 路径
    # => R1（发消息把附件冲 NULL）在结构上不可能发生（design §一 R1 规避机制）。
    # 幂等：CREATE TABLE IF NOT EXISTS，重跑 migrate.py 即增量补表，不触碰既有 5 表数据。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL,
            file_uuid   TEXT    NOT NULL,
            owner       TEXT    NOT NULL DEFAULT '',
            owner_type  TEXT    NOT NULL DEFAULT 'user',
            mime        TEXT    NOT NULL DEFAULT '',
            size        INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_type, owner)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_changes (
            id          TEXT    PRIMARY KEY,
            doc_id      TEXT    NOT NULL,
            actor       TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            summary     TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_changes_doc ON document_changes(doc_id)")
    return conn


# ---- 读写分发（lock-free，调用方持 storage._lock / 单进程脚本上下文）----

def _dispatch(name):
    """把存储文件名映射到 (kind, safe)；safe 仅 agent_read 有意义。"""
    if name == "agents.json":
        return ("agents", None)
    if name == "messages.json":
        return ("messages", None)
    if name == "reads.json":
        return ("reads", None)
    if name.startswith("agent_read_") and name.endswith(".json"):
        safe = name[len("agent_read_"):-len(".json")]
        return ("agent_read", safe)
    raise ValueError("unknown storage name: %r" % (name,))


def read_table_as_list(name, default):
    """把指定 JSON 等价表读回为 list[dict]（或 agent_read 的 list[str]）。

    稀疏字段规则（坑1 / §1.5）：
    - 恒 present 键始终写入（值可 None）：id/content/sender_type/sender_agent_name/
      target_type/target_agent_name/created_at/client_msg_id/read_by；
    - 稀疏键（reply_to_message_id/visible/message_type/event）仅非 NULL 才写键；
    - read_by 死字段（坑9）：读回恒补 []。
    """
    kind, safe = _dispatch(name)
    conn = get_conn()
    if kind == "agents":
        rows = conn.execute(
            "SELECT name, registered_at, last_seen, status, session, token_hash, "
            "description, read_scope, role, capabilities, team FROM agents ORDER BY name"
        ).fetchall()
        return [
            {
                "name": r["name"],
                "registered_at": r["registered_at"],
                "last_seen": r["last_seen"],
                "status": r["status"],
                "session": bool(r["session"]),
                "token_hash": r["token_hash"],
                "description": r["description"],
                "read_scope": r["read_scope"],
                "role": r["role"] or "general",
                "capabilities": r["capabilities"] or "[]",
                "team": r["team"] or "",
            }
            for r in rows
        ]
    if kind == "messages":
        rows = conn.execute(
            "SELECT id, seq, content, sender_type, sender_agent_name, target_type, "
            "target_agent_name, reply_to_message_id, visible, message_type, event, "
            "created_at, client_msg_id, workflow_id FROM messages ORDER BY seq"
        ).fetchall()
        out = []
        for r in rows:
            d = {
                "id": r["id"],
                "content": r["content"],
                "sender_type": r["sender_type"],
                "sender_agent_name": r["sender_agent_name"],
                "target_type": r["target_type"],
                "target_agent_name": r["target_agent_name"],
                "created_at": r["created_at"],
                "client_msg_id": r["client_msg_id"],
                "read_by": [],   # 死字段（坑9）：读回恒补 []，前端由 reads 实时算
            }
            # T-collab-01: 新协作字段 workflow_id
            if r["workflow_id"] is not None:
                d["workflow_id"] = r["workflow_id"]
            if r["reply_to_message_id"] is not None:
                d["reply_to_message_id"] = r["reply_to_message_id"]
            if r["visible"] is not None:
                d["visible"] = r["visible"]
            if r["message_type"] is not None:
                d["message_type"] = r["message_type"]
            if r["event"] is not None:
                d["event"] = r["event"]
            out.append(d)
        return out
    if kind == "reads":
        rows = conn.execute(
            "SELECT message_id, agent_name, read_at FROM reads"
        ).fetchall()
        return [
            {"message_id": r["message_id"], "agent_name": r["agent_name"], "read_at": r["read_at"]}
            for r in rows
        ]
    if kind == "agent_read":
        rows = conn.execute(
            "SELECT message_id FROM agent_read WHERE agent_name=? ORDER BY message_id", (safe,)
        ).fetchall()
        return [r["message_id"] for r in rows]
    raise ValueError("unknown kind: %r" % (kind,))


def write_table_from_list(name, data):
    """把 list[dict]（或 agent_read 的 list[str]）整表替换写回。

    在显式事务内 DELETE+INSERT（或 INSERT OR IGNORE）：
    - 泛型表（agents/messages/reads）整表替换；
    - agent_read 按 agent_name 删除后 INSERT OR IGNORE，并置 agent_read_init 标志（坑4）。
    D-1 铁律：调用方负责传入「被 mutator 原地修改后的 data」，本函数只写 data。
    """
    kind, safe = _dispatch(name)
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if kind == "agents":
            conn.execute("DELETE FROM agents")
            for a in data:
                conn.execute(
                    "INSERT INTO agents (name, registered_at, last_seen, status, session, "
                    "token_hash, description, read_scope) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        a.get("name"),
                        a.get("registered_at"),
                        a.get("last_seen"),
                        a.get("status", "waiting"),
                        int(bool(a.get("session", False))),
                        a.get("token_hash"),
                        a.get("description", ""),   # 坑2：缺省 ""，不落 NULL
                        a.get("read_scope", "all"),  # 坑2：缺省 "all"
                    ),
                )
        elif kind == "messages":
            conn.execute("DELETE FROM messages")
            for i, m in enumerate(data):
                conn.execute(
                    "INSERT INTO messages (id, seq, content, sender_type, sender_agent_name, "
                    "target_type, target_agent_name, reply_to_message_id, visible, message_type, "
                    "event, created_at, client_msg_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        m.get("id"),
                        i,   # seq = 列表下标（坑11：稳定排序键 = JSON 数组下标）
                        m.get("content"),
                        m.get("sender_type"),
                        m.get("sender_agent_name"),
                        m.get("target_type"),
                        m.get("target_agent_name"),
                        m.get("reply_to_message_id"),
                        m.get("visible"),   # 坑1：缺失→None→NULL，绝不落成 0
                        m.get("message_type"),
                        m.get("event"),
                        m.get("created_at"),
                        m.get("client_msg_id"),
                    ),
                )
        elif kind == "reads":
            conn.execute("DELETE FROM reads")
            for r in data:
                conn.execute(
                    "INSERT INTO reads (message_id, agent_name, read_at) VALUES (?,?,?)",
                    (r.get("message_id"), r.get("agent_name"), r.get("read_at")),
                )
        elif kind == "agent_read":
            conn.execute("DELETE FROM agent_read WHERE agent_name=?", (safe,))
            for mid in data:
                conn.execute(
                    "INSERT OR IGNORE INTO agent_read (agent_name, message_id) VALUES (?,?)",
                    (safe, mid),
                )
            # 置「已读集合已初始化」标志（坑4：等价 storage.agent_read_set_exists）
            conn.execute("INSERT OR IGNORE INTO agent_read_init (agent_name) VALUES (?)", (safe,))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def read_agent_read_set(safe):
    """返回某 agent（escaped key）已读 id 集合（lock-free）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT message_id FROM agent_read WHERE agent_name=?", (safe,)
    ).fetchall()
    return set(r["message_id"] for r in rows)


def agent_read_init_exists(safe):
    """等价 storage.agent_read_set_exists（lock-free）。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM agent_read_init WHERE agent_name=? LIMIT 1", (safe,)
    ).fetchone()
    return row is not None


def delete_agent_read(safe):
    """删除某 agent 的已读集合 + init 标志；返回是否原本存在（lock-free）。"""
    conn = get_conn()
    existed = conn.execute(
        "SELECT 1 FROM agent_read WHERE agent_name=? LIMIT 1", (safe,)
    ).fetchone()
    if existed is None:
        existed = conn.execute(
            "SELECT 1 FROM agent_read_init WHERE agent_name=? LIMIT 1", (safe,)
        ).fetchone()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM agent_read WHERE agent_name=?", (safe,))
        conn.execute("DELETE FROM agent_read_init WHERE agent_name=?", (safe,))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    return existed is not None


def integrity_ok():
    """PRAGMA integrity_check；返回 (ok:bool, detail:list[str])。"""
    conn = get_conn()
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    detail = [r[0] for r in rows]
    return (detail == ["ok"], detail)
