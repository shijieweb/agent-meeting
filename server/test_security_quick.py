#!/usr/bin/env python
# -*- coding: utf-8 -*-
import requests
import json
import sys

BASE = 'http://localhost:8011'
results = []

# Test ①: Human upload
r = requests.post(f'{BASE}/api/docs/upload', files={'file': ('test.txt', b'test content', 'text/plain')})
doc = r.json()
doc_id = doc['id']
results.append(('① Human upload', r.status_code == 200, r.status_code))
sys.stdout.write(f"Test ①: {r.status_code} (expected 200) {'OK' if r.status_code == 200 else 'FAIL'}\n")
sys.stdout.flush()

# Test ②: Agent without agent_name - PUT (human super-admin)
r2 = requests.put(f'{BASE}/api/docs/{doc_id}', json={'content': 'hacked'})
results.append(('② PUT no agent_name', r2.status_code == 200, r2.status_code))
sys.stdout.write(f"Test ②: {r2.status_code} (expected 200) {'OK' if r2.status_code == 200 else 'FAIL'}\n")
sys.stdout.flush()

# Test ③: Agent without agent_name - DELETE (human super-admin)
r3 = requests.delete(f'{BASE}/api/docs/{doc_id}')
results.append(('③ DELETE no agent_name', r3.status_code == 200, r3.status_code))
sys.stdout.write(f"Test ③: {r3.status_code} (expected 200) {'OK' if r3.status_code == 200 else 'FAIL'}\n")
sys.stdout.flush()

# Test ④: Agent with agent_name - PUT (not owner)
r_new = requests.post(f'{BASE}/api/docs/upload', files={'file': ('test4.txt', b'test4', 'text/plain')})
doc_id4 = r_new.json()['id']
r4 = requests.put(f'{BASE}/api/docs/{doc_id4}', json={'content': 'hacked'}, params={'agent_name': 'TestAgent'})
results.append(('④ PUT with agent_name', r4.status_code == 403, r4.status_code))
sys.stdout.write(f"Test ④: {r4.status_code} (expected 403) {'OK' if r4.status_code == 403 else 'FAIL'}\n")
sys.stdout.flush()

# Test ⑤: Agent with agent_name - POST new
r5 = requests.post(f'{BASE}/api/docs', json={'name': 'hack.txt'}, params={'agent_name': 'TestAgent'})
results.append(('⑤ POST with agent_name', r5.status_code == 403, r5.status_code))
sys.stdout.write(f"Test ⑤: {r5.status_code} (expected 403) {'OK' if r5.status_code == 403 else 'FAIL'}\n")
sys.stdout.flush()

# Test ⑥: Forbidden fields
r6 = requests.post(f'{BASE}/api/docs', json={'name': 'x.txt', 'sender_type': 'user', 'owner': 'other'})
results.append(('⑥ Forbidden fields', r6.status_code == 422, r6.status_code))
sys.stdout.write(f"Test ⑥: {r6.status_code} (expected 422) {'OK' if r6.status_code == 422 else 'FAIL'}\n")
sys.stdout.flush()

sys.stdout.write("\nSummary:\n")
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
sys.stdout.write(f"  Passed: {passed}/{total}\n")
for name, ok, code in results:
    sys.stdout.write(f"  {'PASS' if ok else 'FAIL'} {name}: {code}\n")
sys.stdout.flush()
