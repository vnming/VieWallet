# Báo cáo Phase 7 — Hoàn thiện app khách hàng

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-24), trừ đo Lighthouse (xem §4) |
| Nhánh | `phase-1/foundation` |
| Migration | `0007` |
| Liên quan | [Design system §9 — 7 trạng thái](design/design-system.md), [Đặc tả API §3e](api/api-spec.md), [Hướng dẫn §3b4](development/setup.md) |

## 1. Phạm vi đã giao

### Backend

| Phần | Nội dung |
|---|---|
| Hồ sơ, cài đặt | `users.phone` (di động Việt Nam mô phỏng, chuẩn hóa `+84…`, không trùng), `theme` (theo hệ thống/sáng/tối), `privacy_mode`; lưu ở server nên áp dụng mọi thiết bị. Đã KYC thì không đổi được họ tên (khớp hồ sơ đã xác minh) |
| Lịch sử | Lọc theo chiều tiền, khoảng ngày (giờ Việt Nam), khoảng số tiền, tìm theo mã/nội dung/tên đối tác (ký tự `%` `_` là chữ thường) |
| Danh mục | 13 danh mục hệ thống; mặc định theo loại, chiều và mã ngành của cửa hàng; người dùng đổi được cho phía của mình (bảng `transaction_categories`) |
| Xuất CSV | Job bất đồng bộ (idempotent), tối đa 366 ngày và 10.000 dòng, UTF-8 có BOM, chống chèn công thức bảng tính, tự hết hạn sau 24 giờ |
| Trợ giúp | 9 bài viết vi + en trong repo, seed vào DB, tìm không dấu, xem được khi chưa đăng nhập |
| Ticket hỗ trợ | Khách tạo (gắn được giao dịch của chính mình), nhắn tin, đóng thì không trả lời được, trả lời ticket đã giải quyết thì mở lại; nhân viên `ticket:manage:any` trả lời, ghi chú nội bộ, đổi trạng thái; thông báo in-app khi nhân viên trả lời; tin nhắn chỉ-thêm; giới hạn 5 ticket/giờ |

### Frontend (app khách hàng)

- Trung tâm **Tài khoản** gom hồ sơ, cài đặt, bảo mật, KYC, hạn mức, tài khoản nhận, người nhận đã lưu, hỗ trợ, trợ giúp. Menu: thêm Tài khoản, Trợ giúp.
- **Hồ sơ** (tên, tên hiển thị, SĐT; đổi email bằng mật khẩu + mã gửi tới email mới), **Cài đặt** (ngôn ngữ, giao diện, chế độ riêng tư; đổi ngay khi bấm, lưu nền), **Người nhận đã lưu**.
- **Trợ giúp** (tìm kiếm, bài viết render Markdown thành React element — không dùng HTML), **Hỗ trợ** (danh sách, tạo, hội thoại), nút **Báo lỗi giao dịch** trên chi tiết giao dịch.
- **Lịch sử**: ô tìm kiếm, bộ lọc chiều/ngày/số tiền, xóa bộ lọc, đổi danh mục ở chi tiết, xuất CSV.
- **Chế độ riêng tư** ẩn số dư và số tiền trong danh sách; **giao diện tối** đồng bộ theo tài khoản; **banner ngoại tuyến** toàn cục.
- Định nghĩa và rà soát **7 trạng thái** (design-system §9). Trang rút tiền, KYC, hạn mức giờ giữ tiêu đề khi đang tải/lỗi.

## 2. Kết quả kiểm tra (2026-11-24)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (210 file), import-linter (4 contract; `support` cùng lớp với `operations`) | Đạt |
| `alembic check`, downgrade/upgrade `0007` | Đạt |
| Backend pytest | **1192 passed**, coverage **93 %** |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **160 passed** (gồm 42 lượt axe trên 21 trang × 2 trạng thái) |
| axe trên trình duyệt thật (có tương phản màu): trợ giúp, cài đặt (cả giao diện tối), ticket, lịch sử, tài khoản | 0 vi phạm serious/critical |
| Playwright trên compose stack (`--workers=2`; web desktop + mobile, admin) | **32 passed** (3,1 phút) |

Test chính: `test_customer_hub.py` (hồ sơ/SĐT/khóa tên sau KYC; lọc lịch sử và ký tự đặc biệt; danh mục riêng mỗi phía, theo mã ngành cửa hàng; CSV: khoảng ngày sai, idempotent, chưa sẵn sàng, của người khác ⇒ 404, nội dung và chống chèn công thức, hết hạn; trợ giúp tìm không dấu; ticket: gắn giao dịch không phải của mình ⇒ 404, ghi chú nội bộ không lộ cho khách, mở lại, đóng, thông báo; giới hạn tạo ticket). Frontend: `account.test.tsx`, `support.test.tsx`, `a11y.test.tsx`. E2E: `web.account.spec.ts`.

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Trang rút tiền, KYC, hạn mức mất tiêu đề trang (h1) khi đang tải hoặc lỗi | Quét axe ở trạng thái lỗi | Giữ tiêu đề trong mọi trạng thái |
| Ô "Nội dung" của ticket có tên truy cập dính cả dòng gợi ý | Test component | Nhãn riêng + `aria-describedby` |
| Ô chế độ riêng tư chỉ đổi sau khi server trả lời; hai thay đổi liên tiếp ghi đè nhau; truy vấn `/me` đang chạy xóa giá trị vừa chọn | E2E | Cập nhật lạc quan có state cục bộ, chỉ gộp trường vừa đổi, hủy truy vấn đang chạy |
| Simulator chạy với thời gian chờ mặc định (2 phút) sau một lần khởi động lại một phần stack, làm hành trình 3 và 7 quá thời gian | E2E | Khởi động lại stack với biến môi trường E2E (không phải lỗi code) |
| Sau khi tạo lại riêng container `api`, nginx của `admin` giữ IP cũ và trả 502, làm hỏng các test admin | E2E | `docker compose restart admin web worker`; ghi chú trong setup.md |
| Hành trình 8 duyệt khoản giữ đầu tiên trong hàng đợi, nên duyệt nhầm khoản do lần chạy hỏng trước để lại | E2E | Test lấy mã giao dịch từ URL và duyệt đúng dòng đó |

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Lighthouse accessibility ≥ 90 | Đo | **Chưa đo**: tải Lighthouse qua `npx` bị kiểm soát quyền của phiên chặn. Thay bằng axe-core (jsdom + trình duyệt thật). Cần chủ dự án cho phép tải gói `lighthouse` hoặc tự chạy |
| Lọc lịch sử theo danh mục | — | Chưa có (danh mục mặc định tính khi đọc); để Phase 10 (báo cáo) |
| SĐT | Tra cứu người nhận theo SĐT | Lưu SĐT mô phỏng, chưa xác minh bằng SMS nên chưa dùng để tra cứu |
| UI admin ticket | Phase 8 | Chỉ API (có test) |
| Tinh chỉnh chuyển động | Phase 7 | Không thêm hiệu ứng mới; các hiệu ứng có sẵn tôn trọng `prefers-reduced-motion` (design-system) |

## 5. Rủi ro còn lại

- Nội dung CSV nằm trong DB tới khi hết hạn (tối đa 24 giờ); cần giới hạn số job mỗi người nếu chạy dài hạn.
- Bài trợ giúp chỉ cập nhật qua repo + seed; trình soạn thảo cho nhân viên ở Phase 8.
- Máy dev 12 GB RAM thường còn 0,2–1 GB trống khi chạy Docker + trình duyệt + test; E2E và vitest phải giảm số worker.

## 6. Bước tiếp theo

Phase 8 — Admin & Operations. Cần chủ dự án xác nhận trước khi bắt đầu.
