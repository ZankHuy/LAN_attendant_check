"""End-to-end tests for the two new behaviours:

  A) Attendance rule: a day only earns work value when BOTH checkin and
     checkout exist (missing either one => 0 công, on every day T2-T7).
  B) /hidden enhancements: `?from=&to=` date filtering, and
     POST /api/hidden/attendance to create a record for a day that has none.

Prereqs: server running on BASE with HIDDEN_PASSWORD=123456.

Run: py -3.11 scripts/test_require_both.py
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
HIDDEN_PASSWORD = os.environ.get("HIDDEN_PASSWORD", "123456")

# Work on days that are definitely in the past and land in the *current* pay
# period only if today is late in the month; to stay robust we test through
# /api/hidden + /api/stats/sheet for the period that contains the chosen date.
TODAY = date.today()

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
    except urllib.error.URLError as e:
        print(f"Cannot reach {BASE}: {e}")
        sys.exit(2)


# ── Auth ───────────────────────────────────────────────────────────────
section("Auth")
auth = "Basic " + base64.b64encode(b"admin:admin123").decode()
req = urllib.request.Request(BASE + "/api/auth/login", method="POST",
                             headers={"Authorization": auth})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        cookie = (r.headers.get("Set-Cookie") or "").split(";")[0]
except Exception as e:
    print(f"admin login failed: {e}")
    sys.exit(2)
ok("admin logged in")

status, tok = http("POST", "/api/hidden/login", json_body={"password": HIDDEN_PASSWORD})
if status != 200:
    print(f"hidden login failed: {status} {tok}")
    print("Set HIDDEN_PASSWORD env on the server (default expected: 123456)")
    sys.exit(2)
TOKEN = tok["token"]
HID = {"X-Hidden-Token": TOKEN}
ok("hidden token issued")


# ── Pick a past weekday (and a past Saturday) to test on ───────────────
def past_weekday(offset_days: int) -> date:
    """A date strictly in the past that is Mon-Fri."""
    d = TODAY - timedelta(days=offset_days)
    while d.weekday() >= 5:          # skip Sat(5)/Sun(6)
        d -= timedelta(days=1)
    return d


def past_saturday(offset_days: int) -> date:
    """A date strictly in the past that is a Saturday."""
    d = TODAY - timedelta(days=offset_days)
    while d.weekday() != 5:
        d -= timedelta(days=1)
    return d


WD = past_weekday(3)          # ~3 days ago, a weekday
SAT = past_saturday(5)        # ~1 week ago, a Saturday
print(f"  using weekday={WD} (dow={WD.weekday()}) and saturday={SAT} (dow={SAT.weekday()})")

# Create a dedicated employee so we never disturb existing data.
EMP_CODE = "RBTEST1"
status, emps = http("GET", "/api/employees")
emp_id = None
if status == 200 and isinstance(emps, list):
    for e in emps:
        if e["code"] == EMP_CODE:
            emp_id = e["id"]
            break
if emp_id is None:
    status, created = http("POST", "/api/employees",
                           json_body={"code": EMP_CODE, "name": "Test Quy Tắc 2 Giờ"})
    if status in (200, 201):
        emp_id = created["id"]
        ok(f"created employee {EMP_CODE} id={emp_id}")
    else:
        fail(f"could not create employee: {status} {created}")
        sys.exit(1)


def clear_day(emp, d):
    """Remove any attendance/time_log for (emp, d) so tests start clean."""
    status, lst = http("GET", f"/api/hidden/attendance?from={d}&to={d}", headers=HID)
    if status == 200 and isinstance(lst, list):
        for row in lst:
            if row["employee_id"] == emp:
                http("PATCH", f"/api/hidden/attendance/{row['id']}/times",
                     headers=HID, json_body={"checkin": None, "checkout": None})


def day_detail(emp, d):
    """Fetch the (employee, date) cell from the stats sheet, or None."""
    # Pay period is 26th of prev month .. 25th of the pay month. So a date
    # on/after the 26th belongs to the NEXT month's pay period.
    if d.day >= 26:
        period = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    else:
        period = (d.year, d.month)
    py_, pm = period
    status, sheet = http("GET", f"/api/stats/sheet?month={pm}&year={py_}")
    if status != 200 or not isinstance(sheet, dict):
        return None
    for dept in sheet.get("departments", []):
        for e in dept.get("employees", []):
            if e["employee_id"] == emp:
                for det in e.get("details", []):
                    if str(det["date"]) == d.isoformat():
                        return det
    return None


# ── A1. Weekday with only checkin => 0 công ───────────────────────────
section("A1. Weekday: checkin only (no checkout) => 0 công")
clear_day(emp_id, WD)
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": WD.isoformat(),
                            "checkin": "08:00", "checkout": None})
if status in (200, 201):
    ok(f"created checkin-only record for {WD}")
else:
    fail(f"create checkin-only failed: {status} {r}")

det = day_detail(emp_id, WD)
if det is None:
    fail(f"could not read {WD} from stats sheet")
elif det["work_value"] != 0:
    fail("checkin-only should be 0 công", f"got work_value={det['work_value']} status={det['status']}")
else:
    ok(f"work_value=0 as expected (status={det['status']})")
    if det["status"] != "no_checkout":
        fail("expected status='no_checkout'", f"got {det['status']}")
    else:
        ok("status='no_checkout'")

# ── A2. Same day, add checkout => 1 công ──────────────────────────────
section("A2. Same weekday + checkout 17:30 => 1 công")
status, lst = http("GET", f"/api/hidden/attendance?from={WD}&to={WD}", headers=HID)
row = next((x for x in lst if x["employee_id"] == emp_id), None) if status == 200 else None
if not row:
    fail("could not find the record to patch")
else:
    status, r = http("PATCH", f"/api/hidden/attendance/{row['id']}/times", headers=HID,
                     json_body={"checkin": "08:00", "checkout": "17:30"})
    if status == 200:
        ok("patched checkout 17:30")
    else:
        fail(f"patch failed: {status} {r}")
    det = day_detail(emp_id, WD)
    if det and det["work_value"] == 1:
        ok("work_value=1 now that both times exist")
    else:
        fail("expected work_value=1", f"got {det and det['work_value']} status={det and det['status']}")

# ── A3. Saturday with only checkin => 0 công ──────────────────────────
section("A3. Saturday: checkin only (no checkout) => 0 công")
clear_day(emp_id, SAT)
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": SAT.isoformat(),
                            "checkin": "08:00", "checkout": None})
if status in (200, 201):
    ok(f"created Saturday checkin-only record for {SAT}")
else:
    fail(f"create Saturday checkin-only failed: {status} {r}")

det = day_detail(emp_id, SAT)
if det is None:
    fail(f"could not read {SAT} from stats sheet")
elif det["work_value"] != 0:
    fail("Saturday checkin-only should be 0 công", f"got work_value={det['work_value']}")
else:
    ok("Saturday checkin-only => work_value=0 (rule applies to T7 too)")

# Saturday with both times => 0.5
status, lst = http("GET", f"/api/hidden/attendance?from={SAT}&to={SAT}", headers=HID)
row = next((x for x in lst if x["employee_id"] == emp_id), None) if status == 200 else None
if row:
    http("PATCH", f"/api/hidden/attendance/{row['id']}/times", headers=HID,
         json_body={"checkin": "08:00", "checkout": "12:00"})
    det = day_detail(emp_id, SAT)
    if det and det["work_value"] == 0.5:
        ok("Saturday with both times => work_value=0.5")
    else:
        fail("expected Saturday 0.5", f"got {det and det['work_value']}")


# ── B1. Date filter on the list endpoint ──────────────────────────────
section("B1. GET /api/hidden/attendance?from=&to= filters by date")
status, allrows = http("GET", "/api/hidden/attendance", headers=HID)
if status == 200 and isinstance(allrows, list):
    ok(f"unfiltered list returns {len(allrows)} rows")
else:
    fail(f"unfiltered list failed: {status}")

status, win = http("GET", f"/api/hidden/attendance?from={WD}&to={WD}", headers=HID)
if status == 200 and isinstance(win, list):
    if all(WD.isoformat() == x["date"] for x in win):
        ok(f"window {WD}..{WD} only contains that date ({len(win)} rows)")
    else:
        fail("date filter leaked other dates", str({x["date"] for x in win}))
else:
    fail(f"date-filtered list failed: {status} {win}")

status, ranged = http("GET", f"/api/hidden/attendance?from={SAT}&to={WD}", headers=HID)
if status == 200 and isinstance(ranged, list):
    if all(SAT.isoformat() <= x["date"] <= WD.isoformat() for x in ranged):
        ok(f"range {SAT}..{WD} respects both bounds ({len(ranged)} rows)")
    else:
        fail("range filter leaked out-of-range dates",
             str({x["date"] for x in ranged}))

# from > to => 422
status, r = http("GET", f"/api/hidden/attendance?from={WD}&to={SAT}", headers=HID)
if status == 422:
    ok("from > to rejected with 422")
else:
    fail("from > to should be 422", f"got {status} {r}")

# absurd range => 422
status, r = http("GET", "/api/hidden/attendance?from=2000-01-01&to=2026-01-01", headers=HID)
if status == 422:
    ok("over-wide range rejected with 422")
else:
    fail("over-wide range should be 422", f"got {status}")


# ── B2. POST create validation ────────────────────────────────────────
section("B2. POST /api/hidden/attendance validation")
D_NEW = past_weekday(10)

# both blank => 422
clear_day(emp_id, D_NEW)
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": D_NEW.isoformat(),
                            "checkin": None, "checkout": None})
if status == 422:
    ok("both times blank rejected with 422")
else:
    fail("both blank should be 422", f"got {status} {r}")

# future date => 422
D_FUT = TODAY + timedelta(days=3)
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": D_FUT.isoformat(),
                            "checkin": "08:00", "checkout": "17:00"})
if status == 422:
    ok(f"future date ({D_FUT}) rejected with 422")
else:
    fail("future date should be 422", f"got {status} {r}")

# unknown employee => 404
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": 99999999, "date": D_NEW.isoformat(),
                            "checkin": "08:00", "checkout": "17:00"})
if status == 404:
    ok("unknown employee rejected with 404")
else:
    fail("unknown employee should be 404", f"got {status} {r}")

# bad time format => 422
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": D_NEW.isoformat(),
                            "checkin": "abc", "checkout": "17:00"})
if status == 422:
    ok("bad HH:MM rejected with 422")
else:
    fail("bad time format should be 422", f"got {status} {r}")

# valid create => 201
clear_day(emp_id, D_NEW)
status, created = http("POST", "/api/hidden/attendance", headers=HID,
                       json_body={"employee_id": emp_id, "date": D_NEW.isoformat(),
                                  "checkin": "08:00", "checkout": "17:30"})
if status in (200, 201) and isinstance(created, dict) and created.get("id"):
    ok(f"created new record id={created['id']} for {D_NEW}")
    det = day_detail(emp_id, D_NEW)
    if det and det["work_value"] == 1:
        ok("newly created full-day record => work_value=1")
    else:
        fail("newly created record should be 1 công", f"got {det and det['work_value']}")
else:
    fail(f"valid create failed: {status} {created}")

# duplicate => 409
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": D_NEW.isoformat(),
                            "checkin": "09:00", "checkout": "18:00"})
if status == 409:
    ok("duplicate (employee, date) rejected with 409")
else:
    fail("duplicate should be 409", f"got {status} {r}")

# only-one-time create is allowed (but scores 0 công by policy)
D_HALF = past_weekday(15)
clear_day(emp_id, D_HALF)
status, r = http("POST", "/api/hidden/attendance", headers=HID,
                 json_body={"employee_id": emp_id, "date": D_HALF.isoformat(),
                            "checkin": "08:00", "checkout": None})
if status in (200, 201):
    ok("creating with only checkin is allowed")
    det = day_detail(emp_id, D_HALF)
    if det and det["work_value"] == 0:
        ok("one-time-only record => work_value=0 (matches policy)")
    else:
        fail("one-time-only should be 0 công", f"got {det and det['work_value']}")
else:
    fail(f"one-time-only create should be allowed: {status} {r}")


# ── Cleanup ───────────────────────────────────────────────────────────
section("Cleanup test data")
for d in (WD, SAT, D_NEW, D_HALF):
    clear_day(emp_id, d)
ok("cleared test attendance rows")

print(f"\n{'=' * 50}")
print(f"passed: {_passed}   failed: {_failed}")
print(f"{'=' * 50}")
sys.exit(1 if _failed else 0)
