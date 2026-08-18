# -*- coding: utf-8 -*-
"""SQLite → JSON 回滚（双模式，二期 v6，design.md §2.3 / F3）。

模式A：--from-backup <dir>  备份还原（JSON 文件逐字节复制回 DATA_DIR，用于「迁移后立刻回滚」）
模式B：--export             SQLite→JSON 反向导出（保留迁移后新数据，用于「已跑一段时间才回滚」）
两模式互斥；退出码 0 成功 / 非0 参数错误或致命错误。均幂等（覆盖写）。

回滚 + 切回迁移前 storage.py（git 还原）后，JSON 版服务即可正常启动（AC-3.4）。
"""
import json
import os
import shutil
import sys

from app.config import DATA_DIR
from app.services import db


def _write_json_file(name, data):
    """写出 JSON 文件（ensure_ascii=False，与一期 JSON 版一致）。"""
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mode_restore(backup_dir):
    """模式A：把备份目录中的四类 JSON 还原回 DATA_DIR。"""
    if not os.path.isdir(backup_dir):
        print("[ERROR] backup dir not found: %s" % backup_dir)
        return 2
    files = ["agents.json", "messages.json", "reads.json"]
    # 仅还原备份目录中实际存在的 agent_read_*.json
    for base in os.listdir(backup_dir):
        if base.startswith("agent_read_") and base.endswith(".json"):
            files.append(base)
    copied = 0
    for base in files:
        src = os.path.join(backup_dir, base)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(DATA_DIR, base))
            copied += 1
    print("[OK] restored %d files from %s/" % (copied, backup_dir))
    return 0


def mode_export():
    """模式B：读 DB 写回 JSON（保留迁移后新产生的数据）。"""
    db.init_db()
    conn = db.get_conn()
    # agents.json
    agents = []
    for r in conn.execute(
        "SELECT name, registered_at, last_seen, status, session, token_hash, "
        "description, read_scope FROM agents ORDER BY name"
    ):
        agents.append({
            "name": r["name"],
            "registered_at": r["registered_at"],
            "last_seen": r["last_seen"],
            "status": r["status"],
            "session": bool(r["session"]),
            "token_hash": r["token_hash"],
            "description": r["description"],
            "read_scope": r["read_scope"],
        })
    _write_json_file("agents.json", agents)
    # messages.json（read_table_as_list 已补 read_by:[] 与稀疏键规则）
    msgs = db.read_table_as_list("messages.json", [])
    _write_json_file("messages.json", msgs)
    # reads.json
    reads = db.read_table_as_list("reads.json", [])
    _write_json_file("reads.json", reads)
    # agent_read_<X>.json：agent_read_init ∪ DISTINCT agent_name FROM agent_read
    agents_in = set()
    for r in conn.execute("SELECT agent_name FROM agent_read_init"):
        agents_in.add(r["agent_name"])
    for r in conn.execute("SELECT DISTINCT agent_name FROM agent_read"):
        agents_in.add(r["agent_name"])
    for safe in sorted(agents_in):
        ids = [row["message_id"] for row in conn.execute(
            "SELECT message_id FROM agent_read WHERE agent_name=? ORDER BY message_id", (safe,))]
        _write_json_file("agent_read_%s.json" % safe, sorted(ids))  # 空集写 []
    print("[OK] exported agents/messages/reads/agent_read from DB to %s/" % DATA_DIR)
    return 0


def main(argv=None):
    """解析参数并执行回滚；返回退出码。"""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) == 2 and argv[0] == "--from-backup":
        return mode_restore(argv[1])
    if len(argv) == 1 and argv[0] == "--export":
        return mode_export()
    print("usage: python rollback.py (--from-backup <dir> | --export)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
