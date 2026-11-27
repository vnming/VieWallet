# Tài liệu VieWallet

VieWallet là ví điện tử fintech mô phỏng (không tiền thật, không ngân hàng thật, không dữ liệu cá nhân thật). Bắt đầu từ [Báo cáo Phase 0](00-phase0-report.md), [Phase 1](01-phase1-report.md), [Phase 2](02-phase2-report.md), [Phase 3](03-phase3-report.md), [Phase 4](04-phase4-report.md), [Phase 5](05-phase5-report.md), [Phase 6](06-phase6-report.md), [Phase 7](07-phase7-report.md) và [Phase 8](08-phase8-report.md).

## Bản đồ tài liệu

- Sản phẩm: [PRD](product/prd.md) · [Luồng người dùng](product/user-flows.md)
- Kiến trúc: [Tổng quan](architecture/architecture.md) · [Database](architecture/database.md) · [Xử lý tài chính](architecture/financial-processing.md) · [Payment Provider Simulator](architecture/payment-provider-simulator.md) · [Bảo mật, rủi ro, KYC](architecture/security.md) · [Trợ lý AI](architecture/ai-assistant.md) · [Thông báo & PWA](architecture/notifications-pwa.md)
- Thiết kế: [UI/UX và design system](design/design-system.md)
- API: [Đặc tả API](api/api-spec.md)
- Chất lượng: [Chiến lược kiểm thử](quality/test-strategy.md)
- Phát triển: [Hướng dẫn môi trường, test, CI](development/setup.md)
- Kế hoạch: [Roadmap](roadmap.md) · [ADR](adr/README.md)

## Quy tắc giữ tài liệu đúng với code

1. Khi một phase đã giao, tài liệu mô tả hành vi đã cài đặt; phần chỉ mới thiết kế được đánh dấu "Bản nháp".
2. Thay đổi hành vi tài chính phải cập nhật [financial-processing.md](architecture/financial-processing.md) trong cùng commit.
3. Thay đổi schema phải cập nhật [database.md](architecture/database.md) và đi kèm migration Alembic.
4. Quyết định quan trọng mới phải có ADR.
5. Tài liệu viết bằng tiếng Việt, giữ nguyên thuật ngữ kỹ thuật tiếng Anh (ADR-0011).
