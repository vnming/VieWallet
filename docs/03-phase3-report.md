# Báo cáo Phase 3 — Wallet & Financial Core

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-23) |
| Nhánh | `phase-1/foundation` (tiếp tục trên cùng nhánh) |
| Migration | `0003` — ví, ledger, giao dịch, pricing, risk, phê duyệt, đối soát |
| Liên quan | [Xử lý tài chính §13](architecture/financial-processing.md#13-trạng-thái-cài-đặt-phase-3), [Database §3.3–3.7](architecture/database.md), [Đặc tả API §3a](api/api-spec.md) |

## 1. Phạm vi đã giao

### Backend

| Module | Nội dung |
|---|---|
| `ledger` | Hệ thống tài khoản (6 mã), quy tắc kế toán kép thuần Python, engine posting: khóa `FOR UPDATE` số dư ví khách theo thứ tự id, kiểm tra đủ tiền trước khi ghi, cập nhật tài khoản hệ thống nguyên tử sau cùng; đảo journal đúng một lần; kiểm tra bất biến I1–I4 bằng SQL |
| `wallet` | Số ví 10 chữ số (Luhn), mở ví kèm tài khoản ledger (idempotent, an toàn khi gọi đồng thời, không để lại tài khoản mồ côi), tự khóa / mở khóa bằng PIN, nhân viên khóa ngay, mở khóa qua phê duyệt |
| `transactions` | State machine khai báo cho 7 loại giao dịch, đổi trạng thái bằng compare-and-set + `transaction_events` chỉ-thêm + outbox, mã tham chiếu `VW` + ngày + 10 ký tự Crockford, lịch sử có cursor và bộ lọc, use case ADJUSTMENT |
| `pricing` | Phí (cố định + bps làm tròn half-up, kẹp min/max, rule cụ thể nhất thắng), hạn mức theo lần/ngày/tháng/trần số dư, seed mặc định theo bảng §8 |
| `risk` | Interface `evaluate(ctx)` + 5 rule đầu (velocity số lượng, velocity số tiền, thời gian chờ sau đổi thông tin đăng nhập, thất bại lặp lại, trùng lặp), lưu `risk_assessments` chỉ-thêm |
| `operations` | Maker-checker: đề xuất có hash payload, người duyệt phải khác người tạo, thực hiện trong cùng DB transaction với quyết định, từ chối / hủy / hết hạn chạy bước bù (hủy giao dịch chờ); đối soát nội bộ I1–I5 chạy mỗi giờ và theo yêu cầu |
| Migration `0003` | 14 bảng; trigger ràng buộc trễ kiểm tra journal cân bằng lúc commit; `journals`, `postings`, `transaction_events`, `risk_assessments` chỉ-thêm; rule phí/hạn mức chỉ đọc với role ứng dụng |
| Worker | Job `internal_reconciliation` (1 giờ), `expire_approval_requests` (5 phút) |

### Frontend

- **App khách hàng**: thẻ số dư (ẩn/hiện số dư, sao chép số ví), trang Ví (tự khóa, mở khóa bằng PIN, thông báo khi nhân viên khóa), Lịch sử giao dịch (lọc theo loại/trạng thái, tải thêm theo cursor), Chi tiết giao dịch với timeline trạng thái.
- **App admin** (tối thiểu, đúng roadmap): Tra cứu ví (khóa ví, đề nghị mở khóa), Điều chỉnh số dư (một Idempotency-Key cho mỗi ý định, gửi lại dùng lại key), Phê duyệt (duyệt/từ chối/hủy, tự mở hộp thoại step-up khi MFA cũ hơn 15 phút), Đối soát.
- **Component mới**: `TransactionRow`, `StatusTimeline`. `api-client` có `financeApi` và hook `useWallet`, `useTransactions` (infinite query), `useTransaction`.

## 2. Kết quả kiểm tra (2026-11-23)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (130 file), import-linter (3 contract, thêm contract phân lớp module) | Đạt |
| Backend pytest | **1061 passed**, coverage **95,3 %** |
| `alembic check`, downgrade/upgrade `0003` | Đạt |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **74 passed** |
| Playwright trên compose stack | **16 passed**, chạy hai lần liên tiếp đều đạt |
| Đối soát trên DB dev sau e2e | 0 vi phạm |

Tiêu chí nghiệm thu của roadmap:

| Mã | Nội dung | Test |
|---|---|---|
| FT-23 | Đảo hai lần bị từ chối | `test_reversal_once_only` (service + `UNIQUE reverses_journal_id`) |
| FT-24 | Mọi bước chuyển bị cấm | `test_state_machine.py`: 700 bộ (loại, từ, đến) so với danh sách cạnh viết lại độc lập từ tài liệu |
| FT-25 | UPDATE/DELETE trên ledger, event, audit | 13 câu lệnh bằng role ứng dụng bị chặn; trigger chặn cả chủ bảng |
| FT-26 | Journal không cân (bỏ qua domain) | Trigger trễ từ chối lúc commit: lệch số tiền, 1 dòng, không dòng nào, lệch tiền tệ |
| FT-27 | 10.000 thao tác ngẫu nhiên | Test thuộc tính ở mức domain (nạp, chuyển có phí, giữ/tất toán/hoàn giữ rút tiền, thanh toán, đảo, điều chỉnh): I1–I5 đúng sau mỗi bước; thêm Hypothesis |
| FT-29 | Làm tròn phí ở biên | 8 trường hợp bps + kẹp min/max |
| FT-30 | Maker tự duyệt | Service trả 403 `APPROVAL_SELF_DECISION` (có audit) và CHECK DB `maker_is_not_checker` |

Test quan trọng khác: 20 lệnh trừ tiền đồng thời trên số dư đủ cho 7 lệnh ⇒ đúng 7 thành công, số dư về 0; 30 lệnh chuyển A→B và B→A đồng thời không deadlock; 5 lần mở ví đồng thời ⇒ 1 ví, 0 tài khoản mồ côi; đảo giao dịch khi người nhận đã tiêu hết ⇒ từ chối; trừ tiền thiếu số dư khi duyệt ⇒ yêu cầu FAILED, giao dịch CANCELLED, số dư không đổi; điều chỉnh chờ duyệt không hiện cho khách; giao dịch của người khác ⇒ 404; ghi chú nội bộ không lộ ra API khách; Idempotency-Key gửi lại trả đúng response cũ, khác body ⇒ 422; duyệt cần step-up; yêu cầu hết hạn sau 72 giờ tự hủy giao dịch; kiểm tra bất biến phát hiện số dư bị sửa tay (I2, I4) và HOLD không khớp (I5).

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Người ẩn danh gọi `POST /admin/adjustments` nhận 400 (thiếu Idempotency-Key) thay vì 401 | Test ma trận phân quyền sinh từ OpenAPI | Xác thực chạy trước kiểm tra header |
| API trả 500 ở lần mở ví đầu tiên trên stack Docker: metadata của process API thiếu bảng `currencies`/`users` nên khóa ngoại không resolve | E2E (pytest không thấy vì đã import mọi module) | Model import tường minh các bảng đích; test mới chạy `app.main` và `app.worker` trong process riêng, kiểm tra mọi khóa ngoại có bảng đích |
| Dọn dữ liệu test bị trigger chỉ-thêm chặn khi `TRUNCATE ... CASCADE` lan tới `transaction_events` | Chạy test | Fixture (chủ bảng) tạm tắt trigger TRUNCATE trong chính transaction dọn dẹp; role ứng dụng không làm được việc này |
| Response API khai báo trạng thái là chuỗi tự do, frontend không kiểm tra được kiểu | `tsc` | Response dùng enum, OpenAPI và type sinh ra có danh sách giá trị |

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Thời điểm mở ví | Khi xác minh email | Ở lần gọi đầu `GET /wallet` (và mọi API ví): idempotent, an toàn đồng thời. Tránh để `identity` phụ thuộc `wallet` (vi phạm phân lớp) |
| Bất biến I5 | Σ giao dịch ở trạng thái giữ tiền | Cột `transactions.held_minor` ghi rõ số tiền đang giữ; I5 so Σ cột này với số dư HOLD. Không phụ thuộc cách suy luận theo loại/trạng thái |
| Rule rủi ro | Bảng `risk_rules` | Rule trong code có `rules_version`; bảng và công cụ admin ở Phase 6 |
| Hạn mức và rủi ro trong luồng tiền | Có engine | Engine + seed + test đủ; được nối vào bước xác nhận khi có luồng trừ tiền (Phase 4–5). ADJUSTMENT của nhân viên không qua hạn mức/rủi ro |
| Tự khóa ví khi vi phạm bất biến | Có (§11) | Chưa: ghi log lỗi, audit `FAILED`, outbox `reconciliation.violation_found`. Khóa tự động làm ở Phase 8 cùng hàng đợi vận hành |
| Seed demo tự động | Lệnh riêng qua service | Chưa có lệnh; số dư demo tạo qua maker-checker trên UI admin (hướng dẫn ở [setup §3b](development/setup.md)). Sẽ thêm lệnh khi có nạp tiền ở Phase 4 |
| Tier KYC | Có | Mọi người dùng tier 0 tới Phase 6 |

## 5. Rủi ro còn lại

- Audit vẫn ghi dưới một advisory lock toàn cục, và mọi thao tác tiền đều ghi audit, nên các transaction tiền xếp hàng tại đó. Cần đo ở Phase 12; phương án là gom audit theo lô hoặc chuỗi hash theo phân vùng.
- Dòng số dư `FEE_REVENUE`/`HOLD` sẽ là điểm nóng khi có nhiều giao dịch (đã ghi phương án tách tài khoản con ở §6).
- `GET /wallet` có tác dụng phụ (mở ví lần đầu). Chấp nhận vì idempotent; có thể chuyển sang handler outbox sau khi xác minh email nếu cần.
- Danh sách phê duyệt dùng phân trang offset; đủ cho hàng đợi nhỏ.

## 6. Bước tiếp theo

Phase 4 — Provider Simulator, Deposit & Withdrawal. Cần chủ dự án xác nhận trước khi bắt đầu.
