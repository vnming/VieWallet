# ADR-0006: Outbox và hàng đợi job trong PostgreSQL; Redis chỉ cho rate limit

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Bối cảnh

Thông báo, gửi lệnh tới provider, truy vấn trạng thái và analytics phải diễn ra tin cậy sau khi commit tài chính. Ghi kép (commit DB rồi publish sang broker) có thể làm mất hoặc nhân đôi event.

## Quyết định

- Bảng transactional outbox được ghi trong transaction nghiệp vụ. Worker nhận dòng bằng `FOR UPDATE SKIP LOCKED`, retry với backoff và chuyển dead-letter sau N lần. Handler idempotent.
- Scheduled job nằm trong bảng PostgreSQL (`scheduled_jobs`). Worker khóa dòng của job bằng `FOR UPDATE SKIP LOCKED` và dời `next_run_at` trong cùng transaction, nên mỗi chu kỳ chỉ một worker chạy job. (Đề xuất ban đầu là advisory lock; cài đặt ở Phase 1 dùng khóa dòng vì đơn giản hơn và lưu được trạng thái lần chạy cuối.)
- Redis chỉ dùng cho bộ đếm rate limit và cache ngắn hạn (cache thu hồi session). Mất Redis chỉ làm suy giảm rate limit, không bao giờ ảnh hưởng tính đúng.
- Không dùng Celery, RabbitMQ, Kafka.

## Hệ quả

- Hiệu ứng đúng một lần nhờ giao ít nhất một lần cộng handler idempotent.
- Thông lượng bị giới hạn bởi polling PostgreSQL (hàng nghìn event mỗi phút), quá đủ cho dự án này.
