# ADR-0004: Khóa dòng bi quan có thứ tự cộng constraint DB

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Bối cảnh

Các lệnh trừ tiền đồng thời trên cùng một ví không được làm âm ví hay mất cập nhật. Kiểm tra hạn mức không được bị vượt qua bởi request song song.

## Các phương án

1. Kiểm tra version lạc quan trên dòng số dư (bão retry khi tranh chấp).
2. Mức cô lập SERIALIZABLE cho transaction tiền (đúng, nhưng ngầm định; phải xử lý retry ở mọi nơi).
3. **READ COMMITTED + `SELECT ... FOR UPDATE` trên dòng số dư khách hàng theo thứ tự**, cộng dồn nguyên tử cho tài khoản hệ thống ở cuối và theo thứ tự, DB `CHECK (balance >= 0)` và deferred trigger kiểm tra cân bằng journal làm chốt chặn, retry có giới hạn khi gặp lỗi deadlock/serialization.

## Quyết định

Phương án 3, với `lock_timeout` 5 giây và `statement_timeout` 15 giây cho transaction tiền. Hạn mức và rủi ro được kiểm tra sau khi đã giữ khóa ví. Không gọi I/O bên ngoài khi đang giữ khóa.

## Hệ quả

- Vùng găng tường minh và review được; thứ tự khóa cố định ngăn deadlock giữa hai lệnh chuyển ngược chiều.
- Tài khoản hệ thống nóng (FEE_REVENUE) bị xếp hàng khi commit. Chấp nhận được ở quy mô này; chia tài khoản con là phương án đã ghi lại.
- Bắt buộc có test đồng thời trên PostgreSQL thật.
