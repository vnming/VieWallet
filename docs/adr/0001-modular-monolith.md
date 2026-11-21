# ADR-0001: Modular monolith với ranh giới module được kiểm tra tự động

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Bối cảnh

Sản phẩm có khoảng 20 mảng nghiệp vụ nhưng chỉ một người phát triển, một database và yêu cầu nhất quán cao (một lệnh chuyển tiền chạm vào transactions, ledger, pricing, risk và audit một cách nguyên tử). Microservices sẽ buộc phải dùng distributed transaction hoặc saga cho mọi lần di chuyển tiền.

## Quyết định

Một codebase backend, triển khai thành hai process (`api`, `worker`). Code chia thành module trong `app/modules/*`, mỗi module sở hữu bảng của mình. Module giao tiếp qua interface `public.py` (đồng bộ, trong cùng DB transaction khi cần nguyên tử) hoặc qua domain event bằng outbox (bất đồng bộ). Contract `import-linter` trong CI cấm import phần nội bộ của module khác.

## Hệ quả

- Transaction ACID xuyên module, không cần điều phối phân tán.
- Ranh giới tường minh và kiểm thử được; về sau có thể tách module ra nếu thật sự cần.
- Kỷ luật được công cụ đảm bảo, không chỉ dựa vào quy ước.
- Payment Provider Simulator được tách riêng có chủ đích (ADR-0005) vì nó mô phỏng hệ thống bên ngoài.
