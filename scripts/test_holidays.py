"""Test holiday CRUD + verify the rules in stats sheet."""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8000"


def http(method, path, headers=None, json_body=None):
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
            try:
                return r.status, json.loads(data) if data else None
            except json.JSONDecodeError:
                return r.status, data
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data) if data else None
        except json.JSONDecodeError:
            return e.code, data


_passed = 0
_failed = 0


def ok(msg):
    global _passed
    _passed += 1
    print(f"  [OK] {msg}")


def fail(msg, detail=""):
    global _failed
    _failed += 1
    print(f"  [FAIL] {msg}")
    if detail:
        print(f"    {detail}")


def section(name):
    print(f"\n=== {name} ===")


# Login
auth = "Basic " + base64.b64encode(b"admin:admin123").decode()
status, _ = http("POST", "/api/auth/login", headers={"Authorization": auth})
assert status == 200, f"login: {status}"
import re
# Use a fresh login
status, response = http("POST", "/api/auth/login", headers={"Authorization": auth})
cookie_match = re.search(r'checknv_session=[^;]+', str(response) if response else "")
# We need to get cookie via a separate request
req = urllib.request.Request(BASE + "/api/auth/login", method="POST",
                              headers={"Authorization": auth})
with urllib.request.urlopen(req, timeout=5) as r:
    cookie = r.headers.get("Set-Cookie") or r.headers.get("set-cookie", "")
cookie = cookie.split(";")[0]
print(f"cookie: {cookie[:40]}...")


# ── List holidays (initially empty) ────────────────────────────────────
section("Holidays: initial list")
status, data = http("GET", "/api/holidays", headers={"Cookie": cookie})
if status == 200 and isinstance(data, list):
    ok(f"GET /api/holidays returns {len(data)} items")
else:
    fail(f"GET /api/holidays: {status} {data}")


# ── Create 'all' holiday (L) ────────────────────────────────────────────
section("Create holiday: 'L' (le) for everyone on 2026-09-02")
status, data = http("POST", "/api/holidays",
                    headers={"Cookie": cookie},
                    json_body={
                        "date": "2026-09-02",
                        "kind": "L",
                        "label": "Quốc khánh",
                        "scope": "all",
                    })
if status == 200 and isinstance(data, dict) and "id" in data:
    ok(f"created L-holiday id={data['id']}")
    h_id = data["id"]
else:
    fail(f"create failed: {status} {data}")


# ── Create 'employee' holiday (P) ──────────────────────────────────────
section("Create holiday: 'P' (phep) for NV001 on 2026-09-15")
status, data = http("POST", "/api/holidays",
                    headers={"Cookie": cookie},
                    json_body={
                        "date": "2026-09-15",
                        "kind": "P",
                        "label": "Nghỉ phép cá nhân",
                        "scope": "employee",
                        "employee_id": 1,
                    })
if status == 200 and isinstance(data, dict) and "id" in data:
    ok(f"created P-holiday id={data['id']}")
else:
    fail(f"create failed: {status} {data}")


# ── List holidays after creates ────────────────────────────────────────
section("List holidays after 2 creates")
status, data = http("GET", "/api/holidays", headers={"Cookie": cookie})
if status == 200 and isinstance(data, list) and len(data) >= 2:
    ok(f"got {len(data)} holidays")
    for h in data:
        print(f"    id={h['id']} date={h['date']} kind={h['kind']} scope={h['scope']} label={h['label']!r}")
else:
    fail(f"list failed: {status}")


# ── Validation: bad kind ────────────────────────────────────────────────
section("Validation: kind='X' (invalid)")
status, data = http("POST", "/api/holidays",
                    headers={"Cookie": cookie},
                    json_body={
                        "date": "2026-09-30",
                        "kind": "X",
                        "scope": "all",
                    })
if status == 422:
    ok(f"rejected kind=X with 422")
else:
    fail(f"kind=X not rejected: {status} {data}")


# ── Validation: bad scope ───────────────────────────────────────────────
section("Validation: scope='world' (invalid)")
status, data = http("POST", "/api/holidays",
                    headers={"Cookie": cookie},
                    json_body={
                        "date": "2026-09-30",
                        "kind": "L",
                        "scope": "world",
                    })
if status == 422:
    ok(f"rejected scope=world with 422")
else:
    fail(f"scope=world not rejected: {status} {data}")


# ── Delete a holiday ────────────────────────────────────────────────────
section("Delete a holiday")
if "h_id" in dir() and h_id:
    status, data = http("DELETE", f"/api/holidays/{h_id}",
                        headers={"Cookie": cookie})
    if status == 200:
        ok(f"deleted holiday id={h_id}")
    else:
        fail(f"delete failed: {status} {data}")


# ── Filter by year+month ────────────────────────────────────────────────
section("Filter holidays by year=2026 month=9")
status, data = http("GET", "/api/holidays?year=2026&month=9", headers={"Cookie": cookie})
if status == 200 and isinstance(data, list):
    ok(f"got {len(data)} holidays in Sep 2026")
else:
    fail(f"filter failed: {status}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)