# Architecture Decision Records (ADR)

Định dạng: bản ghi ngắn kiểu MADR. Trạng thái: Proposed (đề xuất), Accepted (đã chấp nhận), Superseded (bị thay thế). Toàn bộ ADR dưới đây được chủ dự án chấp nhận ngày 2026-11-21.

| ADR | Tiêu đề | Trạng thái |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith với ranh giới module được kiểm tra tự động | Accepted |
| [0002](0002-double-entry-ledger.md) | Ledger kế toán kép, bất biến, có balance projection | Accepted |
| [0003](0003-money-representation.md) | Tiền là số nguyên đơn vị nhỏ nhất; chuỗi trong JSON | Accepted |
| [0004](0004-concurrency-control.md) | Khóa dòng bi quan có thứ tự cộng constraint DB | Accepted |
| [0005](0005-simulator-as-separate-service.md) | Payment Provider Simulator là service riêng | Accepted |
| [0006](0006-postgres-outbox-and-jobs.md) | Outbox và hàng đợi job trong PostgreSQL; Redis chỉ cho rate limit | Accepted |
| [0007](0007-frontend-monorepo-separate-admin.md) | Monorepo pnpm, app khách hàng và app admin tách riêng | Accepted |
| [0008](0008-authentication-tokens-sessions.md) | JWT access ngắn hạn, refresh cookie xoay vòng, session phía server, TOTP cho nhân viên | Superseded bởi 0012 |
| [0009](0009-ai-orchestration.md) | Tự chạy vòng lặp tool AI sau provider port; chỉ model miễn phí; tool chỉ-đọc | Accepted |
| [0010](0010-runtime-versions.md) | Python 3.14, PostgreSQL 18, Node 24, Docker Desktop | Accepted |
| [0011](0011-documentation-language.md) | Tài liệu tiếng Việt; code và commit tiếng Anh; UI ưu tiên tiếng Việt | Accepted |
| [0012](0012-server-side-session-cookies.md) | Session phía server bằng cookie HttpOnly, không dùng JWT | Accepted |
