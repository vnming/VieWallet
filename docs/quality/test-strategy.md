# Chiến lược kiểm thử, kế hoạch kiểm thử và test case chính

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0) |
| Cập nhật | 2026-11-21 |
| Liên quan | [Xử lý tài chính](../architecture/financial-processing.md), [Roadmap](../roadmap.md) |

## 1. Nguyên tắc

- Một tính năng chưa xong nếu chưa có test bao phủ đường lỗi, không chỉ đường thành công.
- Test tài chính chạy trên **PostgreSQL thật** (khóa dòng, constraint và trigger là một phần của thiết kế). Không thay bằng SQLite.
- Domain logic (state machine, quy tắc posting, phí, hạn mức, risk rule) được test như code thuần: nhanh và đầy đủ.
- Mọi bug fix bắt đầu bằng một regression test đang fail.
- Test tất định: đóng băng thời gian, cố định seed ngẫu nhiên, mặc định dùng provider fake (cả payment và AI).

## 2. Kim tự tháp test và công cụ

| Tầng | Phạm vi | Công cụ |
|---|---|---|
| Unit / domain | Money, posting builder, bảng state machine, tính phí, kiểm tra hạn mức, risk rule, chính sách PIN, logic token | pytest, hypothesis |
| Service / integration | Use case có DB: chuyển tiền, callback nạp tiền, vòng đời rút tiền, idempotency, phê duyệt, đối soát | pytest + pytest-asyncio + PostgreSQL (Docker: testcontainers khi chạy local, service container trong CI), fixture transaction có savepoint |
| API | Router: xác thực, permission, validate, định dạng lỗi, phân trang | httpx `AsyncClient` gọi thẳng ASGI app |
| Concurrency | Chuyển tiền song song, confirm song song, callback song song, tranh chấp hạn mức | asyncio task với **kết nối DB riêng**, barrier để bắt đầu cùng lúc |
| Property-based | Chuỗi thao tác ngẫu nhiên vẫn giữ bất biến ledger I1–I5 | hypothesis stateful testing (`RuleBasedStateMachine`) |
| Contract | Adapter simulator so với API simulator; snapshot schema OpenAPI; client TS sinh ra biên dịch được | pytest, schemathesis (fuzz API theo OpenAPI) |
| Migration | upgrade từ DB trống, vòng downgrade/upgrade, trigger tồn tại | pytest + alembic |
| Bảo mật | Ma trận AuthZ (mỗi endpoint × mỗi role), thử BOLA, rate limit, phát hiện dùng lại token, CSRF trên endpoint cookie, kiểm tra header | bảng tham số pytest; quét OWASP ZAP baseline ở Phase 12 |
| Frontend unit/component | Component, hook, định dạng tiền, form và validate | Vitest, Testing Library, MSW, axe |
| E2E | Hành trình quan trọng của user và admin | Playwright trên docker compose stack |
| AI | Vòng lặp orchestrator với provider fake; permission tool; ca injection; kiểm tra số liệu; bộ eval | pytest; eval với model local miễn phí trên máy dev |
| Hiệu năng | Mục tiêu p95 với seed data; tranh chấp tài khoản nóng | Locust hoặc k6 (Phase 12) |

## 3. Ngưỡng coverage và cổng chất lượng (CI)

- Backend: ruff (lint + kiểm tra format), mypy (strict cho `core`, `ledger`, `transactions`, `pricing`, `risk`), import-linter (ranh giới module), pytest có coverage — `ledger`, `transactions`, `pricing`, `risk` ≥ 90 % branch; toàn bộ ≥ 80 %.
- Frontend: eslint, kiểm tra prettier, `tsc --noEmit`, vitest coverage ≥ 70 % cho `packages/ui` và hook tính năng, build.
- E2E: bộ smoke ở mọi PR (≈ 5 phút); bộ đầy đủ chạy hằng đêm và trước khi release.
- Bảo mật: gitleaks, pip-audit, pnpm audit (high/critical làm fail).
- CI không gọi model AI thật; AI test dùng provider fake.

## 4. Danh mục test tài chính (ưu tiên 1)

| ID | Tình huống | Kỳ vọng |
|---|---|---|
| FT-01 | Chuyển tiền với số tiền + phí > số dư khả dụng | 422 `INSUFFICIENT_BALANCE`, không có journal, giao dịch FAILED, số dư không đổi |
| FT-02 | Cùng `Idempotency-Key` + cùng body gửi lại 5 lần (tuần tự) | Một giao dịch, một journal; 4 lần gửi lại trả body giống hệt kèm `Idempotent-Replayed: true` |
| FT-03 | Cùng key, khác số tiền | 422 `IDEMPOTENCY_KEY_REUSED` |
| FT-04 | Cùng key gửi đồng thời 10 lần | Đúng một lần được xử lý; các lần khác nhận 409 in-progress hoặc replay; một journal |
| FT-05 | 50 lệnh chuyển đồng thời từ ví chỉ đủ tiền cho 20 lệnh | Đúng 20 thành công, 30 thất bại do không đủ số dư; số dư cuối = ban đầu − 20×số tiền; không bao giờ âm |
| FT-06 | 100 cặp chuyển đồng thời A→B và B→A | Lỗi deadlock không lộ ra user (retry hấp thụ); tổng tiền được bảo toàn |
| FT-07 | Confirm cùng một lệnh chuyển hai lần đồng thời | Một COMPLETED; lần còn lại nhận 409 `TXN_INVALID_STATE` hoặc replay |
| FT-08 | Confirm sau khi báo giá hết hạn | 422 `QUOTE_EXPIRED`, giao dịch EXPIRED |
| FT-09 | Ví người gửi bị khóa giữa lúc báo giá và lúc confirm | 422 `WALLET_FROZEN`, không có journal |
| FT-10 | Ví người nhận bị khóa | Chuyển FAILED `RECIPIENT_UNAVAILABLE` |
| FT-11 | Sai PIN 5 lần rồi nhập đúng | Lần sai thứ 5 ⇒ PIN_LOCKED; PIN đúng bị từ chối tới khi hết khóa |
| FT-12 | Hạn mức ngày: các lệnh chuyển đồng thời cộng lại vượt hạn mức | Tổng đã hoàn tất ≤ hạn mức |
| FT-13 | Callback nạp tiền bị gửi trùng 3 lần + đồng thời | Ghi có một lần; các callback trùng được ghi nhận là duplicate |
| FT-14 | Callback sai chữ ký | 401, không đổi trạng thái, lưu dạng REJECTED |
| FT-15 | Số tiền trong callback ≠ số tiền operation | Không áp dụng; tạo mục đối soát/hồ sơ thu hồi |
| FT-16 | Callback sai thứ tự (failed đến sau succeeded) | Callback thứ hai bị bỏ qua vì cũ; COMPLETED giữ nguyên |
| FT-17 | Payout timeout ⇒ UNKNOWN ⇒ truy vấn báo thành công | COMPLETED đúng một lần; simulator không có payout thứ hai |
| FT-18 | Payout timeout ⇒ truy vấn báo thất bại | FAILED, hoàn khoản giữ, số dư khôi phục chính xác (số tiền + phí) |
| FT-19 | Sập giữa lúc commit giữ tiền và lúc gửi đi (mô phỏng) | Job phục hồi gửi đúng một lần; simulator xử lý idempotent |
| FT-20 | Hoàn một phần 3 lần, lần thứ ba vượt số tiền gốc | Lần thứ ba bị từ chối; tổng ≤ gốc; chuyển PARTIALLY_REFUNDED→REFUNDED đúng |
| FT-21 | Các hoàn tiền đồng thời cộng lại vượt gốc | Tổng hoàn tối đa bằng số tiền gốc |
| FT-22 | Đảo lệnh chuyển khi người nhận đã tiêu tiền | Từ chối đảo; mở hồ sơ thu hồi; không có số dư âm |
| FT-23 | Đảo hai lần | Lần thứ hai bị từ chối (`UNIQUE reverses_journal_id`, state machine) |
| FT-24 | Mọi bước chuyển trạng thái bị cấm (sinh từ bảng) | `InvalidStateTransition` |
| FT-25 | UPDATE/DELETE trực tiếp trên postings/journals/audit_logs bằng role ứng dụng | DB báo lỗi |
| FT-26 | Insert journal không cân bằng (bỏ qua domain) | Deferred trigger từ chối khi commit |
| FT-27 | Property test: 10.000 thao tác ngẫu nhiên (nạp, chuyển, thanh toán, hoàn tiền, đảo, rút thành công/thất bại) | I1–I5 đúng sau mọi bước |
| FT-28 | Đối soát với sao kê có chèn chênh lệch | Tạo đúng loại mục; dòng khớp không bị ảnh hưởng |
| FT-29 | Làm tròn phí: phí bps ở số tiền biên | Khớp công thức số nguyên đã ghi; kẹp min/max đúng |
| FT-30 | Maker tự duyệt yêu cầu của mình | Bị từ chối (service và DB CHECK) |
| FT-31 | Trần số dư người nhận | Chuyển FAILED `RECEIVER_LIMIT_EXCEEDED` |
| FT-32 | Payment request dùng một lần bị hai user trả đồng thời | Đúng một thanh toán PAID |

## 5. Danh mục test bảo mật (ưu tiên 1)

Dò email qua đăng nhập (cùng response/thời gian), khóa và rate limit, xoay vòng refresh token và dùng lại ⇒ thu hồi cả họ token, đăng xuất mọi phiên làm access token mất hiệu lực trong TTL cache, token admin trên route khách và ngược lại ⇒ 401/403, mọi endpoint admin × mọi role (ma trận sinh tự động), BOLA trên giao dịch/ticket/hội thoại/thông báo, step-up MFA cho permission nhạy cảm, token đặt lại mật khẩu dùng một lần + hết hạn, header/origin CSRF trên endpoint cookie, giả mạo kiểu/kích thước file upload, payload XSS trong mô tả/ticket hiển thị đã escape, ký tự đặc biệt SQL trong tìm kiếm, AI: chuỗi injection trong mô tả giao dịch không kích hoạt thêm tool hay làm lộ system prompt, AI không chạm được dữ liệu của user khác.

## 6. Hành trình E2E (Playwright)

1. Đăng ký → xác minh email (API Mailpit) → đặt PIN → trang chủ.
2. Nộp KYC → reviewer admin duyệt → user lên tier 1.
3. Nạp tiền thành công qua trang SimBank; kịch bản nạp thất bại; đang chờ → hoàn tất sau.
4. Chuyển tiền cho user khác; người nhận thấy thông báo; lịch sử và chi tiết đúng.
5. Chuyển tiền bị chặn do ví bị khóa; do hạn mức; khóa PIN do sai.
6. Thanh toán QR cho merchant → hoàn một phần → khách thấy tiền hoàn.
7. Rút tiền thành công; thất bại ⇒ tiền được hoàn; timeout ⇒ truy vấn giải quyết.
8. Giữ tiền do review rủi ro → analyst duyệt.
9. Điều chỉnh maker-checker của admin.
10. Chạy đối soát có chênh lệch → xử lý.
11. Trợ lý AI (provider fake) trả lời chi tiêu tháng kèm data card.
12. Luồng đăng ký push (Chromium đã cấp quyền) và điều hướng khi bấm thông báo.
13. Đăng xuất xóa dữ liệu; user thứ hai trên cùng trình duyệt không thấy gì của user đầu.

## 7. Dữ liệu test

Factory (`polyfactory`/tự viết) tạo user, ví và giao dịch **qua service** để bất biến được giữ. Seed demo dùng lại factory. Không có dữ liệu cá nhân thật; tên tiếng Việt sinh từ danh sách hư cấu.

## 8. Tiêu chí nghiệm thu phase (Definition of Done cho mỗi module)

Theo master prompt §20: yêu cầu đã viết, logic, migration, API, validate, phân quyền, trạng thái UI (loading/empty/error/success), test xanh (unit/integration/API/E2E tùy trường hợp), lint, type check, build, tài liệu đã cập nhật, không còn lỗi critical, không có tính năng bắt buộc nào chỉ là placeholder.
