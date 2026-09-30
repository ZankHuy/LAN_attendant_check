import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import engine, Base, run_migrations
from app import models as _models  # noqa: F401  — register models with Base
from app.routers import attendance, employees, stats, auth, settings, export, hidden, holidays
from app import crud, ratelimit


# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR = Path(os.environ.get("LOG_DIR", "data"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("checknv")


# ── Hidden URL config (read from env) ───────────────────────────────────────
HIDDEN_URL_PATH = os.environ.get("HIDDEN_URL_PATH", "hidden")
# Ensure the path starts with '/' so it can be mounted as a route.
if not HIDDEN_URL_PATH.startswith("/"):
    HIDDEN_URL_PATH = "/" + HIDDEN_URL_PATH
HIDDEN_PASSWORD = os.environ.get("HIDDEN_PASSWORD", "")


# ── Background tasks ────────────────────────────────────────────────────────
import asyncio


async def _rate_limit_purger(interval_seconds: int = 3600) -> None:
    """Periodically drop rate_limit_buckets rows older than 1 hour."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            deleted = await asyncio.to_thread(ratelimit.purge_old_buckets, 3600)
            if deleted:
                logger.info("Purged %d stale rate_limit_buckets rows", deleted)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("rate_limit_purger loop error")


# ── FastAPI app (with lifespan for background tasks) ────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_rate_limit_purger())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="CheckNV - He thong cham cong", lifespan=lifespan)


# ── DB bootstrap ────────────────────────────────────────────────────────────
run_migrations()
Base.metadata.create_all(bind=engine)
crud.init_db()
logger.info("DB initialized (schema migrated, default admin ensured)")


# Static files (CSS/JS/img if any)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Public pages ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("static/admin.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    with open("static/login.html", "r", encoding="utf-8") as f:
        return f.read()


# Hidden page — URL is configurable via env. If HIDDEN_PASSWORD is unset we
# refuse to serve the page (operator must configure env first).
@app.get(HIDDEN_URL_PATH, response_class=HTMLResponse, include_in_schema=False)
async def hidden_page():
    if not HIDDEN_PASSWORD:
        # Don't leak existence — return generic 404.
        raise HTTPException(status_code=404, detail="Not Found")
    with open("static/hidden.html", "r", encoding="utf-8") as f:
        return f.read()


# ── Healthcheck ─────────────────────────────────────────────────────────────
@app.get("/health", include_in_schema=False)
def health():
    """Liveness + DB readiness probe for Docker/K8s."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("Healthcheck DB probe failed")
        raise HTTPException(status_code=503, detail=f"DB unreachable: {exc}")


# ── Routers ─────────────────────────────────────────────────────────────────
app.include_router(attendance.router)
app.include_router(employees.router)
app.include_router(stats.router)
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(export.router)
app.include_router(hidden.router)
app.include_router(holidays.router)


logger.info(
    "App started. Hidden page URL: %s  (auth: %s)",
    HIDDEN_URL_PATH,
    "password-only" if HIDDEN_PASSWORD else "DISABLED (set HIDDEN_PASSWORD env)",
)
