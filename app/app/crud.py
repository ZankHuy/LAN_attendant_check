from sqlalchemy.orm import Session
from app.models import Admin, User, Employee, Attendance, Device, DeviceBan, Setting, Department
from app.database import SessionLocal
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

def checkin(db: Session, employee_id: int, device_id: str) -> Attendance:
    """Checkin using employee_id. Banned device blocks this action only."""
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
    existing = get_attendance_by_employee_today(db, employee_id, today)
    if existing and existing.checkin_time:
        raise ValueError("Bạn đã checkin hôm nay rồi")

    # 3. Determine shift (AM if checkin <= 12:00, PM otherwise).
    shift = "AM" if now.time() <= AM_CHECKIN_LATEST else "PM"

    # 4. Compute deadline and is_on_time for the shift.
    if shift == "AM":
        deadline = get_am_deadline(db, today)
        is_on_time = now.time() <= deadline
    else:
        deadline = PM_SHIFT_DEADLINE
        is_on_time = now.time() <= deadline

    # 5. Create the attendance record.
    attendance = Attendance(
        employee_id=employee_id,
        date=today,
        checkin_time=now,
        checkin_device_id=device_id,
        is_on_time=is_on_time,
        shift=shift,
    )
    db.add(attendance)

    # 6. Ban this device for future checkin actions.
    create_ban(db, device_id, "checkin", employee_id, get_ban_minutes(db))

    db.commit()
    db.refresh(attendance)
    return attendance
def checkout(db: Session, employee_id: int, device_id: str) -> Attendance:
    """Checkout by employee_id. Device does not have to match checkin device.
    Banned device blocks this action only."""
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

    # 2. Find today's checkin record by employee_id (device is irrelevant).
    attendance = get_attendance_by_employee_today(db, employee_id, today)
    if not attendance or not attendance.checkin_time:
        raise ValueError("Bạn chưa checkin hôm nay")
    if attendance.checkout_time:
        raise ValueError("Bạn đã checkout rồi")

    # 3. Determine shift (use stored shift or infer from checkin_time).
    shift = attendance.shift or ("AM" if attendance.checkin_time.time() <= AM_CHECKIN_LATEST else "PM")
    weekday = today.weekday()
    co_time = now.time()

    # 4. Compute is_early_leave and early_leave_minutes.
    if weekday == 5:  # Saturday
        is_early_leave = co_time < SATURDAY_SHIFT_END
        early_leave_minutes = None
        if is_early_leave:
            early_leave_minutes = (
                SATURDAY_SHIFT_END.hour * 60 + SATURDAY_SHIFT_END.minute
            ) - (co_time.hour * 60 + co_time.minute)
    else:
        # Weekdays: early_leave if checkout before the configured PM deadline
        pm_deadline = get_pm_deadline(db, today)
        is_early_leave = co_time < pm_deadline
        early_leave_minutes = None
        if is_early_leave:
            early_leave_minutes = (
                pm_deadline.hour * 60 + pm_deadline.minute
            ) - (co_time.hour * 60 + co_time.minute)

    # 5. Compute is_full_day: checkin <= AM deadline AND checkout >= PM deadline.
    am_deadline = get_am_deadline(db, today)
    pm_deadline = get_pm_deadline(db, today)
    is_full_day = (
        attendance.checkin_time.time() <= am_deadline
        and co_time >= pm_deadline
    )

    # 6. Set checkout fields.
    attendance.checkout_time = now
    attendance.checkout_device_id = device_id
    attendance.is_early_leave = is_early_leave
    attendance.is_full_day = is_full_day
    attendance.early_leave_minutes = early_leave_minutes

    # 7. Ban this device for future checkout actions.
    create_ban(db, device_id, "checkout", employee_id, get_ban_minutes(db))

    db.commit()
    db.refresh(attendance)
    return attendance

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
def get_sheet_data(db: Session, year: int, month: int) -> dict:
    """Get attendance data for the pay period (26 prev month -> 25 this month).

    Example: month=8, year=2026 -> 2026-07-26 through 2026-08-25.
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
    # 1. Departments with explicit name (sorted by sort_order).
    for dept in departments:
        emps = by_dept.get(dept.id, [])
        if not emps:
            continue
        dept_buckets.append((dept.id, dept.name, emps))
        by_dept.pop(dept.id, None)
    # 2. Employees without department -> "Chưa phân phòng".
    for dept_id, emps in by_dept.items():
        if emps:
            dept_buckets.append((None, "Chưa phân phòng", emps))

    # Pre-load all attendances for the period.
    period_attendances = db.query(Attendance).filter(
        Attendance.date >= period_start,
        Attendance.date <= period_end
    ).all()
    att_by_emp_date: dict[tuple[int, date], Attendance] = {
        (a.employee_id, a.date): a for a in period_attendances
    }

    result = {
        "period_start": period_start,
        "period_end": period_end,
        "dates": dates,
        "weekdays_per_week": 6,  # T2-T6 + sáng T7 = 6 buổi / tuần
        "departments": [],
    }

    for dept_id, dept_name, emps in dept_buckets:
        emp_stats = []
        for emp in emps:
            days_on_time = 0
            days_late = 0
            days_absent = 0
            half_days = 0     # half-day count (Sat worked + weekday half-day rule)
            total_late = 0
            total_early = 0
            details = []

            for d in dates:
                dow = d.weekday()  # 0=Mon, 6=Sun
                att = att_by_emp_date.get((emp.id, d))

                late_min = 0
                early_min = 0
                status = "weekend"

                if dow == 6:  # Sunday
                    status = "weekend"
                    symbol = ""
                elif dow == 5:  # Saturday - always counted as half-day if worked
                    if att and att.checkin_time:
                        # Calculate raw late/early minutes for reporting
                        if att.checkin_time.time() > WEEKDAY_CHECKIN_DEADLINE:
                            late_min = (att.checkin_time.hour * 60 + att.checkin_time.minute) - (8 * 60 + 30)
                        else:
                            late_min = 0
                        if att.checkout_time and att.checkout_time.time() < SATURDAY_SHIFT_END:
                            early_min = (SATURDAY_SHIFT_END.hour * 60 + SATURDAY_SHIFT_END.minute) - (att.checkout_time.hour * 60 + att.checkout_time.minute)
                        else:
                            early_min = 0

                        # Symbol is always 0.5 for Saturday (policy).
                        half_days += 1
                        total_late += late_min
                        total_early += early_min
                        symbol = "0.5"

                        # Status: if is_on_time override is set, use it; else compute from time
                        if att.is_on_time is True:
                            status = "on_time"
                            late_min = 0
                            total_late -= late_min  # reset late minutes on override
                        elif att.is_on_time is False:
                            status = "late"
                        elif late_min > 0:
                            status = "late"
                        elif early_min > 0:
                            status = "early_leave"
                        else:
                            status = "on_time"
                    else:
                        # T7 không đi làm -> để trống (giống nghỉ)
                        status = "weekend"
                        symbol = ""
                else:  # Mon-Fri
                    if att and att.checkin_time:
                        # ── Determine shift and compute raw minutes ─────────────────────
                        # Use stored shift (from checkin) or infer from checkin_time
                        shift = att.shift or (
                            "AM" if att.checkin_time.time() <= AM_CHECKIN_LATEST else "PM"
                        )
                        am_deadline = get_am_deadline(db, d)
                        pm_deadline = get_pm_deadline(db, d)
                        ci_t = att.checkin_time.time()
                        co_t = att.checkout_time.time() if att.checkout_time else None

                        # Raw late minutes: compare against shift-specific deadline
                        if shift == "AM":
                            if ci_t > am_deadline:
                                raw_late_min = (ci_t.hour * 60 + ci_t.minute) - (am_deadline.hour * 60 + am_deadline.minute)
                            else:
                                raw_late_min = 0
                        else:  # PM
                            if ci_t > PM_SHIFT_DEADLINE:
                                raw_late_min = (ci_t.hour * 60 + ci_t.minute) - (PM_SHIFT_DEADLINE.hour * 60 + PM_SHIFT_DEADLINE.minute)
                            else:
                                raw_late_min = 0

                        # Raw early minutes: minutes before PM deadline
                        raw_early_min = att.early_leave_minutes or 0

                        # ── Full-day rule ─────────────────────────────────────────────
                        if att.is_full_day or (ci_t <= am_deadline and co_t and co_t >= pm_deadline):
                            status = "on_time"
                            symbol = "1"
                            late_min = 0
                            early_min = 0
                            if att.is_on_time is not False:
                                days_on_time += 1
                            # override still possible
                        elif att.is_on_time is True:
                            # Manual: marked "đúng giờ"
                            status = "on_time"
                            symbol = "1"
                            late_min = 0
                            early_min = 0
                            days_on_time += 1
                        elif att.is_on_time is False:
                            # Manual: marked "đi muộn"
                            status = "late"
                            symbol = "1"
                            late_min = raw_late_min
                            early_min = 0
                            days_late += 1
                            total_late += raw_late_min
                        else:
                            # ── No override: apply shift-based rules ────────────────────
                            if shift == "PM":
                                # PM checkin (only) → 0.5 công
                                half_days += 1
                                late_min = raw_late_min
                                early_min = raw_early_min
                                total_late += raw_late_min
                                total_early += raw_early_min
                                if late_min > 0 and early_min > 0:
                                    status = "late_and_early"
                                elif late_min > 0:
                                    status = "late"
                                elif early_min > 0:
                                    status = "early_leave"
                                else:
                                    status = "on_time"
                                symbol = "0.5"
                            elif co_t and co_t < WEEKDAY_HALF_DAY_CHECKOUT_THRESHOLD:
                                # AM checkin + checkout before 13:30 → 0.5 buổi sáng
                                half_days += 1
                                late_min = raw_late_min
                                early_min = 0
                                total_late += raw_late_min
                                status = "early_leave" if raw_late_min == 0 else "late"
                                symbol = "0.5"
                            elif co_t and co_t < pm_deadline:
                                # AM checkin + checkout 13:30–pm_deadline → 0.5 công
                                # Record late AM + early PM minutes
                                half_days += 1
                                late_min = raw_late_min
                                early_min = raw_early_min
                                total_late += raw_late_min
                                total_early += raw_early_min
                                if raw_late_min > 0 and raw_early_min > 0:
                                    status = "late_and_early"
                                elif raw_late_min > 0:
                                    status = "late"
                                elif raw_early_min > 0:
                                    status = "early_leave"
                                else:
                                    status = "on_time"
                                symbol = "0.5"
                            elif raw_late_min > 0:
                                status = "late"
                                days_late += 1
                                late_min = raw_late_min
                                early_min = 0
                                total_late += raw_late_min
                                symbol = "1"
                            elif raw_early_min > 0:
                                # AM checkin + checkout after pm_deadline? Not possible unless
                                # early_leave_minutes is set manually — treat as on_time for 1.0
                                status = "on_time"
                                days_on_time += 1
                                late_min = 0
                                early_min = 0
                                symbol = "1"
                            else:
                                status = "on_time"
                                days_on_time += 1
                                late_min = 0
                                early_min = 0
                                symbol = "1"

                        # ── Apply is_early_leave override ─────────────────────────────
                        if att.is_early_leave is True:
                            if status == "on_time":
                                days_on_time -= 1
                                half_days += 1
                                status = "early_leave"
                            elif status == "late":
                                days_late -= 1
                                half_days += 1
                                status = "early_leave"
                            symbol = "0.5"
                            total_early += raw_early_min
                        elif att.is_early_leave is False:
                            if status == "early_leave":
                                half_days -= 1
                                days_on_time += 1
                                status = "on_time"
                            symbol = "1"

                    else:
                        status = "absent"
                        days_absent += 1
                        symbol = ""

                details.append({
                    "date": d,
                    "day_of_week": dow,
                    "checkin_time": att.checkin_time if att else None,
                    "checkout_time": att.checkout_time if att else None,
                    "status": status,
                    "late_minutes": late_min,
                    "early_minutes": early_min,
                    "symbol": symbol,
                })

            # Count standard workdays in the period (Mon-Fri only). Used for "Công chuẩn".
            standard_workdays = sum(1 for d in dates if d.weekday() < 5)

            # Tổng công thực tế (full + 0.5*half_days). half_days gồm:
            # - Tất cả T7 có đi làm (luôn = 0.5 công)
            # - T2-T6 thoả điều kiện nửa công (checkout < 13h30 hoặc checkin > 12h00)
            actual_days = days_on_time + days_late + half_days * 0.5

            # Sum danh sách các summary fields
            emp_stats.append({
                "employee_id": emp.id,
                "employee_code": emp.code,
                "employee_name": emp.name,
                "department_id": dept_id,
                "department_name": dept_name,
                "start_date": emp.start_date,
                "status_label": emp.status_label,
                # Basic counters
                "days_on_time": days_on_time,
                "days_late": days_late,
                "days_absent": days_absent,
                "half_days": half_days,                            # Số ngày tính nửa công
                "total_late_minutes": total_late,
                "total_early_minutes": total_early,
                # Extended summary fields mapped to template columns
                "standard_workdays": standard_workdays,             # Công chuẩn (T2-T6)
                "official_days": actual_days,                       # Công TT (tổng công thực tế)
                "business_trip_days": 0,                            # Công tác (no data)
                "holiday_days": 0,                                  # Nghỉ lễ
                "holiday_work_days": 0,                             # Ngày lễ đi làm
                "paid_leave_days": 0,                               # Nghỉ hưởng lương
                "maternity_leave_days": 0,                          # Nghỉ chế độ
                "compensatory_days": 0,                             # Nghỉ bù
                "office_work_days": 0,                              # Làm việc NVP
                "unpaid_leave_days": days_absent,                   # Nghỉ phép (best guess)
                "paid_work_days": actual_days,                      # Công hưởng lương
                "trial_work_days": (
                    actual_days
                    if emp.status_label == "Thử việc" else 0
                ),
                "official_work_days": (
                    actual_days
                    if emp.status_label == "Chính thức" else 0
                ),
                "tts_days": 0,                                      # Công TTS
                "intern_days": (
                    actual_days
                    if emp.status_label == "Học việc" else 0
                ),
                "details": details,
            })

        result["departments"].append({
            "department_id": dept_id,
            "department_name": dept_name,
            "employees": emp_stats,
        })

    return result