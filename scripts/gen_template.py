"""Generate a minimal-but-valid Excel template for `/api/stats/export`.

The export code (`app/app/routers/export.py`) reads/writes specific cells:

- C1 = month (1-12)            -- header
- C2 = year                    -- header
- A5 = "Kỳ chấm công: ..."     -- title (overwritten at runtime)
- Row 7  = day-of-month numbers =DAY(<col>9)         (overwritten at runtime)
- Row 9  = dates  G9=concrete, H9..AK9=`=<prev>9+1`
- Row 11 = weekday CHOOSE(WEEKDAY(<col>9), "T2", "T3", "T4", "T5", "T6", "T7", "CN")
- Rows 12..31 = body (cleared at runtime, but we leave 1 example row so the
  template's column widths / styles are visible to operators)
- Row 9 columns >= AL = summary labels (consumed by `label_map` in export.py)

This script writes `./resorce/template.xlsx`. It's reproducible — anyone
working on the repo can run it after `pip install openpyxl` to recreate a
template that passes the runtime smoke test (run_tests.py + manual export).
"""
from __future__ import annotations

import os
import sys

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl is required: pip install openpyxl==3.1.2")
    sys.exit(1)

# ---------- Style ----------
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FONT = Font(name="Times New Roman", size=14, bold=True)
TITLE_FONT = Font(name="Times New Roman", size=12, bold=True)
LABEL_FONT = Font(name="Times New Roman", size=10, bold=True)
BODY_FONT = Font(name="Times New Roman", size=10)
HEADER_FILL = PatternFill("solid", fgColor="4472C4")
WEEKEND_FILL = PatternFill("solid", fgColor="F2D2D2")
SUNDAY_FILL = PatternFill("solid", fgColor="F8D7DA")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", indent=1)

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resorce",
    "template.xlsx",
)


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"  # The export code uses template_ws = wb.active

    # ── A1: company name ─────────────────────────────────────────────
    # NOTE: keep C1 / C2 outside merged ranges — export.py reads them.
    ws["A1"] = "CÔNG TY MẪU"
    ws["A1"].font = HEADER_FONT
    ws["A1"].alignment = CENTER
    ws.merge_cells("A1:B1")
    ws.row_dimensions[1].height = 24

    # ── A2: subtitle (optional cosmetic header) ──────────────────────────
    ws["A2"] = "BẢNG CHẤM CÔNG NHÂN VIÊN"
    ws["A2"].font = TITLE_FONT
    ws["A2"].alignment = CENTER
    ws.merge_cells("A2:B2")
    ws.row_dimensions[2].height = 20

    # ── A5: pay-period title (overwritten by export) ─────────────────────
    ws["A5"] = "Kỳ chấm công: 26/<thang_truoc>/<nam> - 25/<thang>/<nam>"
    ws["A5"].font = TITLE_FONT
    ws["A5"].alignment = LEFT
    ws.merge_cells("A5:F5")

    # ── C1, C2: month + year header. Must NOT be inside any merged range.
    ws["C1"] = 9
    ws["C1"].alignment = CENTER
    ws["C1"].font = LABEL_FONT
    ws["C2"] = 2026
    ws["C2"].alignment = CENTER
    ws["C2"].font = LABEL_FONT

    # ── Row 6: info-column headers ─────────────────────────────────────
    headers_row6 = ["STT", "Mã NV", "Họ tên", "Phòng ban", "Ngày vào", "Trạng thái"]
    for col, text in enumerate(headers_row6, start=1):
        cell = ws.cell(6, col, text)
        cell.font = LABEL_FONT
        cell.fill = HEADER_FILL
        cell.font = Font(name="Times New Roman", size=10, bold=True, color="FFFFFF")
        cell.alignment = CENTER
        cell.border = BORDER
    ws.row_dimensions[6].height = 22

    # ── Row 9: dates. G9 = concrete (26 prev month), H9..AK9 = +1 ─────
    # The export code overwrites G9 with a real date, but we still need a
    # valid initial value so the formulas H9..AK9 evaluate to something sane
    # when an operator opens the file in Excel.
    import datetime as _dt
    ws["G9"] = _dt.date(2026, 8, 26)  # placeholder; export will overwrite
    ws["G9"].number_format = "dd"
    for c in range(8, 38):  # H..AK
        prev = get_column_letter(c - 1)
        cell = ws.cell(9, c, f"={prev}9+1")
        cell.number_format = "dd"
    ws.row_dimensions[9].height = 18

    # ── Row 11: weekday names via CHOOSE(WEEKDAY(...), "T2".."CN") ──────
    for c in range(7, 38):  # G..AK
        col = get_column_letter(c)
        cell = ws.cell(
            11, c,
            f'=CHOOSE(WEEKDAY({col}9), "T2", "T3", "T4", "T5", "T6", "T7", "CN")',
        )
        cell.alignment = CENTER
        cell.font = LABEL_FONT
        cell.border = BORDER
    ws.row_dimensions[11].height = 18

    # ── Row 7: day-of-month numbers. Export overwrites these with
    # `=DAY(<col>9)` but we still seed values so the file looks right
    # when opened standalone. ─────────────────────────────────────
    for c in range(7, 38):
        col = get_column_letter(c)
        cell = ws.cell(7, c, f"=DAY({col}9)")
        cell.number_format = "0"
        cell.alignment = CENTER
        cell.font = LABEL_FONT
        cell.border = BORDER

    # ── Summary columns (>= AL=38). Row 9 holds the label that export.py
    # matches against its `label_map`. Keep these labels exactly as the
    # code expects (lowercased + stripped). ───────────────────────────────
    SUMMARY_LABELS = [
        "Công chuẩn",
        "Công TT",
        "Công  tác",
        "Nghỉ lễ",
        "Ngày lễ đi làm",
        "Nghỉ hưởng lương",
        "Nghỉ chế độ",
        "Nghỉ bù",
        "Làm việc NVP",
        "Nghỉ phép",
        "Công hưởng lương",
        "Công thử việc",
        "Công chính thức",
        "Công TTS",
        "Công học việc",
    ]
    for i, label in enumerate(SUMMARY_LABELS):
        col = 38 + i
        cell = ws.cell(9, col, label)
        cell.font = LABEL_FONT
        cell.fill = HEADER_FILL
        cell.font = Font(name="Times New Roman", size=10, bold=True, color="FFFFFF")
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = 10

    # ── Body example rows (rows 12..31). Export deletes rows 12..31 before
    # writing live data, but we seed one example row so the file looks
    # sane when an operator opens it. ─────────────────────────────────
    example = ["NV001", "Nguyễn Văn A", "Phòng mẫu", "2024-01-15", "Chính thức"]
    for r in range(12, 32):
        ws.cell(r, 1, r - 11).font = BODY_FONT
        if r == 12:
            ws.cell(r, 2, example[0]).font = BODY_FONT
            ws.cell(r, 3, example[1]).font = BODY_FONT
            ws.cell(r, 4, example[2]).font = BODY_FONT
            ws.cell(r, 5, example[3]).font = BODY_FONT
            ws.cell(r, 6, example[4]).font = BODY_FONT
        for c in range(7, 38):
            cell = ws.cell(r, c, "")
            cell.alignment = CENTER
            cell.border = BORDER
            cell.font = BODY_FONT
        for c in range(1, 7):
            cell = ws.cell(r, c)
            cell.border = BORDER
            cell.alignment = LEFT if c in (3, 4) else CENTER

    # ── Column widths ──────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 12
    for c in range(7, 38):  # G..AK
        ws.column_dimensions[get_column_letter(c)].width = 4.5

    # ── Print setup: landscape, fit to 1 page wide ─────────────────────
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4

    # ── Save ─────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wb.save(OUT_PATH)
    print(f"Wrote {OUT_PATH}")
    print(f"  Sheet: {ws.title}")
    print(f"  Dimensions: {ws.max_row} rows x {ws.max_column} cols")
    print(f"  Summary labels: {len(SUMMARY_LABELS)} (columns AL..AM+{len(SUMMARY_LABELS) - 1})")


if __name__ == "__main__":
    main()