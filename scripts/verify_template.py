"""Quick smoke test: load resorce/template.xlsx and confirm export.py's
required cells/formulas are present and writable."""
import os
import sys

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resorce",
    "template.xlsx",
)

# Must mirror the cells export.py reads/writes.
EXPECTED_C1_DEFAULT = None  # any int 1..12 ok
EXPECTED_C2_DEFAULT = None  # any int >= 1900 ok
REQUIRED_ROW9_FORMULAS = {8: "=G9+1", 9: "=H9+1"}  # spot-check H9, I9
REQUIRED_ROW11_FORMULA_PREFIX = "=CHOOSE(WEEKDAY("
SUMMARY_LABELS_ROW9 = [
    "Công chuẩn", "Công TT", "Công  tác", "Nghỉ lễ",
    "Ngày lễ đi làm", "Nghỉ hưởng lương", "Nghỉ chế độ",
    "Nghỉ bù", "Làm việc NVP", "Nghỉ phép", "Công hưởng lương",
    "Công thử việc", "Công chính thức", "Công TTS", "Công học việc",
]


def main() -> int:
    if not os.path.exists(TEMPLATE_PATH):
        print(f"FAIL: {TEMPLATE_PATH} not found")
        return 1
    wb = load_workbook(TEMPLATE_PATH)
    ws = wb.active
    print(f"  Sheet name: {ws.title!r}")
    print(f"  Dimensions: {ws.max_row} x {ws.max_column}")

    issues = []

    # C1 / C2
    c1 = ws["C1"].value
    c2 = ws["C2"].value
    if not isinstance(c1, int) or not (1 <= c1 <= 12):
        issues.append(f"C1 expected int 1..12, got {c1!r}")
    if not isinstance(c2, int) or not (1900 <= c2 <= 2999):
        issues.append(f"C2 expected int 1900..2999, got {c2!r}")
    print(f"  C1={c1}, C2={c2}")

    # Row 9 G9 should be a date
    g9 = ws["G9"].value
    print(f"  G9={g9!r} (type={type(g9).__name__})")
    if not hasattr(g9, "year"):
        issues.append(f"G9 should be a date, got {g9!r}")

    # Spot-check H9..I9 formulas
    for col, expected in REQUIRED_ROW9_FORMULAS.items():
        actual = ws.cell(9, col).value
        if actual != expected:
            issues.append(f"{get_column_letter(col)}9: expected {expected!r}, got {actual!r}")

    # Row 11 should have CHOOSE(WEEKDAY(...)) formulas
    h11 = ws["H11"].value
    if not (isinstance(h11, str) and h11.startswith(REQUIRED_ROW11_FORMULA_PREFIX)):
        issues.append(f"H11 should start with {REQUIRED_ROW11_FORMULA_PREFIX!r}, got {h11!r}")

    # Summary labels in row 9, columns 38..52
    labels_found = []
    for col in range(38, ws.max_column + 1):
        v = ws.cell(9, col).value
        if v:
            labels_found.append(str(v).strip())
    missing = [s for s in SUMMARY_LABELS_ROW9 if s not in labels_found]
    if missing:
        issues.append(f"Missing summary labels in row 9: {missing}")
    print(f"  Summary labels found: {len(labels_found)} (need {len(SUMMARY_LABELS_ROW9)})")

    # Body rows 12..31 must exist (export clears them but template must have them).
    if ws.max_row < 31:
        issues.append(f"Template has only {ws.max_row} rows; export clears rows 12..31")

    wb.close()

    if issues:
        print("\nFAIL:")
        for i in issues:
            print(f"  - {i}")
        return 1
    print("\nOK: template passes structural smoke test")
    return 0


if __name__ == "__main__":
    sys.exit(main())