"""
Export Excel bang cham cong.

Behavior:
- Load template from `resorce/{TEMPLATE_FILENAME}` (read-only, never modify
  it on disk). Filename can be overridden via the `EXPORT_TEMPLATE_FILE`
  environment variable.
- Copy the template's active sheet into a NEW sheet named `T{month}_{year}`
  inside a fresh in-memory workbook (so the new sheet inherits the
  template's styles, formulas, column widths, and merged cells).
- In the new sheet, clear the example employee body (rows 12..31) so that
  the template's reference data does NOT leak into the export and produce
  duplicate-name rows.
- Write fresh attendance data into the cleared body starting at row 12.
- Save the workbook as a NEW file `BangChamCong_T{month}_{year}.xlsx` and
  stream it to the client. The template file on disk is never touched.
"""
import io
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from app.database import get_db
from app import crud
from app.schemas import SheetData
from app.routers.auth import _require_session

router = APIRouter(prefix="/api/stats", tags=["stats"])

TEMPLATE_FILENAME = os.environ.get(
    "EXPORT_TEMPLATE_FILE",
    # Put your template .xlsx under ./resorce/ and either rename it to this
    # default, or set EXPORT_TEMPLATE_FILE env var to its filename.
    "template.xlsx",
)
TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "resorce",
    TEMPLATE_FILENAME,
)

# ---------- Style helpers ----------
THIN = Side(style='thin', color='B0B0B0')
BORDER_THIN = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
CENTER_TOP = Alignment(horizontal='center', vertical='top', wrap_text=True)
LEFT = Alignment(horizontal='left', vertical='center', indent=1)
DEPT_FILL = PatternFill('solid', fgColor='4472C4')
DEPT_FONT = Font(name='Times New Roman', size=11, bold=True, color='FFFFFF')
BODY_FONT = Font(name='Times New Roman', size=10)
BODY_FONT_BOLD = Font(name='Times New Roman', size=10, bold=True)


def _day_value(d: dict) -> Optional[float]:
    """Map a day record to the cell value used in the template.

    Returns 1, 0.5, 0, or None (blank for Sunday/weekend/absent).

    We prefer `symbol` (a numeric string the CRUD layer computes) over
    `status` because the policy now says e.g. "Saturday worked => 0.5" and
    "weekday half-day rule => 0.5" regardless of underlying `status` value
    (which may be "late" / "early_leave" / "on_time" depending on why it
    was less than full). Reading from `symbol` keeps export aligned with
    the live counters (days_on_time / days_late / half_days).
    """
    dow = d.get("day_of_week")
    symbol = d.get("symbol")
    if symbol in ("1", 1):
        return 1
    if symbol in ("0.5", 0.5):
        return 0.5
    # No explicit numeric symbol -> fall back to status-based decision
    # (covers weekends, Sundays, absent days which have no symbol).
    status = d.get("status")
    if dow == 6 or status == "weekend":
        return None
    return 0


def _clear_body_rows(ws: Worksheet, first_row: int, last_row: int) -> None:
    """Clear all cell values/styles in the given row range across the full sheet width.
    Also un-merges any merged ranges that intersect the row range, so the cleared
    area is clean and writable.
    """
    # Unmerge ranges that intersect the rows we are about to clear.
    to_unmerge = []
    for merged in ws.merged_cells.ranges:
        if merged.min_row >= first_row and merged.max_row <= last_row:
            to_unmerge.append(str(merged))
    for rng in to_unmerge:
        ws.unmerge_cells(rng)

    max_col = ws.max_column
    for r in range(first_row, last_row + 1):
        for c in range(1, max_col + 1):
            cell = ws.cell(r, c)
            cell.value = None
            # Reset to a clean body style.
            cell.font = BODY_FONT
            cell.alignment = CENTER
            cell.border = BORDER_THIN
            cell.fill = PatternFill(fill_type=None)


def _copy_template_to_new_workbook(template_ws: Worksheet, new_sheet_name: str) -> Worksheet:
    """Build a fresh Workbook whose only data sheet is a copy of `template_ws`.

    We do NOT open the template file with `copy_worksheet` because that would
    mutate the in-memory template workbook too. Instead, we build a brand-new
    Workbook and then deep-copy every cell (value, formula, style, fill, border,
    alignment, number_format) plus column widths, row heights, merged cells and
    print options. The result is a clean, detached clone under the new name.
    """
    new_wb = Workbook()
    # Remove the default empty sheet that openpyxl creates.
    default_ws = new_wb.active
    new_wb.remove(default_ws)

    new_ws = new_wb.create_sheet(title=new_sheet_name)

    # ---- 1. Column widths ----
    for col_letter, dim in template_ws.column_dimensions.items():
        if dim.width:
            new_ws.column_dimensions[col_letter].width = dim.width

    # ---- 2. Row heights ----
    for row_idx, dim in template_ws.row_dimensions.items():
        if dim.height:
            new_ws.row_dimensions[row_idx].height = dim.height

    # ---- 3. Cell-by-cell copy: value, formula, style ----
    max_row = template_ws.max_row
    max_col = template_ws.max_column
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            src = template_ws.cell(r, c)
            dst = new_ws.cell(r, c, value=src.value)
            if src.has_style:
                dst.font = src.font.copy()
                dst.fill = src.fill.copy()
                dst.border = src.border.copy()
                dst.alignment = src.alignment.copy()
                dst.number_format = src.number_format
                dst.protection = src.protection.copy()

    # ---- 4. Merged ranges ----
    for merged in template_ws.merged_cells.ranges:
        new_ws.merge_cells(str(merged))

    # ---- 5. Sheet-level options ----
    new_ws.sheet_format = template_ws.sheet_format
    new_ws.print_options = template_ws.print_options
    new_ws.page_margins = template_ws.page_margins
    new_ws.page_setup = template_ws.page_setup

    return new_wb, new_ws


@router.get("/export")
def export_excel(
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    # Input validation: refuse obviously invalid ranges with 422 instead of
    # letting the downstream code crash on date(year, month-1, 26) or similar.
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=422,
            detail=f"month must be between 1 and 12 (got {month})",
        )
    if not (1900 <= year <= 2999):
        raise HTTPException(
            status_code=422,
            detail=f"year must be between 1900 and 2999 (got {year})",
        )

    # Get the sheet data (same as /api/stats/sheet)
    sheet = crud.get_sheet_data(db, year, month)
    departments: list[dict] = sheet["departments"]

    # Load template (READ-ONLY - we never write this workbook back to disk)
    if not os.path.exists(TEMPLATE_PATH):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Excel template not found at {TEMPLATE_PATH}. "
                "Place a .xlsx template under ./resorce/ and either name it "
                f"'{TEMPLATE_FILENAME}' or set EXPORT_TEMPLATE_FILE env var. "
                "See resorce/README.md for details."
            ),
        )
    template_wb = load_workbook(TEMPLATE_PATH)
    template_ws = template_wb.active  # The template's "Sheet1"

    # Build a fresh workbook that has a new sheet named after the pay period,
    # cloned from the template. This way we never overwrite or mutate the
    # template's "Sheet1" (which contains the example reference employees).
    new_sheet_name = f"T{month:02d}_{year}"
    new_wb, ws = _copy_template_to_new_workbook(template_ws, new_sheet_name)

    # Drop the in-memory template workbook reference - we don't need it.
    # (We never called wb.save() so the on-disk file is untouched.)
    template_wb.close()

    # Update month/year so DATE() formulas in row 9 recalculate.
    ws["C1"] = month
    ws["C2"] = year
    # Update the title row 5 to reflect the pay period
    if month == 1:
        period_str = f"26/12/{year - 1} - 25/01/{year}"
        period_start = date(year - 1, 12, 26)
    else:
        period_str = f"26/{month - 1:02d}/{year} - 25/{month:02d}/{year}"
        period_start = date(year, month - 1, 26)
    ws["A5"] = f"Kỳ chấm công: {period_str}"

    # ---- Fix row 9 G9: template uses =DATE(C2, C1, 26) which computes the
    #      26th of the *payment month* (e.g. month=8 -> 26/08/2026). The pay
    #      period for "month=8" is actually 26/07/2026 -> 25/08/2026, so G9
    #      needs to start at the 26th of the *previous* month.
    #      We write G9 as a concrete date (not a formula) so it always
    #      matches A5. H9..AK9 keep their "+1" formulas and recompute
    #      automatically, which also means row 7 (=DAY(<col>9)) and row 11
    #      (=CHOOSE(WEEKDAY(<col>9), ...)) stay correct.
    ws["G9"] = period_start
    ws["G9"].number_format = "dd"  # day with leading zero, e.g. "26"

    # ---- Fix row 7: the template's hardcoded numbers 5..35 are stale (they
    #      don't match the actual pay period). Replace them with formulas
    #      =DAY(<col>9) so each cell shows the day-of-month for the date that
    #      row 9 computes. This auto-updates if C1/C2 (month/year) change.
    ROW7_COL_FIRST = 7   # G
    ROW7_COL_LAST = 37   # AK
    DAY_NUM_ROW = 7
    for c in range(ROW7_COL_FIRST, ROW7_COL_LAST + 1):
        col_letter = get_column_letter(c)
        cell = ws.cell(DAY_NUM_ROW, c)
        cell.value = f"=DAY({col_letter}9)"
        cell.number_format = "0"  # integer day, no thousand separator
        # Keep whatever font/fill/alignment/border the template set for that
        # cell so the row 7 visual style is preserved.

    # ---- Clear the template's example body rows so we don't get duplicate
    #      names from the reference data leaking into the export.
    # Body starts at row 12. Clear rows 12 through 31 inclusive (the template
    # also has a "Tổng" sum row at row 31 that we'd otherwise overwrite anyway,
    # so wiping it is safe).
    BODY_START_ROW = 12
    BODY_CLEAR_LAST_ROW = 31
    _clear_body_rows(ws, BODY_START_ROW, BODY_CLEAR_LAST_ROW)

    # Body rows start at row 12 (template convention)
    DATE_FIRST_COL = 7   # G
    DATE_LAST_COL = 37   # AK (35 columns; covers 31 days + a few buffers)
    SUMMARY_FIRST_COL = 38  # AL

    # Track which template columns exist for summaries
    summary_labels = {}
    for col in range(SUMMARY_FIRST_COL, ws.max_column + 1):
        lbl = ws.cell(9, col).value
        if lbl:
            summary_labels[str(lbl).strip().lower()] = col

    # Helper: write a dept separator row spanning the whole sheet
    def write_dept_separator(ws, row, dept_name, n_emp):
        text = f"{dept_name}  ({n_emp} nhân viên)"
        for col in range(1, ws.max_column + 1):
            c = ws.cell(row, col)
            c.value = text if col == 1 else None
            c.fill = DEPT_FILL
            c.font = DEPT_FONT
            c.border = BORDER_THIN
            c.alignment = LEFT if col == 1 else CENTER
        # Merge across the whole sheet
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ws.max_column)

    row_idx = BODY_START_ROW
    stt = 1
    for dept in departments:
        emps = dept.get("employees", [])
        if not emps:
            continue

        # Optional separator row for the department
        write_dept_separator(ws, row_idx, dept["department_name"], len(emps))
        row_idx += 1

        for emp in emps:
            # Info columns
            ws.cell(row_idx, 1, stt).font = BODY_FONT_BOLD
            ws.cell(row_idx, 2, emp.get("employee_code") or "").font = BODY_FONT
            ws.cell(row_idx, 3, emp.get("employee_name") or "").font = BODY_FONT
            ws.cell(row_idx, 4, dept["department_name"]).font = BODY_FONT
            ws.cell(row_idx, 5, emp.get("start_date") or "").font = BODY_FONT
            ws.cell(row_idx, 6, emp.get("status_label") or "").font = BODY_FONT

            # Day cells
            for i, d in enumerate(emp.get("details", [])):
                col = DATE_FIRST_COL + i
                if col > DATE_LAST_COL:
                    break
                val = _day_value(d)
                cell = ws.cell(row_idx, col)
                cell.value = val
                cell.font = BODY_FONT
                cell.alignment = CENTER_TOP
                cell.border = BORDER_THIN
                cell.number_format = "0.0;-0.0;\"\";@"

            # Summary columns - map every template column to a source field.
            label_map = {
                "công chuẩn":               emp.get("standard_workdays", 0),
                "công tt":                  emp.get("official_days", 0),
                "công  tác":                emp.get("business_trip_days", 0),
                "nghỉ lễ":                  emp.get("holiday_days", 0),
                "ngày lễ đi làm":           emp.get("holiday_work_days", 0),
                "nghỉ hưởng lương":         emp.get("paid_leave_days", 0),
                "nghỉ chế độ":              emp.get("maternity_leave_days", 0),
                "nghỉ bù":                  emp.get("compensatory_days", 0),
                "làm việc nvp":             emp.get("office_work_days", 0),
                "nghỉ phép":                emp.get("unpaid_leave_days", 0),
                "công hưởng lương":         emp.get("paid_work_days", 0),
                "công thử việc":            emp.get("trial_work_days", 0),
                "công chính thức":          emp.get("official_work_days", 0),
                "công tts":                 emp.get("tts_days", 0),
                "công học việc":            emp.get("intern_days", 0),
            }
            for lbl, col in summary_labels.items():
                if lbl in label_map:
                    val = label_map[lbl]
                    cell = ws.cell(row_idx, col, val)
                    cell.font = BODY_FONT_BOLD
                    cell.alignment = CENTER
                    cell.border = BORDER_THIN
                    cell.number_format = "0.0;-0.0;\"\";@"

            # Apply alignment/border to all info cells of this row
            for col in range(1, 7):
                c = ws.cell(row_idx, col)
                if c.alignment is None or c.alignment.horizontal is None:
                    c.alignment = LEFT if col in (3, 4) else CENTER
                c.border = BORDER_THIN

            stt += 1
            row_idx += 1

    # Save to in-memory buffer
    buf = io.BytesIO()
    new_wb.save(buf)
    new_wb.close()
    buf.seek(0)

    filename = f"BangChamCong_T{month:02d}_{year}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
