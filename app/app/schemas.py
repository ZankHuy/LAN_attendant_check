from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional, List

# Admin schemas
class AdminCreate(BaseModel):
    password: str

class AdminLogin(BaseModel):
    password: str

class ChangePassword(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str

# Employee schemas
class DepartmentCreate(BaseModel):
    name: str
    sort_order: int = 0

class DepartmentResponse(BaseModel):
    id: int
    name: str
    sort_order: int

    class Config:
        from_attributes = True

class EmployeeCreate(BaseModel):
    code: str
    name: str
    department_id: Optional[int] = None
    start_date: Optional[date] = None
    status_label: Optional[str] = None  # Chính thức / Thử việc / Học việc / TTS

class EmployeeUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    department_id: Optional[int] = None
    start_date: Optional[date] = None
    status_label: Optional[str] = None

    def get_set_fields(self):
        """Return names of fields explicitly set in the request payload.
        Used to distinguish 'not provided' vs 'set to null' (e.g. unassign department)."""
        return self.model_fields_set

class EmployeeResponse(BaseModel):
    id: int
    code: str
    name: str
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    start_date: Optional[date] = None
    status_label: Optional[str] = None

    class Config:
        from_attributes = True

# Attendance schemas
class CheckinRequest(BaseModel):
    employee_id: int
    device_id: str

class CheckoutRequest(BaseModel):
    employee_id: int
    device_id: str

class AttendanceResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    employee_code: str
    date: date
    checkin_time: Optional[datetime]
    checkout_time: Optional[datetime]
    is_on_time: bool
    is_early_leave: bool
    late_minutes: int = 0  # minutes late compared to shift deadline (0 if on time)
    shift: Optional[str] = None       # 'AM' | 'PM'
    is_full_day: bool = False         # True: checkin<=AM_deadline & checkout>=PM_deadline
    early_leave_minutes: Optional[int] = None  # minutes early from PM deadline

    class Config:
        from_attributes = True

class AttendanceStatus(BaseModel):
    has_checked_in: bool
    has_checked_out: bool
    checkin_time: Optional[datetime]
    checkout_time: Optional[datetime]
    employee_id: Optional[int]
    employee_name: Optional[str]
    late_minutes: int = 0  # minutes late compared to shift deadline
    shift: Optional[str] = None

class DeviceBanStatus(BaseModel):
    is_banned: bool
    remaining_minutes: int = 0
    action_type: Optional[str] = None  # 'checkin' or 'checkout'

# Settings schemas
class SettingResponse(BaseModel):
    key: str
    value: str

class BanDurationUpdate(BaseModel):
    duration_minutes: int

class SettingUpdate(BaseModel):
    am_checkin_deadline: str   # "HH:MM", ví dụ "08:40"
    pm_checkout_deadline: str   # "HH:MM", ví dụ "17:30"

class AttendanceSettingsResponse(BaseModel):
    am_checkin_deadline: str
    pm_checkout_deadline: str
    am_checkin_deadline_date: str
    pm_checkout_deadline_date: str

# Stats schemas
class DailyStats(BaseModel):
    date: date
    day_of_week: int  # 0=Mon, 6=Sun
    checkin_time: Optional[datetime]
    checkout_time: Optional[datetime]
    checkin_display: str = ""       # "HH:MM" | "" | "No"
    checkout_display: str = ""      # "HH:MM" | "" | "No"
    work_value: Optional[float] = None  # 1 | 0.5 | 0 | None (future)
    status: str  # on_time, late, early_leave, absent, weekend, holiday, future, no_checkout, half_day
    late_minutes: int
    early_minutes: int
    symbol: str = ""  # Ký hiệu hiển thị: 1, 0.5, 0, hoặc ""
    checkin_status: Optional[str] = None   # "normal" | "late" | "faulty" | None
    checkout_status: Optional[str] = None  # "normal" | "early" | "faulty" | "no_checkout" | None

class EmployeeStats(BaseModel):
    employee_id: int
    employee_code: str
    employee_name: str
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    start_date: Optional[date] = None
    status_label: Optional[str] = None
    days_on_time: int
    days_late: int
    days_absent: int
    half_days: int = 0
    total_late_minutes: int
    total_early_minutes: int
    # Extended summary fields (mapped to template columns)
    standard_workdays: int = 0
    official_days: float = 0
    business_trip_days: int = 0
    holiday_days: int = 0
    holiday_work_days: int = 0
    paid_leave_days: int = 0
    maternity_leave_days: int = 0
    compensatory_days: int = 0
    office_work_days: int = 0
    unpaid_leave_days: int = 0
    paid_work_days: float = 0
    trial_work_days: float = 0
    official_work_days: float = 0
    tts_days: int = 0
    intern_days: float = 0
    details: List[DailyStats]

class DepartmentStats(BaseModel):
    department_id: Optional[int]
    department_name: str  # "Tổng" nếu là tổng cuối
    employees: List[EmployeeStats]

class SheetData(BaseModel):
    period_start: date
    period_end: date
    departments: List[DepartmentStats]  # Mỗi phòng ban là 1 group
    dates: List[date]
    weekdays_per_week: int = 5  # Số ngày làm việc trong tuần (mặc định T2-T6 + sáng T7)