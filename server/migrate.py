# -*- coding: utf-8 -*-
"""JSON → SQLite 幂等迁移（二期 v6，design.md §2.2 / F2）。

用法：cd agent-meeting/server && DATA_DIR=$DATA_DIR python migrate.py
- ① 先全量备份四类 JSON 到 backup_<时间戳>/（只读源、不烧，AC-2.5 / AC-3.1）
- ② 建五表（CREATE TABLE IF NOT EXISTS）
- ③ 幂等迁移（INSERT OR IGNORE），重跑不丢不重（AC-2.2）
- ④ 缺省字段补齐（description="" / read_scope="all"，坑2）
- ⑤ 异常数据 WARN 不静默（孤儿 agent_read / 孤儿回执 / 文件名撞名，坑6/7，AC-2.6）
- ⑥ 末尾 PRAGMA integrity_check
退出码 0 成功 / 非0 致命错误。
"""
import glob
import json
import os
import shutil
import sys
import time

from app.config import DATA_DIR
from app.services import db


def _md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    """执行迁移；返回退出码（0 / 非0）。"""
    # ---- ① 全量备份（先于任何写入；只读源 JSON，逐字节复制）----
    ts = time.strftime("%Y%m%d%H%M%S")
    backup_dir = os.path.join(DATA_DIR, "backup_%s" % ts)
    os.makedirs(backup_dir, exist_ok=True)

    source_files = ["agents.json", "messages.json", "reads.json"]
    # 精确扫描 agent_read_*.json（坑7：glob "*.json" 后缀天然排除 .bak/.corrupt/.tmp 等非 .json 结尾；
    # 额外 endswith 过滤作双保险）。
    for fp in glob.glob(os.path.join(DATA_DIR, "agent_read_*.json")):
        base = os.path.basename(fp)
        if base.endswith((".bak", ".corrupt", ".tmp")):
            continue
        source_files.append(base)

    backed_up = 0
    for base in source_files:
        src = os.path.join(DATA_DIR, base)
        if not os.path.isfile(src):
            continue
        shutil.copy(src, os.path.join(backup_dir, base))
        backed_up += 1

    # ---- ② 建库 ----
    db.init_db()
    conn = db.get_conn()

    report = []

    # ---- ③ agents ----
    agents_path = os.path.join(DATA_DIR, "agents.json")
    agents = []
    if os.path.isfile(agents_path):
        with open(agents_path, "r", encoding="utf-8") as f:
            agents = json.load(f)
    src_agents = len(agents)
    inserted = 0
    skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for a in agents:
            cur = conn.execute(
                "INSERT OR IGNORE INTO agents "
                "(name, registered_at, last_seen, status, session, token_hash, description, read_scope) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    a.get("name"),
                    a.get("registered_at"),
                    a.get("last_seen"),
                    a.get("status", "waiting"),
                    int(bool(a.get("session", False))),
                    a.get("token_hash"),
                    a.get("description", ""),    # 坑2：缺省 ""，不落 NULL
                    a.get("read_scope", "all"),  # 坑2：缺省 "all"
                ),
            )
            if cur.rowcount == 0:
                skipped += 1
            else:
                inserted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    target_agents = conn.execute("SELECT count(*) FROM agents").fetchone()[0]
    report.append("[MIGRATE] agents:     source=%d    target=%d    (inserted=%d skipped=%d)"
                  % (src_agents, target_agents, inserted, skipped))

    # ---- ④ messages ----
    msgs_path = os.path.join(DATA_DIR, "messages.json")
    msgs = []
    if os.path.isfile(msgs_path):
        with open(msgs_path, "r", encoding="utf-8") as f:
            msgs = json.load(f)
    src_msgs = len(msgs)
    inserted = 0
    skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for seq, m in enumerate(msgs):
            cur = conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(id, seq, content, sender_type, sender_agent_name, target_type, target_agent_name, "
                "reply_to_message_id, visible, message_type, event, created_at, client_msg_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    m.get("id"), seq, m.get("content"), m.get("sender_type"),
                    m.get("sender_agent_name"), m.get("target_type"), m.get("target_agent_name"),
                    m.get("reply_to_message_id"),
                    m.get("visible"),   # 坑1：缺失→None→NULL，绝不落成 0
                    m.get("message_type"), m.get("event"),
                    m.get("created_at"), m.get("client_msg_id"),
                ),
            )
            if cur.rowcount == 0:
                skipped += 1
            else:
                inserted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    target_msgs = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    report.append("[MIGRATE] messages:   source=%d  target=%d  (inserted=%d skipped=%d)"
                  % (src_msgs, target_msgs, inserted, skipped))

    # ---- ⑤ reads ----
    reads_path = os.path.join(DATA_DIR, "reads.json")
    reads = []
    if os.path.isfile(reads_path):
        with open(reads_path, "r", encoding="utf-8") as f:
            reads = json.load(f)
    src_reads = len(reads)
    inserted = 0
    skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in reads:
            cur = conn.execute(
                "INSERT OR IGNORE INTO reads (message_id, agent_name, read_at) VALUES (?,?,?)",
                (r.get("message_id"), r.get("agent_name"), r.get("read_at")),  # read_at 原值含 NULL
            )
            if cur.rowcount == 0:
                skipped += 1
            else:
                inserted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    target_reads = conn.execute("SELECT count(*) FROM reads").fetchone()[0]
    report.append("[MIGRATE] reads:      source=%d  target=%d  (inserted=%d skipped=%d)"
                  % (src_reads, target_reads, inserted, skipped))

    # ---- ⑥ agent_read（含孤儿 / 撞名 WARN）----
    grouped = {}  # safe(escaped) -> list of source file paths
    for fp in glob.glob(os.path.join(DATA_DIR, "agent_read_*.json")):
        base = os.path.basename(fp)
        if base.endswith((".bak", ".corrupt", ".tmp")):
            continue
        safe = base[len("agent_read_"):-len(".json")]
        grouped.setdefault(safe, []).append(fp)

    escaped_agents = {db.escape_agent_key(a.get("name", "")) for a in agents}
    orphans = []       # agent_read 文件名（raw safe）不在 agents
    collisions = []    # 同一 safe 对应多个源文件（坑6 撞名）
    for safe, fps in grouped.items():
        if safe not in escaped_agents:
            orphans.append(safe)
        if len(fps) > 1:
            collisions.append(safe)

    files_count = len(grouped)
    total_ids = 0
    inserted_ids = 0
    skipped_ids = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for safe, fps in grouped.items():
            ids = set()
            for fp in fps:
                with open(fp, "r", encoding="utf-8") as f:
                    ids |= set(json.load(f))
            total_ids += len(ids)
            for mid in ids:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO agent_read (agent_name, message_id) VALUES (?,?)",
                    (safe, mid),
                )
                if cur.rowcount == 0:
                    skipped_ids += 1
                else:
                    inserted_ids += 1
            conn.execute("INSERT OR IGNORE INTO agent_read_init (agent_name) VALUES (?)", (safe,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    target_ids = conn.execute("SELECT count(*) FROM agent_read").fetchone()[0]
    report.append("[MIGRATE] agent_read: files=%d ids=%d target=%d (inserted=%d skipped=%d)"
                  % (files_count, total_ids, target_ids, inserted_ids, skipped_ids))

    # ---- ⑦ 异常数据 WARN（禁止静默）----
    orphan_reads_agent = conn.execute(
        "SELECT count(*) FROM reads WHERE agent_name NOT IN (SELECT name FROM agents)"
    ).fetchone()[0]
    orphan_reads_msg = conn.execute(
        "SELECT count(*) FROM reads WHERE message_id NOT IN (SELECT id FROM messages)"
    ).fetchone()[0]
    report.append("[WARN] orphan agent_read files (not in agents): %d -> %s"
                  % (len(orphans), ", ".join(sorted(orphans)) if orphans else "none"))
    report.append("[WARN] orphan reads (agent_name not in agents): %d" % orphan_reads_agent)
    report.append("[WARN] orphan reads (message_id not in messages): %d" % orphan_reads_msg)
    report.append("[WARN] filename collision: %s"
                  % (", ".join(sorted(collisions)) if collisions else "none"))

    # ---- ⑧ integrity_check ----
    ok, detail = db.integrity_ok()
    if not ok:
        report.append("[ERROR] integrity_check failed: %s" % detail)
        for line in report:
            print(line)
        return 1

    report.append("[OK] migration complete; backup at %s/" % backup_dir)
    report.append("[OK] integrity_check: ok")
    for line in report:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
