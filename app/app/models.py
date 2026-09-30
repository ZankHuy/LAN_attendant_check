from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Date, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class Admin(Base):
    __tablename__ = "admin"

    id = Column(Integer, primary_key=True, index=True)
    password_hash = Column(String(255), nullable=False)


class User(Base):
    """Multi-role users table. Coexists with the legacy `admin` table.

    `role` values used by this app:
      - 'admin'             : full privileges (same as legacy admin)
      - 'edit_attendance'   : can edit is_on_time / is_early_leave on attendance
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="edit_attendance")
    # server_default so raw inserts (e.g. via CLI) get a timestamp too.
    created_at = Column(
        DateTime, default=datetime, server_default=func.now(), nullable=False
    )


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    start_date = Column(Date, nullable=True)  # Ngày bắt đầu làm việc
    status_label = Column(String(50), nullable=True)  # Chính thức / Thử việc / Học việc / TTS

    attendances = relationship("Attendance", back_populates="employee")
    department = relationship("Department", back_populates="employees")


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    employees = relationship("Employee", back_populates="department", order_by="Employee.code")

class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False)
    checkin_time = Column(DateTime, nullable=True)
    checkout_time = Column(DateTime, nullable=True)
    checkin_device_id = Column(String(100), nullable=True)
    checkout_device_id = Column(String(100), nullable=True)
    is_on_time = Column(Boolean, default=False)
    is_early_leave = Column(Boolean, default=False)
    # ── New: shift & full-day tracking ──────────────────────────────────────
    shift = Column(String(2), nullable=True)            # 'AM' | 'PM' | None
    is_full_day = Column(Boolean, default=False)         # True: checkin<=8:30 & checkout>=18:00 → 1.0 công
    early_leave_minutes = Column(Integer, nullable=True) # Minutes early from 18:00 (or 12:00 Sat)

    employee = relationship("Employee", back_populates="attendances")

    __table_args__ = (
        UniqueConstraint('employee_id', 'date', name='uq_attendance_emp_date'),
    )

class DeviceBan(Base):
    """Temporary ban device after checkin/checkout action.

    `action_type` is either 'checkin' or 'checkout'. A device is banned
    only for that specific action for the configured duration.
    """
    __tablename__ = "device_bans"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), nullable=False, index=True)
    action_type = Column(String(20), nullable=False)  # 'checkin' or 'checkout'
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    banned_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)

class Setting(Base):
    """Key/value configuration stored in DB so admin can edit via UI."""
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(String(255), nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# Kept for backward compatibility with old schema; not used by new logic.
class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), unique=True, nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)

    employee = relationship("Employee")


class AuditLog(Base):
    """Append-only audit trail for admin/hidden actions.

    Written by `app.audit.log_action(...)`. Operators can query via the
    /api/audit endpoint (admin-only).
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    actor = Column(String(50), nullable=False, index=True)     # "admin" / "hidden" / username
    action = Column(String(50), nullable=False, index=True)    # create / patch_flags / delete / ...
    entity_type = Column(String(50), nullable=False, index=True)  # attendance / employee / settings
    entity_id = Column(Integer, nullable=True, index=True)
    detail = Column(Text, nullable=True)                        # JSON-encoded payload
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class RateLimitBucket(Base):
    """Per-(time bucket, employee_id) counter used by `app.ratelimit`.

    Composite primary key on (bucket_key, employee_id). Old rows are
    pruned periodically by `ratelimit.purge_old_buckets`.
    """
    __tablename__ = "rate_limit_buckets"

    bucket_key = Column(Integer, primary_key=True)
    employee_id = Column(Integer, primary_key=True)
    count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TimeLog(Base):
    """Append-only time log for every checkin/checkout action.

    Each row represents one physical checkin or checkout action.
    The `attendance` table is a denormalized summary (MIN(checkin), MAX(checkout))
    computed from this table.

    `is_manual=True` means the row was inserted by admin/hidden (for fixing
    forgotten checkouts, etc). Admin edits are tracked via `actor='hidden'`.
    """
    __tablename__ = "time_log"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    action = Column(String(20), nullable=False)        # 'checkin' | 'checkout'
    time_value = Column(DateTime, nullable=False)
    device_id = Column(String(100), nullable=True)
    client_ip = Column(String(50), nullable=True)
    is_manual = Column(Boolean, default=False, nullable=False)
    actor = Column(String(50), default="kiosk", nullable=False)  # 'kiosk' | 'hidden' | 'admin'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('employee_id', 'date', 'action', name='uq_timelog_emp_date_action'),
    )


class Holiday(Base):
    """Holiday / paid-leave day entries.

    `kind`:
      - 'L' = Nghi le (public holiday)
      - 'P' = Nghi phep (paid leave)
    `scope`:
      - 'all'      = ap dung cho tat ca nhan vien
      - 'employee' = ap dung cho 1 nhan vien cu the (employee_id NOT NULL)
    """
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    kind = Column(String(10), nullable=False)          # 'L' | 'P'
    label = Column(String(100), nullable=True)
    scope = Column(String(20), nullable=False)         # 'all' | 'employee'
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(50), default="admin", nullable=False)