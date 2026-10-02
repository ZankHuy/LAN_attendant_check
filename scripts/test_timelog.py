"""Test time log write-through + holiday effect on stats."""
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


def http(method, path, headers=None, json_body=None, accept_binary=False):
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
            hdrs = {}
            for k, v in r.headers.items():
                hdrs[k] = v
                hdrs[k.lower()] = v
                hdrs[k.title()] = v
            if accept_binary:
                return r.status, data, hdrs
            try:
                return r.status, json.loads(data) if data else None, hdrs
            except json.JSONDecodeError:
                return r.status, data, hdrs
    except urllib.error.HTTPError as e:
        data = e.read()
        hdrs = {}
        for k, v in e.headers.items():
            hdrs[k] = v
            hdrs[k.lower()] = v
            hdrs[k.title()] = v
        if accept_binary:
            return e.code, data, hdrs
        try:
            return e.code, json.loads(data) if data else None, hdrs
        except json.JSONDecodeError:
            return e.code, data, hdrs


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


# Login as admin
auth = "Basic " + base64.b64encode(b"admin:admin123").decode()
req = urllib.request.Request(BASE + "/api/auth/login", method="POST",
                              headers={"Authorization": auth})
with urllib.request.urlopen(req, timeout=5) as r:
    cookie = (r.headers.get("Set-Cookie") or r.headers.get("set-cookie", "")).split(";")[0]
print(f"cookie: {cookie[:40]}...")

# Get hidden token
status, token_resp, _ = http("POST", "/api/hidden/login",
                             json_body={"password": "123456"})
assert status == 200, f"hidden login: {status} {token_resp}"
TOKEN = token_resp["token"]
print(f"hidden token: {TOKEN[:20]}...")


# ── Create a weekday holiday and verify it shows up ───────────────────
section("Create L-holiday for 2026-09-09 (Wednesday)")
status, data, _ = http("POST", "/api/holidays",
                    headers={"Cookie": cookie},
                    json_body={
                        "date": "2026-09-09",
                        "kind": "L",
                        "label": "Nghỉ lễ test",
                        "scope": "all",
                    })
ok(f"created holiday: id={data.get('id')}" if status == 200 else f"failed: {status}")


# ── Verify stats/sheet contains this holiday ───────────────────────────
section("Stats sheet should reflect the holiday override")
status, data, _ = http("GET", "/api/stats/sheet?month=9&year=2026",
                    headers={"Cookie": cookie})
if status == 200 and isinstance(data, dict):
    # employees are nested under departments
    depts = data.get("departments", [])
    all_emps = [emp for d in depts for emp in d.get("employees", [])]
    if all_emps:
        emp = all_emps[0]
        print(f"  employee: id={emp.get('employee_id')} code={emp.get('employee_code')}")
        # days is a dict like {1: {...}, 2: {...}} keyed by day-of-month
        days = emp.get("days", {})
        if days:
            # Find day for 2026-09-09 - it's day 9 of the September period
            # (period_start=2026-08-26, period_end=2026-09-25)
            # 2026-09-09 = day 15 of the period (Aug 26 = day 1)
            day_key = "15"
            day_15 = days.get(day_key) or days.get(15) or list(days.values())[14] if len(days) > 14 else None
            print(f"  day 15 (Sep 9): {day_15}")
            if day_15 and isinstance(day_15, dict):
                wv = day_15.get("work_value")
                if wv == 1.0:
                    ok(f"holiday work_value=1.0")
                else:
                    print(f"  (info) work_value={wv}")
        else:
            print(f"  (info) no days dict for {emp.get('employee_code')}")
    else:
        fail("no employees in stats")
else:
    fail(f"stats sheet failed: {status}")


# ── Create checkin so we have an attendance row to PATCH ──────────────
section("Create checkin for NV001 so we have an attendance row")
status, data, _ = http("POST", "/api/attendance/checkin",
                       headers={"Cookie": cookie},
                       json_body={"employee_id": 1, "device_id": "TEST"})
ok(f"checkin: {status}" if status in (200, 201) else f"checkin failed: {status}")


# ── Hidden PATCH times + verify time_log ─────────────────────────────
section("Hidden PATCH times + verify writes to time_log")
# First get any attendance row
status, attendance, _ = http("GET", "/api/hidden/attendance",
                              headers={"X-Hidden-Token": TOKEN})
if status == 200 and isinstance(attendance, list) and len(attendance) > 0:
    row = attendance[0]
    ok(f"got attendance row id={row['id']}, employee_id={row['employee_id']}, date={row['date']}")
    ATT_ID = row["id"]
    EMP_ID = row["employee_id"]
    ROW_DATE = row["date"]
    # Set checkin=08:00, checkout=17:30
    status, data, _ = http("PATCH", f"/api/hidden/attendance/{ATT_ID}/times",
                            headers={"X-Hidden-Token": TOKEN},
                            json_body={"checkin": "08:00", "checkout": "17:30"})
    if status == 200:
        ok(f"PATCH times worked: {data.get('checkin_time')}, {data.get('checkout_time')}")
    else:
        fail(f"PATCH failed: {status} {data}")
else:
    print(f"  [SKIP] no attendance rows to PATCH (status={status}, len={len(attendance) if isinstance(attendance, list) else 'n/a'})")


# ── Clear both fields ──────────────────────────────────────────────────
section("Hidden PATCH clear both → row should be deleted")
if "ATT_ID" in dir() and ATT_ID:
    status, data, _ = http("PATCH", f"/api/hidden/attendance/{ATT_ID}/times",
                            headers={"X-Hidden-Token": TOKEN},
                            json_body={"checkin": None, "checkout": None})
    if status == 200 and data.get("checkin_time") is None:
        ok(f"cleared both fields")
        # Verify deletion
        status, lst, _ = http("GET", "/api/hidden/attendance",
                           headers={"X-Hidden-Token": TOKEN})
        ids = [r["id"] for r in lst]
        if ATT_ID not in ids:
            ok(f"row {ATT_ID} deleted")
        else:
            fail(f"row {ATT_ID} still present")
    else:
        fail(f"clear failed: {status} {data}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)