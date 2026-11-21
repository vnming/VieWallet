# ADR-0005: Payment Provider Simulator là service riêng

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Quyết định

Simulator là FastAPI service riêng với schema DB, DB role và bí mật riêng, trang checkout "SimBank", callback bất đồng bộ có chữ ký, sao kê và điều khiển kịch bản. VieWallet chỉ giao tiếp với nó qua port `PaymentProvider` bằng HTTP.

## Lý do

Fake chạy trong cùng process không tái hiện chân thực timeout, lỗi kết nối, callback trễ hoặc trùng và việc xử lý chữ ký. Service riêng khiến code tích hợp giống hệt những gì một provider thật đòi hỏi.

## Hệ quả

- Thêm một container; cần contract test giữa adapter và simulator.
- Thay bằng provider thật nghĩa là viết adapter mới cộng cấu hình.
- Endpoint điều khiển simulator chỉ tồn tại ở môi trường development và simulation.
