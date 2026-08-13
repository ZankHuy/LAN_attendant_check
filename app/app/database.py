from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./data/checknv.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _table_columns(conn, table_name: str) -> set:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def run_migrations() -> None:
    """Lightweight idempotent migrations for SQLite.

    SQLAlchemy's create_all only creates missing tables; it never alters
    existing ones. This step renames `device_id` -> `checkin_device_id`,
    adds `checkout_device_id`. The `device_bans` + `settings` tables are
    created by create_all if missing.
    """
    with engine.begin() as conn:
        cols = _table_columns(conn, "attendance")
        if cols:
            if "device_id" in cols and "checkin_device_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE attendance RENAME COLUMN device_id TO checkin_device_id"
                ))
            if "checkout_device_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE attendance ADD COLUMN checkout_device_id VARCHAR(100)"
                ))
            # ── New columns for shift / full-day / early-leave-minutes ──────────
            if "shift" not in cols:
                conn.execute(text(
                    "ALTER TABLE attendance ADD COLUMN shift VARCHAR(2)"
                ))
            if "is_full_day" not in cols:
                conn.execute(text(
                    "ALTER TABLE attendance ADD COLUMN is_full_day BOOLEAN DEFAULT 0"
                ))
            if "early_leave_minutes" not in cols:
                conn.execute(text(
                    "ALTER TABLE attendance ADD COLUMN early_leave_minutes INTEGER"
                ))

        # Employee table migrations: add department_id, start_date, status_label
        emp_cols = _table_columns(conn, "employees")
        if emp_cols:
            if "department_id" not in emp_cols:
                conn.execute(text(
                    "ALTER TABLE employees ADD COLUMN department_id INTEGER REFERENCES departments(id)"
                ))
            if "start_date" not in emp_cols:
                conn.execute(text(
                    "ALTER TABLE employees ADD COLUMN start_date DATE"
                ))
            if "status_label" not in emp_cols:
                conn.execute(text(
                    "ALTER TABLE employees ADD COLUMN status_label VARCHAR(50)"
                ))

        # Note: the `users` table is created by Base.metadata.create_all() from
        # the SQLAlchemy User model (see app/models.py). No raw SQL needed here.


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()