# ADR-0011: Tài liệu tiếng Việt; code và commit tiếng Anh; UI ưu tiên tiếng Việt

- Trạng thái: Accepted (chủ dự án quyết định 2026-11-21; thay thế đề xuất ban đầu viết tài liệu bằng tiếng Anh)
- Ngày: 2026-11-21

## Quyết định

- Tài liệu dự án trong `docs/` và `CLAUDE.md` viết bằng **tiếng Việt**. Thuật ngữ kỹ thuật chuẩn giữ nguyên tiếng Anh (ledger, idempotency, state machine, outbox, ...) để khớp với code và tài liệu tham khảo.
- Code, tên định danh, comment trong code, commit message và tên file viết bằng tiếng Anh (chuẩn ngành, tương thích công cụ).
- UI sản phẩm mặc định tiếng Việt, tiếng Anh là ngôn ngữ thứ hai.
- Trao đổi với chủ dự án bằng tiếng Việt.

## Hệ quả

- Tài liệu dễ đọc với chủ dự án và người dùng Việt Nam.
- Người đọc quốc tế (portfolio) cần bản dịch nếu muốn; có thể bổ sung README tiếng Anh ở Phase 13.
