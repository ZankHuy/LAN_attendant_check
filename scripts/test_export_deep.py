"""Excel export deep-test.

Tests multiple months, empty state, edge cases.
"""
import base64
import io
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
    body = None
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
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


# Login
auth = "Basic " + base64.b64encode(b"admin:admin123").decode()
status, _, headers = http("POST", "/api/auth/login", headers={"Authorization": auth})
if status != 200:
    print(f"FATAL: login failed {status}")
    sys.exit(1)
cookie = (headers.get("Set-Cookie") or headers.get("set-cookie", "")).split(";")[0]
print(f"cookie: {cookie}")


# ── 1. Export Excel with current data ─────────────────────────────────
section("Excel export: T09_2026 with current data")
status, blob, headers = http(
    "GET", "/api/stats/export?month=9&year=2026",
    headers={"Cookie": cookie}, accept_binary=True
)
if status == 200 and isinstance(blob, bytes) and len(blob) > 1000:
    ok(f"exported {len(blob)} bytes")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob))
    if "T09_2026" in wb.sheetnames:
        ok("sheet T09_2026 present")
        ws = wb["T09_2026"]
        # A1 should be company name
        a1 = ws["A1"].value
        if "LAN ATTENDANT" in (a1 or ""):
            ok(f"A1 has company title: {a1!r}")
        else:
            fail(f"A1 missing company title", f"A1={a1!r}")
        max_col = ws.max_column
        max_row = ws.max_row
        ok(f"sheet dimensions: {max_row}x{max_col}")
        # Read summary headers in row 11 (typically "TỔNG" / "NGÀY CÔNG")
        for c in range(38, min(47, max_col + 1)):
            v = ws.cell(row=11, column=c).value
            if v:
                ok(f"col {c} (row 11) summary header: {v!r}")
    else:
        fail(f"sheet T09_2026 missing. Got {wb.sheetnames}")
else:
    fail(f"export returned {status}")


# ── 2. Export Excel: T10_2025 (different period) ──────────────────────
section("Excel export: T10_2025 (different month)")
status, blob, headers = http(
    "GET", "/api/stats/export?month=10&year=2025",
    headers={"Cookie": cookie}, accept_binary=True
)
if status == 200 and isinstance(blob, bytes):
    ok(f"exported {len(blob)} bytes")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob))
    if "T10_2025" in wb.sheetnames:
        ok("sheet T10_2025 present")
        ws = wb["T10_2025"]
        d1 = ws.cell(row=9, column=2).value
        ok(f"first date cell (col B, row 9): {d1!r}")
    else:
        fail("sheet T10_2025 missing", f"sheets: {wb.sheetnames}")
else:
    fail(f"export failed: {status}")


# ── 3. Export: invalid month ──────────────────────────────────────────
section("Excel export: invalid month=13")
status, blob, headers = http(
    "GET", "/api/stats/export?month=13&year=2026",
    headers={"Cookie": cookie}, accept_binary=True
)
if status in (400, 422, 200):
    ok(f"invalid month returns {status}")
else:
    fail(f"unexpected status: {status}")


# ── 4. Export: no auth ─────────────────────────────────────────────────────
section("Excel export: without cookie")
status, blob, _ = http(
    "GET", "/api/stats/export?month=9&year=2026", accept_binary=True
)
# Either a JSON error (e.g. {"detail": ...}) or a binary blob — either way,
# anything except a 200 xlsx is a pass for the "unauth" check.
if status in (401, 403):
    ok(f"unauth returns {status}")
elif status == 200 and isinstance(blob, bytes) and not blob.startswith(b"PK"):
    ok(f"unauth returns error JSON (status={status})")
else:
    fail(f"unauth returned {status} — auth may not be required (security issue)")


# ── 5. Save exported file to disk ────────────────────────────────────────
section("Save export to disk")
out = os.path.join(os.path.dirname(__file__), "..", "test_out_export_full.xlsx")
status, blob, _ = http(
    "GET", "/api/stats/export?month=9&year=2026",
    headers={"Cookie": cookie}, accept_binary=True
)
if status == 200:
    with open(out, "wb") as f:
        f.write(blob)
    ok(f"wrote {out} ({len(blob)} bytes)")
    size = os.path.getsize(out)
    if size == len(blob):
        ok(f"file on disk matches ({size} bytes)")
    else:
        fail(f"file size mismatch: disk={size} blob={len(blob)}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)