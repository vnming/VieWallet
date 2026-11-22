# ADR-0012: Session phía server bằng cookie HttpOnly (không dùng JWT)

- Trạng thái: Accepted (2026-11-22, chủ dự án chọn). Thay thế ADR-0008.
- Ngày: 2026-11-22

## Bối cảnh

Chủ dự án quyết định tạm thời không dựng JWT. Hai app (web, admin) gọi API cùng origin qua nginx (production-like) hoặc Vite proxy (dev), nên cookie là cách mang phiên tự nhiên nhất.

## Quyết định

- **Token phiên**: 32 byte ngẫu nhiên (`secrets.token_urlsafe`). Server chỉ lưu SHA-256 của token trong bảng `sessions`, không lưu token gốc.
- **Cookie**: `vw_session` (app khách hàng) và `vw_admin_session` (app admin); `HttpOnly`, `Secure` (tắt được bằng `VW_COOKIE_SECURE=false` khi test qua HTTP), `SameSite=Strict`, `Path=/api`. Cookie trên `127.0.0.1` không tách theo cổng, nên mỗi app dùng tên cookie riêng và server kiểm tra `audience` của session.
- **Thời hạn**:
  - Web: nhàn rỗi 7 ngày, tuyệt đối 30 ngày.
  - Admin: nhàn rỗi 30 phút, tuyệt đối 8 giờ.
  - `last_seen_at` được cập nhật tối đa mỗi 60 giây để giảm số lần ghi.
- **Kiểm tra mỗi request**: tra session theo hash. Session bị thu hồi, hết hạn hoặc sai audience ⇒ 401. Thu hồi có hiệu lực ngay, không cần refresh token.
- **Chống session fixation**: cấp token mới khi đăng nhập, và cấp lại khi nhân viên xác thực MFA xong (session `MFA_PENDING` → `ACTIVE`).
- **CSRF**: cookie được trình duyệt gửi tự động, nên mọi request ghi (`POST/PUT/PATCH/DELETE`) dưới `/api/` phải có header `X-VW-CSRF: 1`. Request cross-site không thêm được header này nếu không qua CORS preflight, mà CORS chỉ cho phép origin trong allow-list. Ngoài ra, nếu có header `Origin` thì Origin đó phải nằm trong allow-list. Ngoại lệ: webhook provider (Phase 4) xác thực bằng chữ ký, không bằng cookie.
- **Đăng xuất**: thu hồi session hiện tại và xóa cookie. Đăng xuất mọi phiên: thu hồi mọi session của user.
- **Nhân viên**: đăng nhập admin tạo session `MFA_PENDING`, sống 10 phút, chỉ được gọi các endpoint MFA. Bắt buộc đăng ký TOTP ở lần đầu. Permission nhạy cảm cần đã xác thực MFA trong 15 phút gần nhất (step-up).

## Phương án đã cân nhắc

- JWT access + refresh cookie (ADR-0008): bị hoãn theo quyết định của chủ dự án.
- Bearer token không mang thông tin, giữ trong bộ nhớ SPA: tải lại trang là mất phiên, trừ khi thêm refresh cookie (phức tạp gần bằng JWT).

## Hệ quả

- Mỗi request có một lần tra DB theo index unique (chấp nhận được ở quy mô dự án). Có thể thêm cache Redis sau nếu đo thấy cần.
- Client không thể đọc token (HttpOnly), nên XSS không lấy được phiên. CSRF được xử lý bằng SameSite + header bắt buộc + Origin.
- Nếu sau này cần JWT (ví dụ cho mobile native), có thể thêm cơ chế đó song song mà không ảnh hưởng session cookie.
