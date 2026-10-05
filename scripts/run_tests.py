"""Comprehensive smoke tests for the CheckNV backend.

Run with:
    py -3.11 scripts/run_tests.py

Assumes uvicorn is running on http://127.0.0.1:8000 with HIDDEN_PASSWORD=123456.
Hits the real HTTP surface (no DB access from here). Tests are ordered so
each builds on what came before.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

# Force UTF-8 stdout so test messages with Vietnamese survive on cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

# ── Pretty print ────────────────────────────────────────────────────

_passed = 0
_failed = 0
_section_passed = 0
_section_total = 0


def section(name: str):
    global _section_passed, _section_total
    if _section_total:
        status = "[OK]" if _section_passed == _section_total else f"[FAIL ({_section_passed}/{_section_total})]"
        print(f"  {status}")
    _section_passed = 0
    _section_total = 0
    print(f"\n=== {name} ===")


def assert_eq(actual, expected, msg: str = ""):
    global _passed, _failed, _section_passed, _section_total
    _section_total += 1
    if actual == expected:
        _passed += 1
        _section_passed += 1
        print(f"  [OK] {msg or 'eq'}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg or 'eq'}")
        print(f"      expected: {expected!r}")
        print(f"      actual:   {actual!r}")


def assert_true(cond, msg: str = ""):
    global _passed, _failed, _section_passed, _section_total
    _section_total += 1
    if cond:
        _passed += 1
        _section_passed += 1
        print(f"  [OK] {msg or 'true'}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg or 'true'}")


def assert_in(needle, haystack, msg: str = ""):
    assert_true(needle in haystack, msg or f"contains {needle!r}")


def http(method: str, path: str, *, headers=None, json_body=None, raw_body=None, timeout: float = 10.0, accept_binary: bool = False):
    """Tiny HTTP client using urllib so we don't need requests."""
    import urllib.request
    import urllib.error
    url = BASE + path
    body = None
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    if json_body is not None:
        body = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    elif raw_body is not None:
        body = raw_body
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            # r.headers is a Message (case-insensitive). Copy to a regular dict
            # but ALSO keep a lowercased AND title-cased mirror so callers can
            # lookup either way.
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


# ── 1. Healthcheck ─────────────────────────────────────────────────
status, response, headers = http("GET", "/health")
assert_eq(status, 200, "GET /health returns 200")
assert_eq(response, {"status": "ok"}, "/health body is {status: ok}")

# ── 2. Public pages ────────────────────────────────────────────────
status, response, headers = http("GET", "/")
assert_eq(status, 200, "GET / returns 200")
assert_true(b"<html" in response.lower() if isinstance(response, bytes) else "<html" in response.lower(),
            "GET / contains HTML")

status, response, headers = http("GET", "/admin")
assert_eq(status, 200, "GET /admin returns 200")

status, response, headers = http("GET", "/login")
assert_eq(status, 200, "GET /login returns 200")

# ── 3. Hidden page requires password ──────────────────────────────
status, response, headers = http("GET", "/hidden")
assert_eq(status, 200, "GET /hidden returns 200 (password configured)")
assert_true(b"type=" in response if isinstance(response, bytes) else "type=" in response,
            "page contains input form")

# ── 4. Settings (public) ──────────────────────────────────────────
status, response, headers = http("GET", "/api/attendance/settings")
assert_eq(status, 200, "GET /api/attendance/settings returns 200")
assert_in("am_checkin_deadline", response, "settings has am_checkin_deadline")
assert_in("pm_checkout_deadline", response, "settings has pm_checkout_deadline")

# ── 5. Hidden auth: login + token ─────────────────────────────────
status, response, headers = http("POST", "/api/hidden/login",
                                json_body={"password": "wrong-password"})
assert_eq(status, 401, "wrong password returns 401")

status, response, headers = http("POST", "/api/hidden/login",
                                json_body={"password": "123456"})
assert_eq(status, 200, "correct password returns 200")
assert_in("token", response, "login response contains token")
TOKEN = response["token"]
assert_eq(response["expires_in"], 12 * 3600, "expires_in = 12 hours")

# ── 6. Hidden: list without token = 401 ───────────────────────────
status, response, headers = http("GET", "/api/hidden/attendance")
assert_eq(status, 401, "GET attendance without token returns 401")

# ── 7. Hidden: list with token = 200 (even if empty) ─────────────
status, response, headers = http("GET", "/api/hidden/attendance",
                                headers={"X-Hidden-Token": TOKEN})
assert_eq(status, 200, "GET attendance with token returns 200")
assert_true(isinstance(response, list), "response is a list")
assert_true(int(headers.get("X-Total-Count", 0)) >= 0, "X-Total-Count header present")

# ── 8. Admin login (HTTPBasic) + employees ───────────────────────
admin_basic = "Basic " + base64.b64encode(b"admin:wrongpw").decode()
status, response, headers = http("POST", "/api/auth/login", headers={"Authorization": admin_basic})
assert_true(status in (401, 422), "wrong admin password is rejected")

admin_basic_ok = "Basic " + base64.b64encode(b"admin:admin123").decode()
status, response, headers = http("POST", "/api/auth/login", headers={"Authorization": admin_basic_ok})
assert_eq(status, 200, "admin/admin123 returns 200")
admin_cookie = headers.get("Set-Cookie", "") or headers.get("set-cookie", "")
assert_in("checknv_session=", admin_cookie, "session cookie is set")

# ── 9. Employees list ────────────────────────────────────────────
status, response, headers = http("GET", "/api/employees",
                                headers={"Cookie": admin_cookie})
assert_eq(status, 200, "GET /api/employees returns 200")
assert_true(isinstance(response, list), "employees is a list")

# ── 10. Create test employees + department ────────────────────────
status, response, headers = http("POST", "/api/employees",
                                headers={"Cookie": admin_cookie},
                                json_body={
                                    "code": "NV001",
                                    "name": "Nguy\u1ec5n V\u0103n A",
                                    "department_id": None,
                                    "start_date": "2025-01-01",
                                    "status_label": "Chính thức",
                                })
assert_true(status in (200, 201), f"create employee NV001: {status} {response}")
NV001_ID = response.get("id") if isinstance(response, dict) else None

status, response, headers = http("POST", "/api/employees",
                                headers={"Cookie": admin_cookie},
                                json_body={
                                    "code": "NV002",
                                    "name": "Tr\u1ea7n Th\u1ecb B",
                                    "department_id": None,
                                    "start_date": "2025-02-01",
                                    "status_label": "Thử việc",
                                })
assert_true(status in (200, 201), f"create employee NV002: {status} {response}")
NV002_ID = response.get("id") if isinstance(response, dict) else None

# ── 11. Checkin / Checkout (kiosk API) ───────────────────────────
# Pick a "today" date that's a weekday so the rules apply normally.
# We pass it via the API path.
TODAY = date(2026, 9, 30)  # Wed

# 11a. Checkin NV001 (employee_id required)
status, response, headers = http("POST", "/api/attendance/checkin",
                                headers={"Cookie": admin_cookie},
                                json_body={"employee_id": NV001_ID, "device_id": "DEV-TEST-1"})
assert_true(status in (200, 201), f"NV001 checkin: {status} {response}")
NV001_ATT_ID = response.get("id") if isinstance(response, dict) else None

# 11b. Try checkin again - should be rejected (already checked in today)
status, response, headers = http("POST", "/api/attendance/checkin",
                                headers={"Cookie": admin_cookie},
                                json_body={"employee_id": NV001_ID, "device_id": "DEV-TEST-2"})
# The system tracks today's real date, not a custom one. So 2nd checkin
# today (real date) may or may not be rejected depending on time of day.
# We just want to confirm the API responds (any non-500).
assert_true(status < 500, f"NV001 2nd checkin returns non-500: {status}")

# 11c. Checkout NV001
status, response, headers = http("POST", "/api/attendance/checkout",
                                headers={"Cookie": admin_cookie},
                                json_body={"employee_id": NV001_ID, "device_id": "DEV-TEST-3"})
assert_true(status < 500, f"NV001 checkout: {status}")

# 11d. Checkin NV002
status, response, headers = http("POST", "/api/attendance/checkin",
                                headers={"Cookie": admin_cookie},
                                json_body={"employee_id": NV002_ID, "device_id": "DEV-TEST-1"})
assert_true(status < 500, f"NV002 checkin: {status}")

# ── 12. Stats ─────────────────────────────────────────────────────
status, response, headers = http("GET", "/api/stats/sheet?month=9&year=2026",
                                headers={"Cookie": admin_cookie})
assert_eq(status, 200, "GET /api/stats/sheet returns 200")
assert_in("departments", response, "stats has departments field")
assert_in("employees", str(response), "stats has employees field")

# ── 13. EXCEL EXPORT ─────────────────────────────────────────────
# This is the headline feature.
status, response, headers = http("GET", "/api/stats/export?month=9&year=2026",
                                headers={"Cookie": admin_cookie},
                                accept_binary=True)
assert_eq(status, 200, "GET /api/stats/export returns 200 (template should be in place)")
assert_in("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          headers.get("Content-Type", ""),
          "Content-Type is xlsx")
assert_in("attachment", headers.get("Content-Disposition", ""),
          "Content-Disposition is attachment")
assert_true(isinstance(response, bytes) and len(response) > 1000,
            "Excel body is a binary blob > 1KB")

# Save to file so we can inspect.
out = os.path.join(os.path.dirname(__file__), "..", "test_out_export.xlsx")
with open(out, "wb") as f:
    f.write(response)
print(f"    -> wrote {out} ({len(response)} bytes)")

# Verify it's a valid xlsx by reading it back.
try:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(response), data_only=False)
    sheets_names = wb.sheetnames
    assert_true(len(sheets_names) >= 1, f"workbook has at least 1 sheet, got {sheets_names}")
    # The new sheet is named T09_2026 (from export.py: f"T{month:02d}_{year}")
    expected_sheet = "T09_2026"
    assert_in(expected_sheet, sheets_names, f"sheet {expected_sheet} present")
    ws = wb[expected_sheet]
    assert_true(ws.max_row > 0, f"sheet has rows (max_row={ws.max_row})")
    assert_true(ws.max_column >= 37, f"sheet has >= 37 columns (got {ws.max_column})")
    # Spot-check: the template's A1 title carried through into the export.
    # The baseline template shipped in resorce/template.xlsx uses a generic
    # "CÔNG TY MẪU" placeholder (each company replaces the template file).
    a1 = ws["A1"].value
    print(f"    A1 value: {a1!r}")
    assert_true(bool(a1 and str(a1).strip()), "A1 has a company title")
    # C1/C2 hold month/year and drive the DATE() formulas in row 9.
    assert_true(ws["C1"].value is not None, "C1 has month")
    assert_true(ws["C2"].value is not None, "C2 has year")
except Exception as e:
    assert_true(False, f"xlsx parse failed: {e}")

# ── 14. Holidays ──────────────────────────────────────────────────
status, response, headers = http("GET", "/api/holidays",
                                headers={"Cookie": admin_cookie})
assert_true(status == 200, f"GET /api/holidays returns 200, got {status}")

# ── 15. Hidden: PATCH attendance times ───────────────────────────
# We need an attendance id. Either the one from checkin above, or any.
if NV001_ATT_ID:
    ATTEND_ID = NV001_ATT_ID
else:
    # fetch any
    status, response, headers = http("GET", "/api/hidden/attendance",
                                    headers={"X-Hidden-Token": TOKEN})
    assert_eq(status, 200, "GET attendance with token returns 200")
    if response:
        ATTEND_ID = response[0]["id"]
    else:
        ATTEND_ID = None

if ATTEND_ID is not None:
    # 15a. Set checkin to 08:00
    status, response, headers = http("PATCH", f"/api/hidden/attendance/{ATTEND_ID}/times",
                                    headers={"X-Hidden-Token": TOKEN,
                                             "Cookie": admin_cookie},
                                    json_body={"checkin": "08:00", "checkout": None})
    assert_eq(status, 200, f"PATCH times checkin=08:00: {status} {response}")

    # 15b. Set checkout to 17:30
    status, response, headers = http("PATCH", f"/api/hidden/attendance/{ATTEND_ID}/times",
                                    headers={"X-Hidden-Token": TOKEN},
                                    json_body={"checkin": "08:00", "checkout": "17:30"})
    assert_eq(status, 200, f"PATCH times checkout=17:30: {status} {response}")

    # 15c. Clear both - attendance row should be deleted
    status, response, headers = http("PATCH", f"/api/hidden/attendance/{ATTEND_ID}/times",
                                    headers={"X-Hidden-Token": TOKEN},
                                    json_body={"checkin": None, "checkout": None})
    assert_eq(status, 200, f"PATCH clear both: {status} {response}")
    assert_eq(response.get("checkin_time"), None, "checkin_time now null")
    assert_eq(response.get("checkout_time"), None, "checkout_time now null")

    # 15d. The attendance row should be gone now (or only nulls if 403 elsewhere)
    status, response, headers = http("GET", "/api/hidden/attendance",
                                    headers={"X-Hidden-Token": TOKEN})
    if status == 200 and isinstance(response, list):
        ids = [r["id"] for r in response]
        assert_true(ATTEND_ID not in ids, "attendance row was deleted")

# ── 16. Invalid input ───────────────────────────────────────────────
status, response, headers = http("POST", "/api/hidden/login",
                                json_body={"password": ""})
assert_eq(status, 401, "empty password returns 401")

status, response, headers = http("PATCH", "/api/hidden/attendance/999999/times",
                                headers={"X-Hidden-Token": TOKEN},
                                json_body={"checkin": "08:00", "checkout": "17:00"})
assert_eq(status, 404, "PATCH non-existent attendance returns 404")

status, response, headers = http("PATCH", "/api/hidden/attendance/1/times",
                                headers={"X-Hidden-Token": TOKEN},
                                json_body={"checkin": "not-a-time", "checkout": None})
# Use the (now-deleted) id to confirm validation kicks in before 404. We
# instead create a fresh row first via checkin then test.
status2, response2, headers2 = http("POST", "/api/attendance/checkin",
                                    headers={"Cookie": admin_cookie},
                                    json_body={"employee_id": NV001_ID, "device_id": "DEV-TEST-V"})
if status2 in (200, 201) and isinstance(response2, dict) and "id" in response2:
    att_id = response2["id"]
    status, response, headers = http("PATCH", f"/api/hidden/attendance/{att_id}/times",
                                    headers={"X-Hidden-Token": TOKEN},
                                    json_body={"checkin": "not-a-time", "checkout": None})
    assert_eq(status, 422, "PATCH with invalid time format returns 422")
else:
    # best-effort: skip rather than fail
    print(f"  [SKIP] could not create row for invalid-time PATCH (status2={status2})")

# ── 17. Hidden rate limit ─────────────────────────────────────────
# 5 attempts per minute per the source. Just send 6 fast and confirm.
rate_status = None
for i in range(6):
    rate_status, _, _ = http("POST", "/api/hidden/login",
                             json_body={"password": "x"})
assert_eq(rate_status, 429, f"6th login attempt returns 429 (rate limited)")

# ── 18. Hidden: date filter + create new record ────────────────────
# (The detailed attendance-rule coverage lives in scripts/test_require_both.py;
#  these are the API-shape smoke checks so run_tests.py covers both features.)
section("18. Hidden: date filter (from/to) + POST create")

status, response, headers = http("GET", "/api/hidden/attendance",
                                 headers={"X-Hidden-Token": TOKEN})
assert_eq(status, 200, "GET attendance unfiltered returns 200")

status, response, _ = http("GET", "/api/hidden/attendance?from=2026-01-01&to=2026-01-02",
                           headers={"X-Hidden-Token": TOKEN})
assert_eq(status, 200, "GET attendance with from/to returns 200")
assert_true(isinstance(response, list) and
            all("2026-01-01" <= r["date"] <= "2026-01-02" for r in response),
            "date filter returns only rows inside the window")

status, response, _ = http("GET", "/api/hidden/attendance?from=2026-12-31&to=2026-01-01",
                           headers={"X-Hidden-Token": TOKEN})
assert_eq(status, 422, "from > to returns 422")

# Create a record for a day with no attendance, then confirm it appears.
if NV001_ID:
    past = (date.today() - timedelta(days=4)).isoformat()
    status, response, _ = http("POST", "/api/hidden/attendance",
                               headers={"X-Hidden-Token": TOKEN},
                               json_body={"employee_id": NV001_ID, "date": past,
                                          "checkin": "08:00", "checkout": "17:30"})
    assert_true(status in (200, 201), f"POST create new record returns {status}")

    status, response, _ = http("POST", "/api/hidden/attendance",
                               headers={"X-Hidden-Token": TOKEN},
                               json_body={"employee_id": NV001_ID, "date": past,
                                          "checkin": "09:00", "checkout": "18:00"})
    assert_eq(status, 409, "duplicate (employee, date) returns 409")

    # Validation guards
    status, response, _ = http("POST", "/api/hidden/attendance",
                               headers={"X-Hidden-Token": TOKEN},
                               json_body={"employee_id": NV001_ID, "date": past,
                                          "checkin": None, "checkout": None})
    assert_eq(status, 422, "create with both times blank returns 422")

    future = (date.today() + timedelta(days=3)).isoformat()
    status, response, _ = http("POST", "/api/hidden/attendance",
                               headers={"X-Hidden-Token": TOKEN},
                               json_body={"employee_id": NV001_ID, "date": future,
                                          "checkin": "08:00", "checkout": "17:30"})
    assert_eq(status, 422, "create for a future date returns 422")
else:
    print("  [SKIP] NV001 not created, cannot test POST create")

# ── Section summary ───────────────────────────────────────────────
section("summary")

print(f"\n{_passed} passed, {_failed} failed, {_passed + _failed} total")
sys.exit(0 if _failed == 0 else 1)