# CLAUDE.md — VieWallet

Ví điện tử fintech mô phỏng (dự án portfolio). **Không tiền thật, không ngân hàng thật, không API thanh toán thật, không dữ liệu cá nhân thật.** Mọi luồng tiền đi qua ledger nội bộ và Payment Provider Simulator.

## Trạng thái hiện tại

- Phase 0–4 hoàn tất (báo cáo `docs/01-…` tới `docs/04-phase4-report.md`). Phase 5 chưa được cho phép.
- Payment Provider Simulator là service riêng (`backend/sim`, DB `viewallet_sim`, cổng 8090); không import chéo với `app` (import-linter).
- Đăng nhập: session cookie phía server (ADR-0012), không dùng JWT. Mọi request ghi cần header `X-VW-CSRF: 1`.
- Lệnh chạy, test, CI: `docs/development/setup.md`.
- Không bắt đầu phase mới khi chưa có xác nhận rõ ràng của chủ dự án.
- Nguồn thiết kế chính: `docs/` (bắt đầu từ `docs/00-phase0-report.md`). Quyết định: `docs/adr/`.

## Quy tắc bắt buộc

- Tiền: số nguyên đơn vị nhỏ nhất (`BIGINT`, Python `int`, value object `Money`). Không bao giờ dùng float. Số tiền trong JSON là chuỗi.
- Ledger: kế toán kép, chỉ-thêm. Không bao giờ sửa/xóa journal, posting, transaction event hay audit log. Sửa sai bằng giao dịch mới có liên kết.
- Chỉ module `ledger` được thay đổi số dư. Mọi thay đổi trạng thái tài chính nằm trong một DB transaction cùng outbox và audit row.
- Khóa dòng số dư khách hàng bằng `SELECT ... FOR UPDATE` theo thứ tự account id; cập nhật tài khoản hệ thống sau cùng theo thứ tự; không gọi I/O bên ngoài khi đang giữ khóa.
- Endpoint di chuyển tiền bắt buộc `Idempotency-Key`. Callback provider idempotent và được kiểm chứng chữ ký.
- Trạng thái giao dịch chỉ đổi qua state machine của từng loại (compare-and-set). Xem `docs/architecture/financial-processing.md`.
- Phí, hạn mức, quyết định rủi ro và analytics chỉ tính ở backend.
- AI: chỉ dùng **model miễn phí** (Ollama local mặc định, gói free cloud tùy chọn, fake cho test). Tool AI chỉ đọc (cộng link bản nháp). AI không bao giờ di chuyển tiền, sửa bản ghi hay vượt qua rule.
- Phân quyền kiểm tra ở backend cho mọi route; frontend chỉ ẩn UI.
- Mọi thay đổi schema đi qua migration Alembic. Không hardcode bí mật.
- Ranh giới module: chỉ import module khác qua `public.py`.
- Tính năng chỉ xong khi có test (kể cả đường lỗi), lint, type check, build và tài liệu cập nhật.

## Dừng lại và hỏi chủ dự án trước khi

Thay đổi kiến trúc lớn, lựa chọn ảnh hưởng tính đúng tài chính, thao tác phá hủy hoặc làm mất dữ liệu, thay đổi bảo mật quan trọng, tích hợp dịch vụ bên ngoài, bất cứ thứ gì tốn tiền, push lên GitHub.

## Ghi chú môi trường (Windows 11)

- Python: dùng `uv run ...` (Python 3.14; không có alias `python3`). Frontend: pnpm.
- uv dùng `link-mode = copy` (thư mục đồng bộ cloud không hỗ trợ hardlink).
- Dùng `127.0.0.1`, không dùng `localhost` (IPv6 `::1` làm kết nối tới Docker bị treo). Postgres container ở cổng `55432` (máy có PostgreSQL native ở 5432/5433, không được động vào).
- psycopg async trên Windows cần `SelectorEventLoop` (đã xử lý trong worker, seed, pytest).
- TypeScript ghim 6.0 (typescript-eslint chưa hỗ trợ 7).
- Docker Desktop đã cài (29.9.3, Compose v5.7.0); CLI ở `C:\Users\MINH\AppData\Local\Programs\DockerDesktop\resources\bin`. Nếu `docker` không nhận, khởi động lại terminal hoặc thêm đường dẫn này vào PATH của phiên.
- Phần cứng: RAM 12 GB, RTX 4050 Laptop 6 GB VRAM. Hạn chế chạy cùng lúc Docker stack đầy đủ và Ollama.

## Ngôn ngữ

Tài liệu (`docs/`, file này) viết tiếng Việt, giữ thuật ngữ kỹ thuật tiếng Anh. Code, comment trong code, commit message bằng tiếng Anh. UI ưu tiên tiếng Việt (vi mặc định, en thứ hai). Trao đổi với chủ dự án bằng tiếng Việt.
