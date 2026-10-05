# CheckNV - Hệ Thống Chấm Công

**Version:** 2026.08 | **Stack:** FastAPI + SQLite + HTML/JS | **Deploy:** Docker

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Cài đặt nhanh](#2-cài-đặt-nhanh)
3. [Cấu trúc dự án](#3-cấu-trúc-dự án)
4. [Kiến trúc hệ thống](#4-kiến-trúc-hệ-thống)
5. [Database schema](#5-database-schema)
6. [API Endpoints](#6-api-endpoints)
7. [Tính năng chấm công](#7-tính-năng-chấm-công)
8. [Quy tắc tính công](#8-quy-tắc-tính-công)
9. [Cấu hình giờ làm việc](#9-cấu-hình-giờ-làm-việc)
10. [Docker deployment](#10-docker-deployment)
11. [Update hệ thống](#11-update-hệ-thống)
12. [Backup & Restore](#12-backup--restore)
13. [Bảo mật](#13-bảo-mật)
14. [Giải đáp lỗi thường gặp](#14-giải-đáp-lỗi-thường-gặp)

---

## 1. Tổng quan

CheckNV là hệ thống chấm công nhân viên, hoạt động trên mô hình **web-based checkin/checkout** qua trình duyệt. Mỗi thiết bị (trình duyệt) được gán một Device ID duy nhất, dùng để xác định nhân viên checkin và chống gian lận (1 thiết bị không thể checkin cho 2 người cùng lúc).

### Đặc điểm chính

| Đặc điểm | Mô tả |
|---|---|
| **Ngôn ngữ** | Python 3.11 + FastAPI |
| **Database** | SQLite (file-based, không cần server) |
| **Giao diện** | HTML + JavaScript thuần, không phụ thuộc framework |
| **Deploy** | Docker container, chạy trên bất kỳ máy nào có Docker |
| **Miền giờ** | Asia/Ho_Chi_Minh (UTC+7) |
| **Kỳ tính công** | 26 tháng trước → 25 tháng hiện tại |

---

## 2. Cài đặt nhanh

### Yêu cầu

- Docker & Docker Compose
- Python 3.11+ (nếu chạy không dùng Docker)

### 2.1. Chạy bằng Docker (Khuyến nghị)

```bash
# Clone hoặc copy thư mục CheckNV vào máy
cd /path/to/CheckNV

# Build và chạy (lần đầu)
docker compose up -d --build

# Các lần sau chỉ cần
docker compose up -d
```

### 2.2. Chạy không dùng Docker

```bash
cd /path/to/CheckNV

# Tạo môi trường ảo
python -m venv venv
source venv/bin/activate   # Linux/Mac
# hoặc: venv\Scripts\activate   # Windows

# Cài đặt thư viện
pip install -r requirements.txt

# Chạy server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2.3. Truy cập

| Trang | URL | Mục đích |
|---|---|---|
| Trang chấm công | `http://localhost:8000/` | Checkin / Checkout nhân viên |
| Trang quản lý | `http://localhost:8000/admin` | Quản lý nhân viên, xuất bảng công |
| Trang đăng nhập | `http://localhost:8000/login` | Đăng nhập user |
| Trang ẩn | `http://localhost:8000/hidden` | Lọc / sửa / tạo bản ghi chấm công (mật khẩu `HIDDEN_PASSWORD`) |

### 2.4. Tài khoản mặc định

| Tài khoản | Mật khẩu | Quyền |
|---|---|---|
| admin | `admin123` | Toàn quyền |

> **Quan trọng:** Đổi mật khẩu admin ngay sau khi cài đặt!

---

## 3. Cấu trúc dự án

```
CheckNV/
├── app/                          # Backend Python
│   ├── main.py                   # FastAPI app entry point
│   ├── models.py                 # SQLAlchemy models (database schema)
│   ├── schemas.py                # Pydantic schemas (API validation)
│   ├── crud.py                   # Business logic & database operations
│   ├── database.py               # Database connection & migrations
│   └── routers/                  # API route handlers
│       ├── attendance.py         # Checkin/Checkout/Settings endpoints
│       ├── employees.py          # Employee CRUD endpoints
│       ├── stats.py              # Sheet data & export endpoints
│       ├── auth.py               # Authentication endpoints
│       ├── settings.py           # Ban duration settings
│       ├── export.py             # Excel export
│       └── hidden.py             # Hidden attendance edit endpoints
├── static/                       # Frontend HTML/JS/CSS
│   ├── index.html                # Trang chấm công chính
│   ├── admin.html                # Trang quản lý admin
│   ├── login.html                # Trang đăng nhập
│   └── hidden.html               # Trang chỉnh sửa ẩn
├── resorce/                      # Resource files (Excel template)
├── data/                         # SQLite database (runtime)
│   └── checknv.db
├── Dockerfile                    # Docker image definition
├── docker-compose.yml            # Docker Compose configuration
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

---

## 4. Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────┐
│                    Client Browser                    │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐ │
│  │ index.html  │ │ admin.html  │ │  hidden.html │ │
│  │ (Chấm công) │ │ (Quản lý)  │ │ (Chỉnh sửa)  │ │
│  └─────────────┘ └─────────────┘ └──────────────┘ │
└──────────────────────┬────────────────────────────────┘
                      │ HTTP/REST API
┌──────────────────────▼────────────────────────────────┐
│              FastAPI Server (port 8000)                │
│  ┌─────────────────────────────────────────────────┐  │
│  │               API Routers                        │  │
│  │  attendance │ employees │ stats │ auth │ ...   │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │          Business Logic (crud.py)               │  │
│  │  - Checkin/Checkout logic                       │  │
│  │  - Late minutes calculation                     │  │
│  │  - Shift classification (AM/PM)                 │  │
│  │  - Full-day calculation                         │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │          Database Layer (SQLAlchemy)            │  │
│  │  SQLite: employees, attendance, settings, ...   │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## 5. Database Schema

Database: `data/checknv.db` (SQLite)

### 5.1. Bảng `employees`

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | ID nhân viên |
| `code` | VARCHAR(20) | Mã NV duy nhất (VD: "NV001") |
| `name` | VARCHAR(100) | Họ tên |
| `department_id` | INTEGER FK | Phòng ban |
| `start_date` | DATE | Ngày bắt đầu làm |
| `status_label` | VARCHAR(50) | Trạng thái: Chính thức / Thử việc / Học việc / TTS |

### 5.2. Bảng `attendance`

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | ID bản ghi |
| `employee_id` | INTEGER FK | Nhân viên |
| `date` | DATE | Ngày chấm công |
| `checkin_time` | DATETIME | Giờ checkin |
| `checkout_time` | DATETIME | Giờ checkout |
| `checkin_device_id` | VARCHAR(100) | Device ID dùng checkin |
| `checkout_device_id` | VARCHAR(100) | Device ID dùng checkout |
| `is_on_time` | BOOLEAN | Đánh dấu đúng giờ (override thủ công) |
| `is_early_leave` | BOOLEAN | Đánh dấu về sớm (override thủ công) |
| `shift` | VARCHAR(2) | Ca làm: 'AM' hoặc 'PM' (NULL nếu dữ liệu cũ) |
| `is_full_day` | BOOLEAN | True nếu đủ ngày (AM deadline → PM deadline) |
| `early_leave_minutes` | INTEGER | Số phút về sớm so với deadline PM |

### 5.3. Bảng `departments`

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | ID phòng ban |
| `name` | VARCHAR(100) | Tên phòng ban |
| `sort_order` | INTEGER | Thứ tự hiển thị |

### 5.4. Bảng `device_bans`

Chống gian lận — mỗi thiết bị bị cấm tạm thời sau khi checkin/checkout.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | |
| `device_id` | VARCHAR(100) | Device ID bị cấm |
| `action_type` | VARCHAR(20) | 'checkin' hoặc 'checkout' |
| `employee_id` | INTEGER FK | Nhân viên đã dùng thiết bị |
| `banned_at` | DATETIME | Thời điểm cấm |
| `expires_at` | DATETIME | Thời điểm hết cấm |

### 5.5. Bảng `settings`

Lưu cấu hình hệ thống dạng key-value.

| Key | Mô tả | Mặc định |
|---|---|---|
| `ban_minutes` | Thời gian cấm thiết bị (phút) | 30 |
| `am_checkin_deadline` | Giờ deadline checkin AM (HH:MM) | 08:30 |
| `am_checkin_deadline_date` | Ngày áp dụng deadline AM mới | "" |
| `pm_checkout_deadline` | Giờ deadline checkout PM (HH:MM) | 18:00 |
| `pm_checkout_deadline_date` | Ngày áp dụng deadline PM mới | "" |

### 5.6. Bảng `users`

Tài khoản multi-user với phân quyền.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | VARCHAR(50) | Tên đăng nhập |
| `password_hash` | VARCHAR(255) | Mật khẩu bcrypt |
| `role` | VARCHAR(50) | Vai trò (admin / edit_attendance) |
| `created_at` | DATETIME | Ngày tạo |

### 5.7. Bảng `time_log`

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | |
| `employee_id` | INTEGER FK | Nhân viên |
| `date` | DATE | Ngày |
| `action` | VARCHAR(20) | `checkin` / `checkout` |
| `time_value` | DATETIME | Giờ thực tế |
| `device_id` | VARCHAR(100) | Thiết bị kiosk |
| `client_ip` | VARCHAR(50) | IP máy kiosk |
| `is_manual` | BOOLEAN | `True` nếu admin sửa qua `/hidden` |
| `actor` | VARCHAR(50) | `kiosk` / `hidden` / `admin` |
| `created_at` | DATETIME | Thời điểm ghi log |

UNIQUE: `(employee_id, date, action)`.

### 5.8. Bảng `holidays`

| Cột | Kiểu | Mô tả |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | DATE | Ngày lễ / phép |
| `kind` | VARCHAR(10) | `L` (lễ) / `P` (phép) |
| `label` | VARCHAR(100) | Nhãn tuỳ chọn |
| `scope` | VARCHAR(20) | `all` / `employee` |
| `employee_id` | INTEGER FK | NV cụ thể (chỉ khi scope=`employee`) |
| `created_at` | DATETIME | |
| `created_by` | VARCHAR(50) | `admin` / username |

---

## 6. API Endpoints

### 6.1. Attendance (`/api/attendance`)

| Method | Endpoint | Mô tả |
|---|---|---|
| `POST` | `/checkin` | Checkin nhân viên |
| `POST` | `/checkout` | Checkout nhân viên |
| `GET` | `/today/{device_id}` | Lấy trạng thái hôm nay theo thiết bị |
| `GET` | `/ban-status/{device_id}` | Kiểm tra trạng thái cấm thiết bị |
| `GET` | `/settings` | Lấu cấu hình giờ AM/PM |
| `POST` | `/settings` | Cập nhật cấu hình giờ AM/PM |

### 6.1.1 Holidays (`/api/holidays`) — admin-only

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/?year=&month=` | Danh sách ngày lễ / nghỉ phép trong kỳ |
| `POST` | `/` | Thêm ngày lễ/phép (scope=all hoặc scope=employee) |
| `DELETE` | `/{id}` | Xóa ngày lễ/phép |

### 6.2. Employees (`/api/employees`)

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/` | Danh sách nhân viên |
| `POST` | `/` | Tạo nhân viên mới |
| `PUT` | `/{id}` | Cập nhật nhân viên |
| `DELETE` | `/{id}` | Xóa nhân viên |
| `GET` | `/departments/all` | Danh sách phòng ban |
| `POST` | `/departments` | Tạo phòng ban |
| `DELETE` | `/departments/{id}` | Xóa phòng ban |

### 6.3. Stats (`/api/stats`)

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/sheet?year=2026&month=8` | Lấy dữ liệu bảng công kỳ tháng |
| `GET` | `/export?year=2026&month=8` | Xuất file Excel bảng công |

### 6.4. Auth (`/api/auth`)

| Method | Endpoint | Mô tả |
|---|---|---|
| `POST` | `/login` | Đăng nhập admin (HTTP Basic) |
| `POST` | `/change-password` | Đổi mật khẩu admin |
| `POST` | `/login-user` | Đăng nhập multi-user |

### 6.5. Settings (`/api/settings`)

| Method | Endpoint | Mô tả | Auth |
|---|---|---|---|
| `GET` | `/ban-duration` | Lấy thời gian cấm thiết bị | Public |
| `PUT` | `/ban-duration` | Cập nhật thời gian cấm | Admin |

### 6.6. Hidden (`/api/hidden/attendance`)

Auth: header `X-Hidden-Token` (lấy từ `POST /api/hidden/login` với `HIDDEN_PASSWORD`).

| Method | Endpoint | Mô tả |
|---|---|---|
| `POST` | `/api/hidden/login` | Đổi mật khẩu → token (hạn 12h) |
| `GET` | `/api/hidden/attendance?from=&to=` | Danh sách bản ghi, lọc theo khoảng ngày (tối đa 366 ngày/lần) |
| `POST` | `/api/hidden/attendance` | Tạo bản ghi mới cho ngày chưa có bản ghi (chỉ ngày đã qua/hôm nay) |
| `PATCH` | `/api/hidden/attendance/{id}/times` | Sửa giờ checkin/checkout (xoá bản ghi nếu cả 2 trống) |

Body của `POST /api/hidden/attendance`:

```json
{ "employee_id": 1, "date": "2026-10-01", "checkin": "08:00", "checkout": "17:30" }
```

Mã lỗi: `404` nhân viên không tồn tại · `409` đã có bản ghi cho ngày đó (dùng `PATCH`) ·
`422` ngày tương lai / cả 2 giờ trống / sai định dạng `HH:MM` / `from > to`.

### 6.7. Hidden Timelog (`/api/hidden/timelog`)

| Method | Endpoint | Mô tả | Auth |
|---|---|---|---|
| `GET` | `/?employee_code=&date=` | Lấy checkin/checkout hiện tại | edit_attendance |
| `POST` | `/update` | Sửa checkin/checkout (ghi vào `time_log`) | edit_attendance |

---

## 7. Tính năng chấm công

### 7.1. Luồng Checkin/Checkout

1. Nhân viên mở trang chấm công → Hệ thống tự sinh Device ID (lưu trong localStorage)
2. Chọn nhân viên từ dropdown
3. Bấm **CHECKIN** → Ghi nhận giờ vào, phân loại ca (AM/PM)
4. Sau đó nút đổi thành **CHECKOUT** → Bấm khi về
5. Hệ thống tính toán: đúng giờ / muộn / về sớm / đủ ngày

### 7.2. Phân loại ca (AM/PM)

| Giờ checkin | Ca | Mô tả |
|---|---|---|
| `checkin <= 12:00` | **AM** | Buổi sáng |
| `checkin > 12:00` | **PM** | Buổi chiều |

- Ca **AM**: Deadline checkin = 08:30 (có thể thay đổi), Deadline checkout = 18:00
- Ca **PM**: Deadline checkin = 13:30, Deadline checkout = 18:00

### 7.3. Chống gian lận (Device Ban)

```
Thiết bị checkin cho NV001
    ↓
Thiết bị bị CẤM checkin trong 30 phút (mặc định)
    ↓
Nhân viên khác dùng cùng thiết bị → BỊ TỪ CHỐI
    ↓
Hết thời gian cấm → Thiết bị tự động được phép dùng lại
```

### 7.4. Hiển thị bảng công (`/admin`) — 3 dòng / NV / ngày

Mỗi nhân viên hiển thị **3 dòng liên tiếp** trong bảng:

| Dòng | Nội dung |
|---|---|
| 1 | Số công (1 / 0.5 / 0 / "-") |
| 2 | Giờ checkin (HH:MM hoặc "-" nếu không có) |
| 3 | Giờ checkout (HH:MM hoặc "No" nếu có checkin mà quên checkout) |

Quy tắc hiển thị theo thời gian:
- Ngày tương lai + hôm nay: tất cả 3 dòng để `-` (chưa tính).
- Ngày quá khứ: dùng `time_log` (lưu qua checkin/checkout hoặc admin sửa qua `/hidden`) để hiển thị số công + giờ.

- Mỗi hành động (checkin/checkout) có cấm riêng
- Có thể chỉnh thời gian cấm trong `/admin`
- Device ID = fingerprint của trình duyệt (localStorage)

### 7.5. Trang Hidden (`/hidden`)

Dùng để chỉnh sửa thủ công các bản ghi chấm công (yêu cầu mật khẩu `HIDDEN_PASSWORD`):

- **Lọc theo khoảng ngày:** hai ô *Từ* / *Đến* lọc bản ghi trong khoảng đó.
  Bỏ trống cả hai thì xem toàn bộ. Bên cạnh vẫn có ô tìm theo mã NV / tên.
- **Tạo bản ghi mới** (nút *➕ Tạo bản ghi*): dùng khi nhân viên quên chấm công nên
  ngày đó **không có bản ghi nào**. Chọn nhân viên + ngày + giờ checkin/checkout.
  - Chỉ tạo được cho **ngày đã qua hoặc hôm nay**.
  - Phải nhập ít nhất một giờ; muốn ngày đó được tính công thì nhập **đủ cả hai**
    (xem [8.2](#82-điều-kiện-tiên-quyết-phải-đủ-checkin-và-checkout)).
  - Nếu ngày đó đã có bản ghi, hệ thống báo trùng — hãy sửa giờ trực tiếp trên bảng.
- **Sửa giờ trực tiếp** trên từng dòng: nhập `HH:MM` hoặc bấm `✕` để xoá một giờ.
  Xoá cả hai giờ thì bản ghi bị xoá hoàn toàn.
- Mọi thay đổi đều được ghi vào `audit_log` với `actor = hidden`.

---

## 8. Quy tắc tính công

### 8.1. Kỳ tính công

- **Bắt đầu:** Ngày 26 tháng trước
- **Kết thúc:** Ngày 25 tháng hiện tại
- **Ví dụ:** Kỳ 08/2026 = 26/07/2026 → 25/08/2026

### 8.2. Điều kiện tiên quyết: phải đủ checkin VÀ checkout

> **Quy tắc quan trọng:** Một ngày chỉ được tính công khi nhân viên có **ĐỦ CẢ HAI**
> giờ: checkin **và** checkout. Thiếu bất kỳ giờ nào trong 2 giờ thì ngày đó tính **0 công**
> (`status = no_checkout`), kể cả thứ Bảy.
>
> Trên bảng công, ô checkout của ngày đó hiển thị `No` để dễ nhận biết nguyên nhân.
> Muốn sửa lại thì vào trang `/hidden` → nhập giờ checkout (hoặc tạo bản ghi mới nếu
> ngày đó chưa có bản ghi nào).

Áp dụng cho **T2 – T7**. Chủ nhật không tính công nên không áp dụng.

### 8.3. Quy tắc tính công (Thứ 2 - Thứ 6) — sau khi đã đủ 2 giờ

Tính công on-the-fly từ bảng `time_log`.

| Checkin | Checkout | Công | Ghi chú |
|---|---|---|---|
| Không checkin | — | **0** | Nghỉ (`absent`) |
| Có checkin | Không checkout | **0** | `no_checkout` — thiếu giờ ra |
| `> 12:00` | `< 13:30` | **0** | PM lỗi (chỉ làm 30 phút) |
| `<= 12:00` | `< 13:30` | **0.5** | Buổi sáng |
| `> 12:00` | `>= 13:30` | **0.5** | Buổi chiều |
| `<= 12:00` | `>= 13:30` | **1** | Full day |

Ngoài ra, giờ "lỗi" ghi đè kết quả trên (xem [8.7](#87-highlight-màu-trên-bảng-công-checkincheckout)):
checkin `> 18:00` hoặc checkout `< 08:30` → **0 công**.

### 8.4. Quy tắc tính công (Thứ 7) — sau khi đã đủ 2 giờ

| Checkin | Checkout | Công |
|---|---|---|
| Có checkin | Không checkout | **0** — thiếu giờ ra |
| Có checkin | Có checkout | **0.5** |
| Không checkin | — | **0** (không tính) |

### 8.5. Thứ Chủ Nhật

- Không tính công, bỏ qua trong bảng công

### 8.6. Đi muộn & Về sớm

**Đi muộn AM:** `max(0, checkin - AM_deadline)` phút
**Về sớm:** `max(0, PM_deadline - checkout)` phút

### 8.7. Highlight màu trên bảng công (checkin/checkout)

Mỗi ô checkin/checkout được tô màu theo rule (dùng để dễ phát hiện sai sót):

| Trạng thái | Quy tắc | Màu hiển thị |
|---|---|---|
| **Checkin đúng giờ** | `timein < 8:30` **HOẶC** `12:00 < timein < 13:30` | Xanh lá nhạt (`#d4edda`) |
| **Checkin muộn** | `8:30 ≤ timein ≤ 12:00` (muộn AM) **HOẶC** `13:30 ≤ timein ≤ 18:00` (muộn PM) | Vàng (`#fff3cd`) |
| **Checkin lỗi** | `timein > 18:00` → **0 công** | Đỏ (`#f8d7da`) |
| **Checkout đúng giờ** | `12:00 < timeout < 13:30` **HOẶC** `timeout ≥ 18:00` | Xanh lá nhạt |
| **Checkout về sớm** | các trường hợp còn lại | Vàng |
| **Checkout lỗi** | `timeout < 8:30` → **0 công** | Đỏ |

**Lưu ý:** Checkin/checkout lỗi có hiệu lực **ghi đè**: ngày đó tính 0 công dù các rule khác có ra sao. Rule áp dụng cho cả T2-T7 (CN không có).

---

## 8.8. Bảng `time_log`

Mỗi lượt checkin/checkout tạo **1 row** trong bảng `time_log` (source of truth). Bảng `attendance` chỉ là summary view được tính lại (MIN/MAX) mỗi khi có action.

Cấu trúc `time_log`:

| Cột | Mô tả |
|---|---|
| `employee_id` | NV |
| `date` | Ngày |
| `action` | `checkin` / `checkout` |
| `time_value` | Giờ thực tế |
| `device_id` | Thiết bị kiosk |
| `is_manual` | `True` nếu admin sửa qua `/hidden` |
| `actor` | `kiosk` / `hidden` / `admin` |

UNIQUE constraint: `(employee_id, date, action)` — mỗi NV mỗi ngày chỉ có 1 row checkin + 1 row checkout. Admin sửa qua endpoint `/api/hidden/timelog/update` sẽ ghi đè.

## 8.9. Bảng `holidays` (nghỉ lễ / nghỉ phép)

Admin thêm ngày lễ (L) hoặc nghỉ phép (P) từ `/admin` → panel "📅 Ngày lễ/Phép":

- **scope = all**: áp dụng cho tất cả NV trong ngày đó.
- **scope = employee**: áp dụng cho 1 NV cụ thể.

Quy tắc công khi rơi vào ngày lễ/phép:

| Thứ | Công |
|---|---|
| T2 - T6 | **1** |
| T7 | **0.5** |
| CN | không tính |

Khi admin xóa holiday: ngày đó được tính lại theo logic chấm công thông thường (on-the-fly, tự động cập nhật khi FE pull).

---

## 9. Cấu hình giờ làm việc

### 9.1. Thay đổi giờ deadline AM/PM

Truy cập **Trang Hidden** (`/hidden`) → Đăng nhập tài khoản `edit_attendance` → Phần "⚙️ Cấu hình giờ checkin/checkout".

```
Giờ checkin AM:  [08:30]  ← Thay đổi rồi bấm "Lưu cấu hình giờ"
Giờ checkout PM: [18:00]  ← Áp dụng từ HÔM NAY trở đi
```

**Quy tắc:**
- Giờ mới **chỉ áp dụng cho các ngày từ ngày thay đổi trở đi**
- Các ngày trước đó giữ nguyên deadline cũ
- Ngày áp dụng được ghi nhận tự động

### 9.2. Thay đổi thời gian cấm thiết bị

Truy cập **Trang Admin** (`/admin`) → Đăng nhập admin → Có thể chỉnh trong phần cấu hình.

---

## 10. Docker Deployment

### 10.1. Cấu hình Docker Compose

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    volumes:
      # Persist SQLite database (không mất dữ liệu khi rebuild)
      - ./data:/app/data
    restart: unless-stopped
    environment:
      - TZ=Asia/Ho_Chi_Minh
```

### 10.2. Các lệnh Docker thường dùng

```bash
# Build image mới (sau khi update code)
docker compose build

# Chạy container
docker compose up -d

# Xem logs
docker compose logs -f

# Xem trạng thái
docker compose ps

# Restart
docker compose restart

# Stop
docker compose down

# Xem logs một service cụ thể
docker compose logs -f app

# Shell vào container (debug)
docker compose exec app /bin/sh
```

### 10.3. Auto-rebuild (Development)

Docker Compose có `watch` mode — khi save file trong `app/`, `static/`, `requirements.txt`, Dockerfile → tự động rebuild.

```bash
docker compose watch   # Chạy watch mode (blocking)
```

### 10.4. Deploy trên server (Production)

```bash
# Trên server, clone hoặc copy thư mục CheckNV
cd /opt/checknv

# Copy database từ máy cũ (nếu có)
cp /backup/checknv.db data/

# Build và chạy
docker compose build
docker compose up -d

# Kiểm tra
curl http://localhost:8000/
```

### 10.5. Deploy với Docker Hub (Pull image thay vì build)

```bash
# Build và tag local
docker build -t checknv:latest .

# Push lên Docker Hub (nếu có registry)
docker tag checknv:latest yourusername/checknv:latest
docker push yourusername/checknv:latest

# Trên server khác, pull và chạy
docker pull yourusername/checknv:latest
docker run -d -p 8000:8000 -v ./data:/app/data --name checknv checknv:latest
```

### 10.6. Cấu hình IP tĩnh cho máy chạy Docker

Để truy cập qua IP cố định trong LAN, hãy đặt IP tĩnh theo dải mạng công ty bạn (ví dụ `10.0.0.50`):

**Cách 1: Đặt IP tĩnh trên máy chạy Docker**
1. Vào Network Settings → Đặt IP tĩnh theo dải LAN của bạn
2. Hoặc cấu hình DHCP reservation trên router

**Cách 2: Port Forwarding (VPS)**
```
Router: Port 80 → IP_server:8000
VPS:    Public_IP:8000 → Container
```

> Lưu ý: IP chỉ là ví dụ. Thay bằng IP thực tế của máy bạn.

---

## 11. Update hệ thống

### 11.1. Quy trình update (Docker)

```bash
# 1. Backup database
cp data/checknv.db data/checknv.db.backup.$(date +%Y%m%d)

# 2. Pull code mới (nếu dùng git)
git pull

# 3. Rebuild
docker compose build

# 4. Restart
docker compose up -d

# 5. Verify
curl http://localhost:8000/
docker compose logs --tail=20
```

### 11.2. Update database schema

Database tự động migrate khi server khởi động nhờ `run_migrations()` trong `database.py`. Các migration được thực hiện idempotent (chạy lại nhiều lần vẫn an toàn).

### 11.3. Rollback

```bash
# Khôi phục database
cp data/checknv.db.backup.20260812 data/checknv.db

# Rebuild image cũ (nếu cần)
docker build -t checknv:rollback -f Dockerfile .

# Restart với image cũ
docker compose down
docker compose run -d --name checknv checknv:rollback
```

---

## 12. Backup & Restore

### 12.1. Backup

```bash
# Backup database
cp data/checknv.db "data/checknv_backup_$(date +%Y%m%d_%H%M%S).db"

# Backup toàn bộ thư mục data
tar -czf "checknv_data_backup_$(date +%Y%m%d).tar.gz" -C /path/to/CheckNV data/

# Backup tự động hàng ngày (crontab Linux)
0 2 * * * cp /opt/checknv/data/checknv.db /backup/checknv_$(date +\%Y\%m\%d).db
```

### 12.2. Restore

```bash
# Copy backup vào thư mục data
cp /backup/checknv_backup_20260812.db data/checknv.db

# Restart Docker
docker compose restart
```

### 12.3. Chuyển dữ liệu sang máy mới

```bash
# Trên máy cũ: backup database
cp data/checknv.db /usb/backup.db

# Trên máy mới: copy thư mục CheckNV mới, rồi copy database
cp /usb/backup.db new_checknv/data/checknv.db

# Build và chạy
cd new_checknv
docker compose build
docker compose up -d
```

---

## 13. Bảo mật

### 13.1. Mật khẩu

- Admin password mặc định: `admin123` → **ĐỔI NGAY SAU KHI CÀI ĐẶT**
- User passwords được hash bằng bcrypt
- Không lưu password dạng plain text

### 13.2. Device Ban

- Mỗi thiết bị bị cấm 30 phút sau khi checkin/checkout
- Chống 1 thiết bị checkin cho nhiều người
- Cấm áp dụng riêng cho checkin và checkout

### 13.3. Trang Hidden (`/hidden`)

- Toàn bộ `/api/hidden/*` trả `404` nếu chưa đặt `HIDDEN_PASSWORD`
- Đăng nhập bằng mật khẩu dùng chung → token hạn 12h, gửi qua header `X-Hidden-Token`
- Rate limit đăng nhập: 5 lần / 60 giây / IP
- Mọi thao tác sửa / tạo / xoá bản ghi đều ghi vào `audit_log` (`actor = hidden`)

### 13.4. Production Checklist

- [ ] Đổi mật khẩu admin mặc định
- [ ] Bật HTTPS (reverse proxy: Nginx + Let's Encrypt)
- [ ] Giới hạn IP truy cập trang `/hidden` (set `ALLOW_ANY_IP = False`)
- [ ] Backup database định kỳ
- [ ] Không để file `.db` trong thư mục public

---

## 14. Giải đáp lỗi thường gặp

### Lỗi: "Thiết bị này đã được dùng để checkin cho nhân viên khác"

**Nguyên nhân:** Device bị cấm do nhân viên khác vừa checkin/checkout.
**Giải pháp:** Đợi 30 phút (thời gian cấm mặc định) hoặc dùng thiết bị/trình duyệt khác.

### Lỗi: "Bạn đã checkin hôm nay rồi"

**Nguyên nhân:** Nhân viên đã checkin hôm nay, không thể checkin lại.
**Giải pháp:** Checkout trước khi checkin lại, hoặc admin xóa bản ghi trong `/hidden`.

### Lỗi: "Nhân viên không tồn tại"

**Nguyên nhân:** Employee ID không đúng hoặc nhân viên chưa được tạo.
**Giải pháp:** Kiểm tra danh sách nhân viên trong `/admin`.

### Lỗi: Database not found

**Nguyên nhân:** Chạy server lần đầu, database chưa được tạo.
**Giải pháp:** Khởi động lại server — database tự tạo kèm default admin.

### Lỗi: Container không start được

```bash
# Xem logs để debug
docker compose logs -f

# Kiểm tra port 8000 có bị chiếm không
netstat -an | grep 8000

# Xóa container và image cũ, rebuild
docker compose down --rmi all
docker compose build --no-cache
docker compose up -d
```

### Lỗi: Dữ liệu bảng công không đúng

**Nguyên nhân:** Thiếu bản ghi hoặc cờ `is_on_time`/`is_early_leave` chưa được set.
**Giải pháp:** Kiểm tra trong `/hidden`, chỉnh sửa thủ công các bản ghi.

### Lỗi: Xuất Excel báo "Excel template not found"

**Nguyên nhân:** Thư mục `resorce/` chưa có file template `.xlsx`.

**Giải pháp:** Đặt 1 file Excel mẫu bảng công vào `resorce/`, đặt tên `template.xlsx` (hoặc đặt tên bất kỳ rồi set biến môi trường `EXPORT_TEMPLATE_FILE=ten_file.xlsx` trong `docker-compose.yml`). Xem chi tiết tại `resorce/README.md`.

---

## License

Nội bộ công ty. Không phân phối bên ngoài.

---

## Clean state cho deployment mới

Phiên bản này đã được **làm sạch** để sẵn sàng triển khai cho công ty mới:

- ✅ Database rỗng (chưa từng có NV nào)
- ✅ Không seed phòng ban mặc định
- ✅ Không whitelist IP cá nhân
- ✅ Không chứa file Excel template của công ty cũ
- ✅ Frontend đã bỏ các header giả IP

**Sau khi cài**, người quản trị cần:

1. Tạo file Excel template (xem `resorce/README.md`) — hoặc bỏ qua nếu không dùng tính năng xuất Excel.
2. Tạo phòng ban qua giao diện `/admin`.
3. Thêm nhân viên qua `/admin`.
4. Đổi mật khẩu admin mặc định (`admin123`).
5. (Khuyến nghị) Bật HTTPS + reverse proxy khi deploy production.
