# R9 未读徽标 + 已读回执 后端测试
import os, sys, sqlite3, time, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path('.').absolute()))

PASS = 0
FAIL = 0

def setup():
    tmp = tempfile.mkdtemp(prefix="r9_")
    os.environ['DATA_DIR'] = tmp
    os.environ['SWEEP_INTERVAL'] = '0'
    from app.services import db as main_db
    # 清除单例连接，强制重建
    if hasattr(main_db, '_CONN') and main_db._CONN:
        main_db._CONN.close()
        main_db._CONN = None
    main_db.init_db()
    # 使用 init_db 创建的连接插入数据
    conn = main_db.get_conn()
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    for name in ['boss', 'agent_a']:
        try:
            conn.execute('INSERT INTO agents (name, registered_at, status, token_hash) VALUES (?, ?, ?, ?)', (name, now, 'offline', 'x'))
        except:
            pass  # 已存在则跳过
    conn.commit()
    from app.services import message_store
    return tmp, message_store

def cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
    # 重置单例连接，防止指向已删除的数据库
    from app.services import db as main_db
    if hasattr(main_db, '_CONN') and main_db._CONN:
        try:
            main_db._CONN.close()
        except:
            pass
        main_db._CONN = None

def check(name, cond):
    global PASS, FAIL
    if cond:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}")
        FAIL += 1

print("\n=== R9: 未读徽标 + 已读回执 ===\n")

# T1
tmp, ms = setup()
check("T1: boss 空消息未读数=0", ms.agent_unread_count('boss') == 0)
check("T1: agent_a 空消息未读数=0", ms.agent_unread_count('agent_a') == 0)
cleanup(tmp)

# T2
tmp, ms = setup()
ms.send_user_message('Hello', 'single', 'agent_a', client_msg_id='t2')
check("T2: boss 未读数=0", ms.agent_unread_count('boss') == 0)
check("T2: agent_a 未读数=1", ms.agent_unread_count('agent_a') == 1)
cleanup(tmp)

# T3
tmp, ms = setup()
ms.send_user_message('Hi', 'single', 'agent_a', client_msg_id='t3')
check("T3: pull 前未读数=1", ms.agent_unread_count('agent_a') == 1)
ms.pull_messages('agent_a')
check("T3: pull 后未读数=0", ms.agent_unread_count('agent_a') == 0)
cleanup(tmp)

# T4
tmp, ms = setup()
ms.send_user_message('M1', 'single', 'agent_a', client_msg_id='t4a')
ms.send_user_message('M2', 'single', 'agent_a', client_msg_id='t4b')
msgs = ms.load_messages()
agent_msgs = [m for m in msgs if m.get('target_agent_name') == 'agent_a']
ids = [agent_msgs[-2]['id'], agent_msgs[-1]['id']]
ms.mark_reads_json('agent_a', [ids[0]])
check("T4: 标记一条后未读数=1", ms.agent_unread_count('agent_a') == 1)
ms.mark_reads_json('agent_a', [ids[1]])
check("T4: 标记两条后未读数=0", ms.agent_unread_count('agent_a') == 0)
cleanup(tmp)

# T5
tmp, ms = setup()
ms.send_user_message('X', 'single', 'agent_a', client_msg_id='t5')
msg_id = [m for m in ms.load_messages() if m.get('target_agent_name') == 'agent_a'][-1]['id']
ms.mark_reads_json('agent_a', [msg_id])
check("T5: 第一次标记后未读数=0", ms.agent_unread_count('agent_a') == 0)
ms.mark_reads_json('agent_a', [msg_id])
check("T5: 第二次标记（幂等）后未读数=0", ms.agent_unread_count('agent_a') == 0)
cleanup(tmp)

# T6
tmp, ms = setup()
check("T6: 无消息返回 False", ms.agent_has_unread('agent_a') is False)
ms.send_user_message('Y', 'single', 'agent_a', client_msg_id='t6')
check("T6: 有未读返回 True", ms.agent_has_unread('agent_a') is True)
ms.pull_messages('agent_a')
check("T6: pull 后返回 False", ms.agent_has_unread('agent_a') is False)
cleanup(tmp)

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
