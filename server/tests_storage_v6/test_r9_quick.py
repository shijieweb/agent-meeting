# R9 test
import os, sys, sqlite3, time, subprocess, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path('.').absolute()))
os.environ['DATA_DIR'] = tempfile.mkdtemp()
os.environ['SWEEP_INTERVAL'] = '0'
from app.services import db as main_db
main_db.init_db()
conn = sqlite3.connect(os.path.join(os.environ['DATA_DIR'], 'agent_meeting.db'))
now = time.strftime('%Y-%m-%dT%H:%M:%S')
conn.execute('INSERT INTO agents (name, registered_at, status, token_hash) VALUES (?, ?, ?, ?)', ('boss', now, 'offline', 'x'))
conn.execute('INSERT INTO agents (name, registered_at, status, token_hash) VALUES (?, ?, ?, ?)', ('agent_a', now, 'offline', 'x'))
conn.commit(); conn.close()
from app.services import message_store
print('agent_unread_count(boss):', message_store.agent_unread_count('boss'))
print('agent_unread_count(agent_a):', message_store.agent_unread_count('agent_a'))
message_store.send_user_message('Hello', 'single', 'agent_a', client_msg_id='test1')
print('after send, agent_a unread:', message_store.agent_unread_count('agent_a'))
print('PASS')
