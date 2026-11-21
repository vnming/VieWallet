# ADR-0008: JWT access ngắn hạn, refresh cookie xoay vòng, session phía server, TOTP cho nhân viên

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Quyết định

- Access JWT (10 phút), chỉ giữ trong bộ nhớ SPA, chứa `sid` và `aud` (web hoặc admin).
- Refresh token: giá trị ngẫu nhiên không mang thông tin, lưu hash trong `sessions`, gửi qua cookie HttpOnly, Secure, SameSite=Strict giới hạn ở `/api/v1/auth`, xoay vòng mỗi lần dùng, có phát hiện dùng lại (thu hồi cả họ token).
- Mỗi request kiểm tra thu hồi `sid` (cache Redis, trễ tối đa 30 giây), nên đăng xuất mọi phiên và thu hồi từ nhân viên có hiệu lực nhanh.
- Nhân viên: bắt buộc TOTP MFA, step-up cho permission nhạy cảm, session tuyệt đối 8 giờ.
- Mật khẩu và PIN: Argon2id; PIN có thêm pepper HMAC.

## Phương án đã cân nhắc

Session thuần bằng cookie phía server (thu hồi đơn giản, nhưng phải xử lý CSRF trên mọi route) và JWT sống lâu (không thu hồi được). Phương án lai được chọn giới hạn rủi ro CSRF ở hai endpoint cookie và cho phép thu hồi nhanh.

## Hệ quả

- Mỗi request có một lần tra Redis/DB (chấp nhận được).
- Tải lại trang cần gọi refresh để lấy access token mới (xử lý khi app khởi động).
