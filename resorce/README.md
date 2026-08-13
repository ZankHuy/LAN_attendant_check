# `resorce/` — thư mục template Excel

Thư mục này chứa **file Excel template** dùng cho chức năng "Xuất Excel" (`/api/stats/export`).

## Cách sử dụng

### 1. Tạo file template của bạn

Mở Excel, thiết kế 1 sheet bảng công mẫu với các vùng:

- **Dòng tiêu đề** (tên công ty, kỳ chấm công) — tự do.
- **Dòng ngày trong tháng** (G9..AK9 là cột ngày từ 26 đến 25).
  - Ô `C1` = tháng (1–12), ô `C2` = năm (vd `2026`).
  - Ô `G9` được code tự ghi giá trị concrete (26 của tháng trước). Các cột `H9..AK9` giữ công thức `+1` để tự cộng dồn.
- **Dòng thứ trong tuần** (T2..CN) — `=CHOOSE(WEEKDAY(<col>9), ...)` nếu muốn tự động.
- **Dòng số ngày** (G7..AK7) — code sẽ ghi lại `=DAY(<col>9)` để luôn khớp với G9.
- **Body chấm công** — bắt đầu từ **dòng 12**. Code sẽ xoá sạch các dòng 12..31 của template trước khi ghi dữ liệu mới.

Lưu file vào thư mục này với tên bất kỳ, ví dụ `template.xlsx` hoặc `bang_cong_2026.xlsx`.

### 2. Trỏ code vào template

Mặc định code tìm file `resorce/template.xlsx`. Có 2 cách đổi tên file:

**Cách A — Đặt tên file là `template.xlsx`** (khuyến nghị cho setup nhanh):
```bash
cp my_template.xlsx resorce/template.xlsx
```

**Cách B — Đặt tên tuỳ ý và export biến môi trường**:

Trong `docker-compose.yml`:
```yaml
environment:
  - EXPORT_TEMPLATE_FILE=bang_cong_2026.xlsx
```

Hoặc chạy local:
```bash
EXPORT_TEMPLATE_FILE=bang_cong_2026.xlsx uvicorn app.main:app
```

### 3. Khởi động lại

Nếu dùng Docker:
```bash
docker compose up -d --build
```

Nếu mount volume (xem `docker-compose.yml` mặc định — `./resorce:/app/resorce`), chỉ cần `docker compose restart`, không cần build lại.

## Lưu ý

- File template là **read-only** — code không bao giờ ghi đè lên file gốc.
- Mỗi lần xuất sẽ tạo 1 file mới `BangChamCong_T{thang}_{nam}.xlsx` trả về cho client (qua `Content-Disposition: attachment`).
- **Nếu không có template**: nút "Xuất Excel" trong `/admin` sẽ trả về lỗi 503 với hướng dẫn chi tiết. Mọi chức năng khác (checkin/checkout, bảng công) vẫn hoạt động bình thường.

## Trạng thái clean

Template ban đầu được phát triển cho công ty cũ, đã được loại bỏ khi clean dự án để tránh lộ thông tin nội bộ. Mỗi khách hàng / công ty mới cần tự chuẩn bị template riêng cho phù hợp với bảng công nội bộ của họ.

Tham khảo `README.md` mục "Clean state cho deployment mới" và `HUONG-DAN-VAN-HANH.md` mục 7.4.