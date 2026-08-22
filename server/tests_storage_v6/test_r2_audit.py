# R2 操作审计 后端测试
import os, sys, sqlite3, time, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path('.').absolute()))

# 创建隔离测试环境
tmp = tempfile.mkdtemp(prefix="r2_")
os.environ['DATA_DIR'] = tmp
os.environ['SWEEP_INTERVAL'] = '0'
from app.services import db as main_db
main_db.init_db()
conn = main_db.get_conn()
now = time.strftime('%Y-%m-%dT%H:%M:%S')
for name in ['boss', 'agent_a']:
    conn.execute('INSERT INTO agents (name, registered_at, status, token_hash) VALUES (?, ?, ?, ?)', (name, now, 'offline', 'x'))
conn.commit()
from app.services import audit, message_store

print("\n=== R2: 操作审计 ===\n")

# T1: 记录审计动作
audit.record_action("boss", "delete_message", "message", "msg_123", "删除消息")
audit.record_action("agent_a", "cleanup_messages", "messages", None, "清理了5条")
audit.record_action("system", "cleanup_messages", "messages", None, "自动清理")

# T2: 查询审计日志
actions = audit.list_actions(limit=10)
assert len(actions) == 3, f"期望 3 条，实际 {len(actions)}"
print("  ✅ T1: 记录审计动作")

# T3: 按 actor 筛选
boss_actions = audit.list_actions(actor="boss", limit=10)
assert len(boss_actions) == 1, f"期望 1 条 boss 操作，实际 {len(boss_actions)}"
print("  ✅ T2: 按 actor 筛选")

# T4: 按 action 筛选
cleanup_actions = audit.list_actions(action="cleanup_messages", limit=10)
assert len(cleanup_actions) == 2, f"期望 2 条 cleanup 操作，实际 {len(cleanup_actions)}"
print("  ✅ T3: 按 action 筛选")

# T5: 统计数量
total = audit.count_actions()
assert total == 3, f"期望总数 3，实际 {total}"
print("  ✅ T4: 统计数量")

# T6: cleanup_messages 自动记录审计
message_store.send_user_message("Test", "single", "agent_a", client_msg_id="r2_test")
before = audit.count_actions()
result = message_store.cleanup_messages(keep_last=0)
after = audit.count_actions()
assert after == before + 1, f"cleanup 应增加 1 条审计，前={before} 后={after}"
print("  ✅ T5: cleanup_messages 自动记录审计")

shutil.rmtree(tmp, ignore_errors=True)
print("\nR2 所有测试通过!")
