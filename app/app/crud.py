from sqlalchemy.orm import Session
from app.models import (
    Admin, User, Employee, Attendance, Device, DeviceBan, Setting, Department,
    TimeLog, Holiday,
)
from app.database import SessionLocal
from app.log_timelog import (
    record_checkin as _record_checkin,
    record_checkout as _record_checkout,
)
from datetime import datetime, date, time, timedelta
from typing import Optional, List
import bcrypt

# Default ban duration (minutes) used when no setting is configured.
DEFAULT_BAN_MINUTES = 30

# Standard checkin deadline (weekdays). 8:30 AM.
WEEKDAY_CHECKIN_DEADLINE = time(8, 30)
# Standard checkout deadline (weekdays). 6:00 PM.
WEEKDAY_CHECKOUT_DEADLINE = time(18, 0)
# Saturday checkout deadline. 12:00 PM.
SATURDAY_CHECKOUT_DEADLINE = time(12, 0)
# Half-day thresholds (weekdays Mon-Fri only): any employee who checks out
# before this time OR checks in after this time is counted as half-day (0.5)
# instead of a full day.
WEEKDAY_HALF_DAY_CHECKIN_THRESHOLD = time(12, 0)   # checkin after 12:00 -> half
WEEKDAY_HALF_DAY_CHECKOUT_THRESHOLD = time(13, 30) # checkout before 13:30 -> half

# AM/PM shift classification
AM_CHECKIN_LATEST = time(12, 0)      # checkin <= 12:00 = buổi sáng
AM_SHIFT_DEADLINE = time(8, 30)       # giờ vào ca sáng (default, có thể override)
PM_SHIFT_DEADLINE = time(13, 30)      # giờ vào ca chiều
PM_SHIFT_END = time(18, 0)            # giờ tan ca chuẩn
SATURDAY_SHIFT_END = time(12, 0)      # giờ tan ca T7


def compute_late_minutes(checkin_dt: datetime, shift: str = "AM", db: Session = None, target_date: date = None) -> int:
    """Return minutes late given a checkin datetime vs deadline for the shift.

    For AM: deadline is get_am_deadline(db, target_date) (default 8:30).
    For PM: deadline is 13:30 (fixed for now; configurable via settings later if needed).
    """
    if checkin_dt is None:
        return 0
    ci = checkin_dt.time()
    if shift == "AM" and db is not None:
        deadline = get_am_deadline(db, target_date)
    elif shift == "PM":
        deadline = PM_SHIFT_DEADLINE
    else:
        deadline = WEEKDAY_CHECKIN_DEADLINE
    if ci <= deadline:
        return 0
    ci_minutes = ci.hour * 60 + ci.minute
    dl_minutes = deadline.hour * 60 + deadline.minute
    return ci_minutes - dl_minutes

def init_db():
    """Seed default admin and default settings. Schema is the caller's job.

    The caller (app.main) is expected to have already invoked
    `run_migrations()` followed by `Base.metadata.create_all()`. This
    function only handles the row-level seed data.
    """
    db = SessionLocal()
    try:
        # Create default admin if not exists
        admin = db.query(Admin).first()
        if not admin:
            hashed = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt())
            admin = Admin(password_hash=hashed.decode())
            db.add(admin)
            db.commit()

        # Seed default settings
        if not db.query(Setting).filter(Setting.key == "ban_minutes").first():
            db.add(Setting(key="ban_minutes", value=str(DEFAULT_BAN_MINUTES)))
            db.commit()

        # Seed default departments if table is empty.
        # Left empty so each deployment can set up their own org chart via /admin.
        # (To pre-seed: add a Department(name=..., sort_order=...) entry below.)
    finally:
        db.close()

# ---------------- Settings ----------------
def get_ban_minutes(db: Session) -> int:
    """Read the configured ban duration in minutes."""
    setting = db.query(Setting).filter(Setting.key == "ban_minutes").first()
    if not setting:
        return DEFAULT_BAN_MINUTES
    try:
        return max(1, int(setting.value))
    except (TypeError, ValueError):
        return DEFAULT_BAN_MINUTES

def set_ban_minutes(db: Session, minutes: int) -> int:
    if minutes < 1:
        raise ValueError("Thời gian cấm phải >= 1 phút")
    setting = db.query(Setting).filter(Setting.key == "ban_minutes").first()
    if not setting:
        setting = Setting(key="ban_minutes", value=str(minutes))
        db.add(setting)
    else:
        setting.value = str(minutes)
    db.commit()
    return minutes

def save_setting(db: Session, key: str, value: str) -> None:
    """Upsert a setting."""
    setting = db.query(Setting).filter(Setting.key == key).first()
    if not setting:
        setting = Setting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()

def get_setting(db: Session, key: str, default: str = None) -> str:
    """Read a setting value by key."""
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting else default

def parse_time_setting(value: str) -> time:
    """Parse 'HH:MM' string to time object."""
    if not value:
        return None
    h, m = map(int, value.split(":"))
    return time(h, m)

def get_am_deadline(db: Session, target_date: date = None) -> time:
    """Return AM checkin deadline: setting if target_date >= effective date, else default 8:30."""
    setting = get_setting(db, "am_checkin_deadline")
    effective_str = get_setting(db, "am_checkin_deadline_date")
    if setting and effective_str:
        try:
            effective = date.fromisoformat(effective_str)
            if target_date and target_date >= effective:
                parsed = parse_time_setting(setting)
                if parsed:
                    return parsed
        except ValueError:
            pass
    return time(8, 30)

def get_pm_deadline(db: Session, target_date: date = None) -> time:
    """Return PM checkout deadline: setting if target_date >= effective date, else default 18:00."""
    setting = get_setting(db, "pm_checkout_deadline")
    effective_str = get_setting(db, "pm_checkout_deadline_date")
    if setting and effective_str:
        try:
            effective = date.fromisoformat(effective_str)
            if target_date and target_date >= effective:
                parsed = parse_time_setting(setting)
                if parsed:
                    return parsed
        except ValueError:
            pass
    return time(18, 0)

# ---------------- Admin ----------------
def get_admin(db: Session) -> Optional[Admin]:
    return db.query(Admin).first()

def verify_admin_password(db: Session, password: str) -> bool:
    admin = get_admin(db)
    if not admin:
        return False
    return bcrypt.checkpw(password.encode(), admin.password_hash.encode())

def change_admin_password(db: Session, old_password: str, new_password: str) -> bool:
    admin = get_admin(db)
    if not admin or not bcrypt.checkpw(old_password.encode(), admin.password_hash.encode()):
        return False
    admin.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    db.commit()
    return True

# ---------------- User (multi-role) ----------------
def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()

def verify_user_password(db: Session, username: str, password: str) -> Optional[User]:
    """Verify username+password against the new `users` table. Returns the User
    if credentials are valid, None otherwise."""
    user = get_user_by_username(db, username)
    if not user:
        return None
    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return None
    return user

# ---------------- Department ----------------
def get_departments(db: Session) -> List[Department]:
    return db.query(Department).order_by(Department.sort_order, Department.id).all()

def get_department(db: Session, department_id: int) -> Optional[Department]:
    return db.query(Department).filter(Department.id == department_id).first()

def create_department(db: Session, name: str, sort_order: int = 0) -> Department:
    dept = Department(name=name, sort_order=sort_order)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept

def delete_department(db: Session, department_id: int) -> bool:
    dept = get_department(db, department_id)
    if not dept:
        return False
    # Detach all employees that belong to this department by setting their
    # department_id to NULL. They will appear under "Chưa phân phòng" in
    # the sheet. We do this BEFORE the dept delete to avoid FK conflicts
    # when the dept row is removed.
    db.query(Employee).filter(Employee.department_id == department_id).update(
        {Employee.department_id: None}, synchronize_session=False
    )
    db.delete(dept)
    db.commit()
    return True

# ---------------- Employee ----------------
def get_employees(db: Session) -> List[Employee]:
    return db.query(Employee).order_by(Employee.code).all()

def get_employee(db: Session, employee_id: int) -> Optional[Employee]:
    return db.query(Employee).filter(Employee.id == employee_id).first()

def create_employee(
    db: Session,
    code: str,
    name: str,
    department_id: Optional[int] = None,
    start_date: Optional[date] = None,
    status_label: Optional[str] = None,
) -> Employee:
    employee = Employee(
        code=code,
        name=name,
        department_id=department_id,
        start_date=start_date,
        status_label=status_label,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee

def update_employee(
    db: Session,
    employee_id: int,
    code: Optional[str] = None,
    name: Optional[str] = None,
    department_id: Optional[int] = None,
    start_date: Optional[date] = None,
    status_label: Optional[str] = None,
    clear_department: bool = False,
    clear_start_date: bool = False,
) -> Optional[Employee]:
    employee = get_employee(db, employee_id)
    if not employee:
        return None
    if code is not None:
        employee.code = code
    if name is not None:
        employee.name = name
    if clear_department:
        employee.department_id = None
    elif department_id is not None:
        employee.department_id = department_id
    if clear_start_date:
        employee.start_date = None
    elif start_date is not None:
        employee.start_date = start_date
    if status_label is not None:
        employee.status_label = status_label
    db.commit()
    db.refresh(employee)
    return employee

def delete_employee(db: Session, employee_id: int) -> bool:
    employee = get_employee(db, employee_id)
    if not employee:
        return False
    # Cascade: remove all attendance records and device bans linked to this
    # employee before deleting the employee row. FK constraints don't have
    # ON DELETE CASCADE, so we do it manually to avoid IntegrityError.
    db.query(Attendance).filter(Attendance.employee_id == employee_id).delete(
        synchronize_session=False
    )
    db.query(DeviceBan).filter(DeviceBan.employee_id == employee_id).delete(
        synchronize_session=False
    )
    db.delete(employee)
    db.commit()
    return True

# ---------------- Device Ban ----------------
def get_active_ban(db: Session, device_id: str, action_type: str) -> Optional[DeviceBan]:
    """Return the active ban for a device+action if any."""
    return db.query(DeviceBan).filter(
        DeviceBan.device_id == device_id,
        DeviceBan.action_type == action_type,
        DeviceBan.expires_at > datetime.now()
    ).first()

def get_ban_status(db: Session, device_id: str, action_type: str) -> dict:
    ban = get_active_ban(db, device_id, action_type)
    if not ban:
        return {
            "is_banned": False,
            "remaining_minutes": 0,
            "action_type": None
        }
    remaining = int((ban.expires_at - datetime.now()).total_seconds() // 60)
    return {
        "is_banned": True,
        "remaining_minutes": max(1, remaining),
        "action_type": action_type
    }

def create_ban(db: Session, device_id: str, action_type: str, employee_id: int, minutes: int) -> DeviceBan:
    now = datetime.now()
    ban = DeviceBan(
        device_id=device_id,
        action_type=action_type,
        employee_id=employee_id,
        banned_at=now,
        expires_at=now + timedelta(minutes=minutes)
    )
    db.add(ban)
    return ban

# ---------------- Attendance ----------------
def get_attendance_by_employee_today(db: Session, employee_id: int, today: date) -> Optional[Attendance]:
    return db.query(Attendance).filter(
        Attendance.employee_id == employee_id,
        Attendance.date == today
    ).first()

def checkin(db: Session, employee_id: int, device_id: str, client_ip: Optional[str] = None) -> Attendance:
    """Checkin using employee_id. Banned device blocks this action only.

    Writes the action to `time_log` (source of truth) and keeps `attendance`
    summary in sync via `log_timelog`.
    """
    today = date.today()
    now = datetime.now()

    # 0. Validate employee exists.
    employee = get_employee(db, employee_id)
    if not employee:
        raise ValueError("Nhân viên không tồn tại")

    # 1. If this device is currently banned for checkin, refuse.
    ban = get_active_ban(db, device_id, "checkin")
    if ban:
        remaining = int((ban.expires_at - now).total_seconds() // 60) + 1
        raise ValueError(
            f"Thiết bị này đã được dùng để checkin cho nhân viên khác, "
            f"vui lòng đợi khoảng {remaining} phút"
        )

    # 2. Employee must not already have a checkin today.
    existing_tl = db.query(TimeLog).filter(
        TimeLog.employee_id == employee_id,
        TimeLog.date == today,
        TimeLog.action == "checkin",
    ).first()
    if existing_tl:
        raise ValueError("Bạn đã checkin hôm nay rồi")

    # 3. Record into time_log (this also upserts the attendance summary row).
    _record_checkin(
        db,
        employee_id=employee_id,
        target_date=today,
        time_value=now,
        device_id=device_id,
        client_ip=client_ip,
        actor="kiosk",
    )

    # 4. Ban this device for future checkin actions.
    create_ban(db, device_id, "checkin", employee_id, get_ban_minutes(db))

    db.commit()
    # Return the refreshed attendance summary.
    att = get_attendance_by_employee_today(db, employee_id, today)
    if att is None:
        # Should not happen but guard against race.
        raise ValueError("Không thể tạo bản ghi chấm công")
    db.refresh(att)
    return att


def checkout(db: Session, employee_id: int, device_id: str, client_ip: Optional[str] = None) -> Attendance:
    """Checkout by employee_id. Device does not have to match checkin device.

    Writes the action to `time_log` and keeps `attendance` summary in sync.
    """
    today = date.today()
    now = datetime.now()

    # 0. Validate employee exists.
    employee = get_employee(db, employee_id)
    if not employee:
        raise ValueError("Nhân viên không tồn tại")

    # 1. Device ban check.
    ban = get_active_ban(db, device_id, "checkout")
    if ban:
        remaining = int((ban.expires_at - now).total_seconds() // 60) + 1
        raise ValueError(
            f"Thiết bị này đã được dùng để checkout cho nhân viên khác, "
            f"vui lòng đợi khoảng {remaining} phút"
        )

    # 2. Employee must have a checkin today (otherwise can't checkout).
    existing_ci = db.query(TimeLog).filter(
        TimeLog.employee_id == employee_id,
        TimeLog.date == today,
        TimeLog.action == "checkin",
    ).first()
    if not existing_ci:
        raise ValueError("Bạn chưa checkin hôm nay")

    # 3. Refuse if already checked out.
    existing_co = db.query(TimeLog).filter(
        TimeLog.employee_id == employee_id,
        TimeLog.date == today,
        TimeLog.action == "checkout",
    ).first()
    if existing_co:
        raise ValueError("Bạn đã checkout rồi")

    # 4. Record into time_log.
    _record_checkout(
        db,
        employee_id=employee_id,
        target_date=today,
        time_value=now,
        device_id=device_id,
        client_ip=client_ip,
        actor="kiosk",
    )

    # 5. Ban this device for future checkout actions.
    create_ban(db, device_id, "checkout", employee_id, get_ban_minutes(db))

    db.commit()
    att = get_attendance_by_employee_today(db, employee_id, today)
    if att is None:
        raise ValueError("Không thể cập nhật bản ghi chấm công")
    db.refresh(att)
    return att

def get_attendance_status_for_device(db: Session, device_id: str) -> dict:
    """Today's status keyed by the device used to checkin.

    Since each device is banned for 30 minutes after checkin, the device
    that just performed a checkin is still safe to query here.
    """
    today = date.today()
    attendance = db.query(Attendance).filter(
        Attendance.checkin_device_id == device_id,
        Attendance.date == today
    ).first()

    if not attendance:
        return {
            "has_checked_in": False,
            "has_checked_out": False,
            "checkin_time": None,
            "checkout_time": None,
            "employee_id": None,
            "employee_name": None,
            "late_minutes": 0,
            "shift": None,
        }

    employee = get_employee(db, attendance.employee_id)
    shift = attendance.shift or ("AM" if attendance.checkin_time.time() <= AM_CHECKIN_LATEST else "PM")
    return {
        "has_checked_in": True,
        "has_checked_out": attendance.checkout_time is not None,
        "checkin_time": attendance.checkin_time,
        "checkout_time": attendance.checkout_time,
        "employee_id": attendance.employee_id,
        "employee_name": employee.name if employee else None,
        "late_minutes": compute_late_minutes(attendance.checkin_time, shift, db, today),
        "shift": attendance.shift,
    }

# ---------------- Stats ----------------
# Threshold times used by compute_work_day.
NOON = time(12, 0)
HALF_DAY_PM_BORDER = time(13, 30)   # Checkout < 13:30 AND checkin > 12:00 = 0 cong (PM-loi)
AM_CHECKIN_NORMAL_END = time(8, 30)        # Checkin đúng giờ nếu timein < 8:30
PM_CHECKIN_OK_BORDER = time(13, 30)        # Checkin được tính đúng giờ nếu 12:00 < timein < 13:30
PM_CHECKOUT_OK_END = time(18, 0)           # Checkout đúng giờ nếu timeout > 18:00 (full day)
NOON_END = time(12, 0)                     # Checkout đúng giờ nếu 12:00 < timeout < 13:30 (PM)
CHECKIN_FAULTY = time(18, 0)               # Checkin lỗi: timein > 18:00 → 0 công
CHECKOUT_FAULTY = time(8, 30)              # Checkout lỗi: timeout < 8:30 → 0 công


def classify_checkin(ci_t: time, dow: int) -> str:
    """Trả về 1 trong: 'normal' | 'late' | 'pm_late' | 'faulty'.

    Quy tắc highlight (áp dụng T2-CN, kể cả T7):
      - normal: timein < 8:30  HOẶC  12:00 < timein < 13:30
      - late:   8:30 ≤ timein ≤ 12:00  (muộn AM)  HOẶC  13:30 ≤ timein ≤ 18:00 (muộn PM)
      - faulty: timein > 18:00 (lỗi — NV checkin ngày hôm sau hoặc ghi nhầm)
    """
    if ci_t < AM_CHECKIN_NORMAL_END:
        return "normal"
    if ci_t <= NOON:
        return "late"        # muộn AM (8:30–12:00)
    if ci_t < PM_CHECKIN_OK_BORDER:
        return "normal"      # 12:00–13:30 = nghỉ trưa, tính như đúng giờ PM
    if ci_t <= CHECKIN_FAULTY:
        return "late"        # 13:30–18:00 = muộn PM
    return "faulty"          # > 18:00 = lỗi


def classify_checkout(co_t: time, dow: int) -> str:
    """Trả về 1 trong: 'normal' | 'early' | 'faulty' | 'no_checkout'.

    Quy tắc highlight:
      - normal:  12:00 < timeout < 13:30  (checkout đầu giờ chiều — tính đúng giờ PM)
                 HOẶC timeout ≥ 18:00 (full day)
      - early:   các trường hợp còn lại (checkout sớm)
      - faulty:  timeout < 8:30 (lỗi — checkout quá sớm / sai ngày)
    """
    if co_t < CHECKOUT_FAULTY:
        return "faulty"
    if NOON_END < co_t < PM_CHECKIN_OK_BORDER:
        return "normal"
    if co_t >= PM_CHECKOUT_OK_END:
        return "normal"
    return "early"


def compute_work_day(
    checkin_dt: Optional[datetime],
    checkout_dt: Optional[datetime],
    target_date: date,
) -> dict:
    """Return the work-value for one (employee, date) cell.

    Returns a dict:
      - work_value: 0 | 0.5 | 1 | None (None = Chủ nhật, không tính)
      - status:     one of "weekend", "absent", "no_checkout",
                    "on_time", "late", "early_leave", "half_day",
                    "checkin_faulty", "checkout_faulty"
      - symbol:     "1", "0.5", "0", or ""  (display-friendly)
      - is_holiday: bool  (called externally; default False here)
      - holiday_kind: "L" | "P" | None
      - checkin_status:   "normal" | "late" | "faulty" | None
      - checkout_status:  "normal" | "early" | "faulty" | None

    Quy tắc (on-the-fly, không cache):
      - CN (dow=6): work_value=None
      - Không checkin: work_value=0, status=absent
      - Có checkin nhưng KHÔNG có checkout: work_value=0, status=no_checkout
        (quy tắc "phải đủ checkin VÀ checkout" — áp dụng cho T2-T7)
      - T2-T6 (đã có đủ 2 giờ):
          * checkin > 18:00 (lỗi)                              → 0
          * checkout < 8:30 (lỗi)                              → 0
          * checkin <= 12:00 AND checkout >= 13:30             → 1
          * checkin <= 12:00 AND checkout <  13:30             → 0.5 (buổi sáng)
          * checkin >  12:00 AND checkout <  13:30             → 0   (PM lỗi)
          * checkin >  12:00 AND checkout >= 13:30             → 0.5 (buổi chiều)
      - T7 (dow=5, đã có đủ 2 giờ): → 0.5
    """
    dow = target_date.weekday()

    if dow == 6:  # Sunday
        return {
            "work_value": None,
            "status": "weekend",
            "symbol": "",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": None,
            "checkout_status": None,
        }

    if checkin_dt is None:
        return {
            "work_value": 0,
            "status": "absent",
            "symbol": "0",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": None,
            "checkout_status": None,
        }

    # ── Quy tắc "đủ 2 giờ" ─────────────────────────────────────────────────
    # Có checkin nhưng thiếu checkout → ngày đó KHÔNG tính công (0 công).
    # Đặt gate ở đây (trước mọi nhánh T7 / T2-T6) để quy tắc áp dụng
    # đồng nhất cho cả tuần: thiếu 1 trong 2 giờ = không công.
    if checkout_dt is None:
        return {
            "work_value": 0,
            "status": "no_checkout",
            "symbol": "0",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": classify_checkin(checkin_dt.time(), dow),
            "checkout_status": "no_checkout",
        }

    ci_t = checkin_dt.time()
    co_t = checkout_dt.time()
    ci_status = classify_checkin(ci_t, dow)
    co_status = classify_checkout(co_t, dow)

    # ── Quy tắc "lỗi" ghi đè ────────────────────────────────────────────────
    # Checkin lỗi: timein > 18:00 → ngày đó = 0 công (dù cho có checkout)
    if ci_status == "faulty":
        return {
            "work_value": 0,
            "status": "checkin_faulty",
            "symbol": "0",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": ci_status,
            "checkout_status": co_status,
        }

    # Checkout lỗi: timeout < 8:30 → ngày đó = 0 công
    if co_status == "faulty":
        return {
            "work_value": 0,
            "status": "checkout_faulty",
            "symbol": "0",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": ci_status,
            "checkout_status": co_status,
        }

    # Saturday — always 0.5 if there is any checkin.
    if dow == 5:
        return {
            "work_value": 0.5,
            "status": "half_day",
            "symbol": "0.5",
            "is_holiday": False,
            "holiday_kind": None,
            "checkin_status": ci_status,
            "checkout_status": co_status,
        }

    # Weekday rules (Mon-Fri). Lưu ý: tới đây chắc chắn đã có CẢ checkin
    # VÀ checkout (gate "đủ 2 giờ" ở trên đã loại trường hợp thiếu checkout),
    # nên co_t luôn khác None.
    if ci_t <= NOON:
        # AM shift
        if co_t < HALF_DAY_PM_BORDER:
            work = 0.5   # morning only
            status = "half_day"
        else:
            work = 1
            status = "on_time"
    else:
        # PM shift (checkin > 12:00)
        if co_t < HALF_DAY_PM_BORDER:
            work = 0     # PM-loi
            status = "absent"
        else:
            work = 0.5   # afternoon only
            status = "half_day"

    symbol = "" if work == 0 else ("1" if work == 1 else "0.5")
    return {
        "work_value": work,
        "status": status,
        "symbol": symbol,
        "is_holiday": False,
        "holiday_kind": None,
        "checkin_status": ci_status,
        "checkout_status": co_status,
    }


def get_holiday_for(db: Session, employee_id: int, target_date: date) -> Optional[Holiday]:
    """Return the most specific Holiday for (employee, date).

    Priority: per-employee (scope='employee') first, then 'all'.
    """
    emp_row = db.query(Holiday).filter(
        Holiday.date == target_date,
        Holiday.scope == "employee",
        Holiday.employee_id == employee_id,
    ).first()
    if emp_row:
        return emp_row
    return db.query(Holiday).filter(
        Holiday.date == target_date,
        Holiday.scope == "all",
    ).first()


def get_sheet_data(db: Session, year: int, month: int) -> dict:
    """Get attendance data for the pay period (26 prev month -> 25 this month).

    Example: month=8, year=2026 -> 2026-07-26 through 2026-08-25.

    Logic: tính công on-the-fly từ time_log + holidays. Bảng `attendance`
    chỉ dùng để fallback hiển thị giờ checkin/checkout (sẽ đồng bộ với time_log).
    """
    if month == 1:
        period_start = date(year - 1, 12, 26)
        period_end = date(year, 1, 25)
    else:
        period_start = date(year, month - 1, 26)
        period_end = date(year, month, 25)

    # Pre-load departments (ordered) with their employees.
    departments = get_departments(db)
    all_employees = get_employees(db)
    by_dept: dict[Optional[int], list[Employee]] = {}
    for emp in all_employees:
        by_dept.setdefault(emp.department_id, []).append(emp)

    # Generate all dates in period.
    dates: list[date] = []
    cur = period_start
    while cur <= period_end:
        dates.append(cur)
        cur += timedelta(days=1)

    # Build department buckets.
    dept_buckets = []
    for dept in departments:
        emps = by_dept.get(dept.id, [])
        if not emps:
            continue
        dept_buckets.append((dept.id, dept.name, emps))
        by_dept.pop(dept.id, None)
    for dept_id, emps in by_dept.items():
        if emps:
            dept_buckets.append((None, "Chưa phân phòng", emps))

    # Pre-load all time_log rows for the period (source of truth).
    tl_rows = db.query(TimeLog).filter(
        TimeLog.date >= period_start,
        TimeLog.date <= period_end,
    ).all()
    # Build (employee_id, date) -> {"checkin": dt, "checkout": dt}
    tl_by_emp_date: dict[tuple[int, date], dict] = {}
    for r in tl_rows:
        slot = tl_by_emp_date.setdefault((r.employee_id, r.date), {})
        if r.action == "checkin":
            if slot.get("checkin") is None or r.time_value < slot["checkin"]:
                slot["checkin"] = r.time_value
        elif r.action == "checkout":
            if slot.get("checkout") is None or r.time_value > slot["checkout"]:
                slot["checkout"] = r.time_value

    # Pre-load holidays in the period (both scopes).
    holiday_rows = db.query(Holiday).filter(
        Holiday.date >= period_start,
        Holiday.date <= period_end,
    ).all()
    # Build (date, employee_id_or_None) -> Holiday. For 'all', employee_id is None.
    holiday_by_date: dict[date, list[Holiday]] = {}
    for h in holiday_rows:
        holiday_by_date.setdefault(h.date, []).append(h)

    today = date.today()

    result = {
        "period_start": period_start,
        "period_end": period_end,
        "dates": dates,
        "weekdays_per_week": 6,
        "departments": [],
    }

    for dept_id, dept_name, emps in dept_buckets:
        emp_stats = []
        for emp in emps:
            days_on_time = 0
            days_late = 0
            days_absent = 0
            half_days = 0
            holiday_full = 0   # L/P ghi đè, full công (T2-T6)
            holiday_half = 0   # L/P ghi đè, nửa công (T7)
            total_late = 0
            total_early = 0
            details = []

            for d in dates:
                dow = d.weekday()
                slot = tl_by_emp_date.get((emp.id, d))
                # Dùng .get() thay vì [] vì slot chỉ có key cho loại giờ thực
                # sự có: ngày chỉ có checkin thì slot không có key "checkout"
                # (thiếu 1 trong 2 giờ là tình huống bình thường).
                checkin_dt = slot.get("checkin") if slot else None
                checkout_dt = slot.get("checkout") if slot else None

                ci_status = None
                co_status = None

                # Holiday ghi đè.
                holiday = None
                for h in holiday_by_date.get(d, []):
                    if h.scope == "all" or h.employee_id == emp.id:
                        holiday = h
                        break

                # Ngày tương lai + hôm nay: để trống (chưa tính).
                if d > today:
                    work_value = None
                    status = "future"
                    symbol = ""
                    late_min = 0
                    early_min = 0
                    checkin_display = ""
                    checkout_display = ""
                elif holiday is not None:
                    # Áp dụng ngày lễ/phép: T2-T6 = 1 công, T7 = 0.5
                    if dow == 6:  # CN không tính dù là lễ
                        work_value = None
                        status = "weekend"
                        symbol = ""
                    elif dow == 5:
                        work_value = 0.5
                        status = "holiday"
                        symbol = "0.5"
                        holiday_half += 1
                    else:
                        work_value = 1
                        status = "holiday"
                        symbol = "1"
                        holiday_full += 1
                    late_min = 0
                    early_min = 0
                    checkin_display = ""
                    checkout_display = ""
                else:
                    res = compute_work_day(checkin_dt, checkout_dt, d)
                    work_value = res["work_value"]
                    status = res["status"]
                    symbol = res["symbol"]
                    ci_status = res.get("checkin_status")
                    co_status = res.get("checkout_status")
                    late_min = 0
                    early_min = 0
                    if checkin_dt is not None:
                        am_dl = get_am_deadline(db, d)
                        if checkin_dt.time() > am_dl:
                            late_min = (checkin_dt.hour * 60 + checkin_dt.minute) - (
                                am_dl.hour * 60 + am_dl.minute
                            )
                        total_late += late_min
                    if checkout_dt is not None and dow != 6 and dow != 5:
                        pm_dl = get_pm_deadline(db, d)
                        if checkout_dt.time() < pm_dl:
                            early_min = (pm_dl.hour * 60 + pm_dl.minute) - (
                                checkout_dt.hour * 60 + checkout_dt.minute
                            )
                            total_early += early_min

                    checkin_display = (
                        checkin_dt.strftime("%H:%M") if checkin_dt else ""
                    )
                    if checkout_dt is not None:
                        checkout_display = checkout_dt.strftime("%H:%M")
                    elif checkin_dt is not None:
                        # Ngày có checkin nhưng thiếu checkout → 0 công theo
                        # quy tắc "đủ 2 giờ". Vẫn hiển thị "No" để admin
                        # thấy rõ nguyên nhân ngày đó không được tính công.
                        checkout_display = "No"
                    else:
                        checkout_display = ""

                    if work_value == 1 and late_min == 0:
                        days_on_time += 1
                    elif work_value == 1 and late_min > 0:
                        days_late += 1
                    elif work_value == 0.5:
                        half_days += 1
                    elif work_value == 0:
                        days_absent += 1

                details.append({
                    "date": d,
                    "day_of_week": dow,
                    "checkin_time": checkin_dt if d <= today else None,
                    "checkout_time": checkout_dt if d <= today else None,
                    "checkin_display": checkin_display,
                    "checkout_display": checkout_display,
                    "status": status,
                    "late_minutes": late_min,
                    "early_minutes": early_min,
                    "symbol": symbol,
                    "work_value": work_value,
                    "checkin_status": ci_status if d <= today else None,
                    "checkout_status": co_status if d <= today else None,
                })

            standard_workdays = sum(1 for d in dates if d.weekday() < 5)
            actual_days = days_on_time + days_late + half_days * 0.5 + holiday_full + holiday_half * 0.5

            emp_stats.append({
                "employee_id": emp.id,
                "employee_code": emp.code,
                "employee_name": emp.name,
                "department_id": dept_id,
                "department_name": dept_name,
                "start_date": emp.start_date,
                "status_label": emp.status_label,
                "days_on_time": days_on_time,
                "days_late": days_late,
                "days_absent": days_absent,
                "half_days": half_days,
                "holiday_full_days": holiday_full,
                "holiday_half_days": holiday_half,
                "total_late_minutes": total_late,
                "total_early_minutes": total_early,
                "standard_workdays": standard_workdays,
                "official_days": actual_days,
                "business_trip_days": 0,
                "holiday_days": holiday_full + holiday_half,
                "holiday_work_days": 0,
                "paid_leave_days": 0,
                "maternity_leave_days": 0,
                "compensatory_days": 0,
                "office_work_days": 0,
                "unpaid_leave_days": days_absent,
                "paid_work_days": actual_days,
                "trial_work_days": actual_days if emp.status_label == "Thử việc" else 0,
                "official_work_days": actual_days if emp.status_label == "Chính thức" else 0,
                "tts_days": 0,
                "intern_days": actual_days if emp.status_label == "Học việc" else 0,
                "details": details,
            })

        result["departments"].append({
            "department_id": dept_id,
            "department_name": dept_name,
            "employees": emp_stats,
        })

    return result