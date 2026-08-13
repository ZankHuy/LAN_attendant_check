from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud, ratelimit, sessions
from app.database import get_db
from app.schemas import ChangePassword

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBasic()

_COOKIE_KWARGS = {
    "key": sessions.COOKIE_NAME,
    "httponly": True,
    "samesite": "strict",
    "path": "/",
    "secure": False,  # set True when serving over HTTPS in production
    "max_age": sessions.ABSOLUTE_TIMEOUT_SECONDS,
}


def _require_session(
    checknv_session: str | None = Cookie(default=None),
) -> str:
    sid = sessions.get_session_id(checknv_session)
    if not sid or not sessions.touch(sid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
        )
    return sid


@router.post("/login")
def login(
    request: Request,
    response: Response,
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Đăng nhập admin. Trả cookie HttpOnly thay vì để client lưu password."""
    client_ip = request.client.host if request.client else "unknown"
    # 5 attempts / minute per IP — generous enough for legitimate users
    # but stops brute-force.
    allowed, retry_after = ratelimit.check_ip(
        client_ip, max_requests=5, window_seconds=60
    )
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quá nhiều lần đăng nhập. Thử lại sau {retry_after}s.",
        )
    if not crud.verify_admin_password(db, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai mật khẩu",
            headers={"WWW-Authenticate": "Basic"},
        )
    sid = sessions.create_session()
    response.set_cookie(value=sid, **_COOKIE_KWARGS)
    return {"message": "Đăng nhập thành công"}


@router.post("/logout")
def logout(
    response: Response,
    sid: str = Depends(_require_session),
):
    """Đăng xuất — thu hồi session và xóa cookie phía client."""
    sessions.revoke(sid)
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    return {"message": "Đã đăng xuất"}


@router.post("/change-password")
def change_password(
    data: ChangePassword,
    db: Session = Depends(get_db),
    _: str = Depends(_require_session),
):
    """Đổi mật khẩu admin (yêu cầu session)."""
    if data.new_password != data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới không khớp",
        )
    if len(data.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải có ít nhất 6 ký tự",
        )
    if not crud.verify_admin_password(db, data.old_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mật khẩu cũ không đúng",
        )
    crud.change_admin_password(db, data.old_password, data.new_password)
    return {"message": "Đổi mật khẩu thành công"}


@router.get("/verify")
def verify_auth(
    sid: str = Depends(_require_session),
):
    """Phiên còn hiệu lực — dùng để FE check session khi tải lại trang."""
    return {"authenticated": True}


class LoginUserResponse(BaseModel):
    username: str
    role: str
    message: str


@router.post("/login-user", response_model=LoginUserResponse)
def login_user(
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Đăng nhập với username + password từ bảng `users`.

    Không tạo session cookie — multi-user chỉ dùng cho endpoint cần `role`
    riêng (hiện không có endpoint nào dùng). Admin cũ (chỉ password) vẫn
    dùng `/api/auth/login` ở trên.
    """
    user = crud.verify_user_password(db, credentials.username, credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai username hoặc password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return LoginUserResponse(
        username=user.username,
        role=user.role,
        message="Đăng nhập thành công",
    )