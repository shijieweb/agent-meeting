#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Security tests for commit 53f98e6 - Agent Meeting docs system fixes."""

import requests
import json

BASE = 'http://localhost:8011'

def test_security():
    results = []

    # Test ①: Human upload to get doc_id
    print("=" * 60)
    print("Test ①: Human upload to get doc_id")
    print("=" * 60)
    try:
        r = requests.post(f'{BASE}/api/docs/upload', files={'file': ('test.txt', b'test content', 'text/plain')})
        print(f"HTTP Code: {r.status_code}")
        doc = r.json()
        doc_id = doc['id']
        print(f"doc_id: {doc_id}")
        print(f"owner: {doc['owner']}, owner_type: {doc['owner_type']}")
        results.append(("① Human upload", r.status_code == 200, r.status_code))
    except Exception as e:
        print(f"ERROR: {e}")
        results.append(("① Human upload", False, str(e)))
        return results
    print()

    # Test ②: Agent WITHOUT agent_name - PUT
    print("=" * 60)
    print("Test ②: Agent without agent_name - PUT")
    print("Expected: 200 (super-admin human can edit any doc)")
    print("=" * 60)
    r2 = requests.put(f'{BASE}/api/docs/{doc_id}', json={'content': 'hacked'})
    print(f"HTTP Code: {r2.status_code}")
    print(f"Response: {r2.text[:200] if r2.text else 'empty'}")
    # Note: Without agent_name, treated as human super-admin who CAN edit any doc
    results.append(("② PUT no agent_name", r2.status_code == 200, r2.status_code))
    print()

    # Test ③: Agent WITHOUT agent_name - DELETE
    print("=" * 60)
    print("Test ③: Agent without agent_name - DELETE")
    print("Expected: 200 (super-admin human can delete any doc)")
    print("=" * 60)
    r3 = requests.delete(f'{BASE}/api/docs/{doc_id}')
    print(f"HTTP Code: {r3.status_code}")
    print(f"Response: {r3.text[:200] if r3.text else 'empty'}")
    results.append(("③ DELETE no agent_name", r3.status_code == 200, r3.status_code))
    print()

    # Test ④: Agent WITH ?agent_name=TestAgent - PUT (not owner)
    print("=" * 60)
    print("Test ④: Agent with agent_name=TestAgent - PUT (not owner)")
    print("Expected: 403 (Agent cannot modify human-owned doc)")
    print("=" * 60)
    # Create new doc
    r_new = requests.post(f'{BASE}/api/docs/upload', files={'file': ('test4.txt', b'test4', 'text/plain')})
    doc_id4 = r_new.json()['id']
    r4 = requests.put(f'{BASE}/api/docs/{doc_id4}', json={'content': 'hacked'}, params={'agent_name': 'TestAgent'})
    print(f"HTTP Code: {r4.status_code}")
    print(f"Response: {r4.text[:200] if r4.text else 'empty'}")
    results.append(("④ PUT with agent_name", r4.status_code == 403, r4.status_code))
    print()

    # Test ⑤: Agent WITH ?agent_name=TestAgent - POST new doc
    print("=" * 60)
    print("Test ⑤: Agent with agent_name=TestAgent - POST new")
    print("Expected: 403 (Agent cannot create new doc)")
    print("=" * 60)
    r5 = requests.post(f'{BASE}/api/docs', json={'name': 'hack.txt'}, params={'agent_name': 'TestAgent'})
    print(f"HTTP Code: {r5.status_code}")
    print(f"Response: {r5.text[:200] if r5.text else 'empty'}")
    results.append(("⑤ POST with agent_name", r5.status_code == 403, r5.status_code))
    print()

    # Test ⑥: Forbidden fields in body
    print("=" * 60)
    print("Test ⑥: Forbidden fields in body (sender_type/owner)")
    print("Expected: 422 (forbidden fields rejected by Pydantic)")
    print("=" * 60)
    r6 = requests.post(f'{BASE}/api/docs', json={'name': 'x.txt', 'sender_type': 'user', 'owner': 'other'})
    print(f"HTTP Code: {r6.status_code}")
    print(f"Response: {r6.text[:200] if r6.text else 'empty'}")
    results.append(("⑥ Forbidden fields", r6.status_code == 422, r6.status_code))
    print()

    return results

if __name__ == '__main__':
    results = test_security()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = 0
    failed = 0
    for name, ok, code in results:
        status = "PASS" if ok else "FAIL"
        print(f"{status}: {name} - got {code}")
        if ok:
            passed += 1
        else:
            failed += 1
    print()
    print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
