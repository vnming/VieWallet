# Đặc tả API (v1)

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0). Tài liệu OpenAPI sinh tự động (`/api/v1/openapi.json`) sẽ là hợp đồng chính thức khi đã cài đặt; file này giữ vai trò tổng quan thiết kế. |
| Cập nhật | 2026-11-21 |
| Liên quan | [Xử lý tài chính](../architecture/financial-processing.md), [Bảo mật](../architecture/security.md) |

## 1. Quy ước

- Đường dẫn gốc `/api/v1` (khách hàng), `/api/v1/admin` (nhân viên, cần token audience admin), `/api/v1/webhooks` (callback provider), `/api/v1/simulator-control` (chỉ dev/sim, qua proxy).
- JSON, trường `snake_case`, tên tài nguyên số nhiều, đoạn đường dẫn kebab-case.
- **Phiên bản**: số phiên bản chính trong URL. Thay đổi bổ sung (trường, endpoint mới) nằm trong v1; thay đổi phá vỡ ⇒ v2 chạy song song v1.
- **Tiền**: `{"amount": "1250000", "currency": "VND"}` — amount là chuỗi số nguyên đơn vị nhỏ nhất. Các trường hiển thị phụ (nếu có) không bao giờ dùng cho logic.
- **Thời gian**: RFC 3339 UTC (`2026-11-21T07:30:00Z`).
- **ID**: chuỗi UUID; giao dịch còn truy cập được bằng `reference`.
- **Header**: cookie phiên (`vw_session` / `vw_admin_session`), `X-VW-CSRF: 1` cho mọi request ghi, `Idempotency-Key` (bắt buộc ở endpoint đánh dấu **[I]**), `X-Request-ID` (tùy chọn; được trả lại), `Accept-Language` (vi, en).
- **Phân trang**:
  - Cursor (luồng dữ liệu: giao dịch, thông báo, tin nhắn AI): `?limit=20&cursor=<opaque>` → `{ "items": [...], "next_cursor": "..." | null }`. Keyset trên `(created_at, id)`.
  - Theo trang (bảng admin): `?page=1&page_size=50` → `{ "items": [...], "page": 1, "page_size": 50, "total": 1234 }`. `page_size ≤ 100`.
- **Lọc**: query param tường minh (`type=TRANSFER&type=PAYMENT&status=COMPLETED&from=...&to=...&min_amount=...&q=...`). **Sắp xếp**: `sort=-created_at` với các trường trong allow-list.
- **Header rate limit**: `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`; 429 kèm `Retry-After`.

## 2. Lỗi (RFC 9457 Problem Details)

```json
{
  "type": "https://docs.viewallet.local/errors/insufficient-balance",
  "title": "Insufficient balance",
  "status": 422,
  "code": "INSUFFICIENT_BALANCE",
  "detail": "Số dư khả dụng nhỏ hơn số tiền cộng phí.",
  "correlation_id": "01JD...",
  "errors": [{"field": "amount", "code": "TOO_LARGE", "message": "..."}]
}
```

`detail` và `message` được bản địa hóa theo `Accept-Language`; `code` cố định để client xử lý.

| HTTP | Dùng khi |
|---|---|
| 400 | Request sai định dạng |
| 401 | Chưa đăng nhập hoặc phiên không còn hiệu lực (`AUTH_REQUIRED`, `SESSION_EXPIRED`, `INVALID_CREDENTIALS`) |
| 403 | Không có quyền hoặc bị chặn (`FORBIDDEN`, `CSRF_FAILED`, `MFA_REQUIRED`, `MFA_STEP_UP_REQUIRED`, `EMAIL_NOT_VERIFIED`, `ACCOUNT_LOCKED`, `ACCOUNT_DISABLED`) |
| 404 | Không tồn tại **hoặc không thuộc về user** (không lộ sự tồn tại) |
| 409 | Xung đột trạng thái: `TXN_INVALID_STATE`, `IDEMPOTENCY_IN_PROGRESS`, `VERSION_CONFLICT` |
| 422 | Lỗi validate và quy tắc nghiệp vụ: `VALIDATION_ERROR`, `INSUFFICIENT_BALANCE`, `LIMIT_EXCEEDED`, `PIN_INVALID`, `PIN_LOCKED`, `PIN_NOT_SET`, `WALLET_FROZEN`, `RISK_BLOCKED`, `QUOTE_EXPIRED`, `IDEMPOTENCY_KEY_REUSED`, `KYC_REQUIRED`, `RECIPIENT_UNAVAILABLE`, `RECEIVER_LIMIT_EXCEEDED` |
| 429 | Bị giới hạn tần suất |
| 503 | Phụ thuộc không khả dụng (`PROVIDER_UNAVAILABLE`, `AI_UNAVAILABLE`) |

Mã lỗi là một enum đóng, có tài liệu, chia sẻ với frontend qua client sinh tự động.

## 3. Xác thực và định danh (đã cài đặt ở Phase 2)

Phiên đăng nhập là cookie phía server (ADR-0012). Mọi request ghi phải có `X-VW-CSRF: 1`.

| Method | Path | Ghi chú |
|---|---|---|
| POST | `/auth/register` | email, password, full_name, locale → **202** luôn giống nhau (email đã tồn tại cũng vậy); gửi mã xác minh |
| POST | `/auth/verify-email` | email, code → 204; sai ⇒ 422 `VERIFICATION_CODE_INVALID` |
| POST | `/auth/verify-email/resend` | email → 202 (rate limit theo địa chỉ và IP) |
| POST | `/auth/login` | email, password → 200 `MeResponse` + cookie `vw_session`; lỗi `INVALID_CREDENTIALS`, `EMAIL_NOT_VERIFIED`, `ACCOUNT_LOCKED` (kèm `Retry-After`), `RATE_LIMITED` |
| POST | `/auth/logout` · `/auth/logout-all` | thu hồi phiên hiện tại / mọi phiên |
| POST | `/auth/password/forgot` | email → 202 luôn giống nhau; gửi link đặt lại (30 phút) |
| POST | `/auth/password/reset` | token, new_password → 204; thu hồi mọi phiên |
| GET · PATCH | `/me` | hồ sơ, permission, trạng thái PIN · đổi full_name/display_name/locale |
| POST | `/me/password` | current_password, new_password → 204; thu hồi các phiên khác |
| POST | `/me/email` · `/me/email/confirm` | password + new_email → 202 · code → `MeResponse` |
| GET · DELETE | `/me/sessions` · `/me/sessions/{id}` | phiên đang hoạt động (đánh dấu `current`) · thu hồi một phiên (phiên của người khác ⇒ 404) |
| GET | `/me/login-history?limit=` | tối đa 50 bản ghi mới nhất |
| POST · PUT | `/me/pin` | đặt PIN lần đầu (password + pin) · đổi PIN (current_pin, new_pin) |
| POST | `/me/pin/reset/request` · `/me/pin/reset/confirm` | password → mã qua email · code + new_pin |
| POST | `/admin/auth/login` | → `{next_step: MFA_ENROLLMENT_REQUIRED | MFA_REQUIRED}` + cookie phiên `MFA_PENDING` |
| POST | `/admin/auth/mfa/enroll` · `/admin/auth/mfa/confirm` | secret + `otpauth://` URI · code → 10 mã khôi phục, phiên được nâng cấp |
| POST | `/admin/auth/mfa/verify` | code **hoặc** recovery_code → 204, phiên được nâng cấp |
| POST | `/admin/auth/step-up` | code → làm mới `mfa_verified_at` (permission nhạy cảm cần ≤ 15 phút) |
| POST · GET | `/admin/auth/logout` · `/admin/me` | — · hồ sơ nhân viên, role, permission, hạn phiên |
| GET | `/admin/audit-logs` · `/admin/audit-logs/verify` | `audit:read:any` (phân trang, lọc theo action/actor/target) · `audit:verify:any` (nhạy cảm) |

## 3a. Ví, lịch sử giao dịch, phê duyệt, đối soát (đã cài đặt ở Phase 3)

Các mục 5, 6, 13 bên dưới là thiết kế đầy đủ; bảng này là phần đã chạy được. Số tiền luôn là `{"amount": "<chuỗi số nguyên>", "currency": "VND"}`.

| Method | Path | Quyền | Ghi chú |
|---|---|---|---|
| GET | `/api/v1/wallet` | khách | Mở ví (kèm tài khoản ledger) ở lần gọi đầu, idempotent. Trả `wallet_number`, `status`, `freeze_source`, `balance` |
| POST | `/api/v1/wallet/freeze` | khách | Tự khóa ví (không cần PIN vì chỉ bớt quyền). Khóa khi đã khóa ⇒ 409 `WALLET_STATE_CONFLICT` |
| POST | `/api/v1/wallet/unfreeze` | khách | `{pin}`. Chỉ mở được khóa do chính mình đặt; khóa của nhân viên ⇒ 409 |
| GET | `/api/v1/transactions` | khách | `cursor` (id cuối trang trước, UUIDv7), `limit` ≤ 50, `type`, `status`. Mới nhất trước. Điều chỉnh đang chờ duyệt không hiển thị |
| GET | `/api/v1/transactions/{reference}` | khách | Chi tiết + `events` (timeline). Giao dịch của người khác ⇒ 404 (không lộ sự tồn tại) |
| GET | `/api/v1/admin/wallets/{wallet_number}` | `user:read:any` | Số dư, trạng thái, chủ ví |
| POST | `/api/v1/admin/wallets/{wallet_number}/freeze` | `wallet:freeze:any` | `{reason}`; có hiệu lực ngay |
| POST | `/api/v1/admin/wallets/{wallet_number}/unfreeze-requests` | `wallet:unfreeze:any` | Tạo yêu cầu `WALLET_UNFREEZE`; người thứ hai duyệt |
| GET | `/api/v1/admin/transactions/{reference}` | `transaction:read:any` | Kèm `metadata` nội bộ, `held`, event có actor và correlation id |
| POST | `/api/v1/admin/adjustments` [I] | `adjustment:propose:any` (step-up) | `{wallet_number, direction: CREDIT\|DEBIT, amount, reason_code, note, description?, ticket_ref?}` ⇒ 201 `{approval, transaction_reference}` |
| GET | `/api/v1/admin/approvals` | có ít nhất một quyền duyệt | `status` (mặc định PENDING), `page`, `page_size`. Chỉ thấy loại yêu cầu mình có quyền duyệt; `can_decide` cho biết có được quyết định không |
| GET | `/api/v1/admin/approvals/{id}` | như trên | |
| POST | `/api/v1/admin/approvals/{id}/approve` | quyền duyệt theo loại (step-up) | `{note?}`. Duyệt và thực hiện trong cùng DB transaction ⇒ `EXECUTED` hoặc `FAILED` + `failure_code` (ví dụ `INSUFFICIENT_BALANCE`). Tự duyệt ⇒ 403 `APPROVAL_SELF_DECISION` |
| POST | `/api/v1/admin/approvals/{id}/reject` | như trên | `{note}` ≥ 5 ký tự; hủy giao dịch đang chờ |
| POST | `/api/v1/admin/approvals/{id}/cancel` | chỉ maker | |
| GET/POST | `/api/v1/admin/reconciliation/runs` | `reconciliation:manage:any` | POST chạy đối soát nội bộ I1–I5 ngay và trả kết quả kèm `items` |
| GET | `/api/v1/admin/reconciliation/runs/{id}` | như trên | |

Mã lỗi mới: `WALLET_NOT_FOUND`, `WALLET_NOT_ACTIVE`, `WALLET_STATE_CONFLICT`, `TXN_NOT_FOUND`, `TXN_INVALID_STATE`, `TXN_CONCURRENT_UPDATE`, `INSUFFICIENT_BALANCE`, `ALREADY_REVERSED`, `APPROVAL_NOT_FOUND`, `APPROVAL_NOT_PENDING`, `APPROVAL_SELF_DECISION`, `APPROVAL_EXPIRED`, `APPROVAL_DUPLICATE`.

## 4. KYC

`GET /kyc` (trạng thái, tier, lịch sử) · `POST /kyc/submissions` (thông tin) · `POST /kyc/submissions/{id}/documents` (multipart, ≤ 5 MB) · `POST /kyc/submissions/{id}/submit`.
Admin: `GET /admin/kyc/submissions?status=PENDING` · `POST /admin/kyc/submissions/{id}/claim` · `POST /admin/kyc/submissions/{id}/decision` (APPROVE/REJECT, reason_code, note) · `GET /admin/kyc/documents/{id}/url` (signed URL ngắn hạn, có audit).

## 5. Ví

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/wallet` | số dư, đang giữ, trạng thái, số ví, tiền tệ |
| GET | `/wallet/balance-history` | số dư cuối ngày theo khoảng |
| GET | `/wallet/limits` | hạn mức còn lại theo loại/kỳ |
| POST | `/wallet/freeze` | tự khóa ví |
| POST | `/wallet/unfreeze` | mật khẩu + PIN (chỉ cho ví tự khóa) |
| GET | `/wallet/qr` | payload QR nhận tiền cá nhân |
| GET/POST/DELETE | `/wallet/destinations` | tài khoản ngân hàng nhận tiền rút (ngân hàng giả lập) |
| GET/POST/PATCH/DELETE | `/recipients` | người nhận đã lưu |
| GET | `/recipients/lookup?wallet_number=` / `?phone=` / `?email=` | tên đã che; có rate limit (chống dò tìm) |

## 6. Giao dịch (đọc)

`GET /transactions` (cursor, bộ lọc, q) · `GET /transactions/{ref}` (chi tiết + timeline + liên quan) · `PATCH /transactions/{ref}/category` · `POST /transactions/exports` [I] (CSV bất đồng bộ, trả về job) · `GET /exports/{id}` (trạng thái + URL tải).

## 7. Di chuyển tiền

| Method | Path | Ghi chú |
|---|---|---|
| POST | `/deposits` [I] | amount, method → giao dịch (PENDING/FAILED) + `checkout_url` |
| GET | `/deposits/{ref}` | trạng thái (polling) |
| POST | `/deposits/{ref}/cancel` | chỉ khi CREATED |
| POST | `/withdrawals` [I] | báo giá: destination_id, amount → CREATED kèm phí, tổng, expires_at |
| POST | `/withdrawals/{ref}/confirm` [I] | pin → PENDING/FAILED |
| POST | `/withdrawals/{ref}/cancel` | CREATED hoặc PENDING trước khi gửi đi |
| POST | `/transfers` [I] | báo giá: người nhận (wallet_number hoặc recipient_id), amount, note → CREATED, phí, tổng, tên người nhận đã che, cảnh báo |
| POST | `/transfers/{ref}/confirm` [I] | pin → COMPLETED/PENDING/FAILED |
| POST | `/transfers/{ref}/cancel` | chỉ khi CREATED |
| GET | `/payment-requests/{code}` | kiểm chứng QR, trả về merchant/người phát hành, số tiền, trạng thái |
| POST | `/payment-requests` [I] | tạo (merchant hoặc xin tiền giữa người dùng) |
| POST | `/payment-requests/{code}/cancel` · `/decline` | người phát hành hủy / người trả từ chối |
| POST | `/payment-requests/{code}/pay` [I] | báo giá: amount (nếu để trống) → giao dịch payment CREATED |
| POST | `/payments/{ref}/confirm` [I] | pin |
| GET | `/merchant/payments` | màn hình merchant |
| POST | `/merchant/payments/{ref}/refunds` [I] | amount, reason → COMPLETED hoặc PENDING chờ duyệt |

Response của bước xác nhận luôn trả đầy đủ giao dịch, gồm `status`, `failure_code`, `next_action` (ví dụ `NONE`, `WAIT_PROVIDER`, `WAIT_REVIEW`, `REDIRECT`).

## 8. Webhook (provider → VieWallet)

`POST /webhooks/providers/{provider_code}` — kiểm chứng chữ ký trên raw body, luôn trả 2xx nhanh sau khi đã ghi bền vào hộp nhận (xử lý bất đồng bộ); 401 khi sai chữ ký; 200 khi trùng.

## 9. Thông báo

`GET /notifications` (cursor, category, unread) · `GET /notifications/unread-count` · `POST /notifications/{id}/read` · `POST /notifications/read-all` · `GET/PUT /notifications/preferences` · `GET /notifications/push/vapid-public-key` · `GET/POST /notifications/push-subscriptions` · `DELETE /notifications/push-subscriptions/{id}`.

## 10. AI

`GET /ai/conversations` · `POST /ai/conversations` · `GET /ai/conversations/{id}/messages` · `POST /ai/conversations/{id}/messages` (stream `text/event-stream` trên POST: event `message_start`, `text_delta`, `tool_status` {name, state}, `data_card`, `draft_action`, `message_end`, `error`) · `DELETE /ai/conversations/{id}` · `GET /ai/quota`.
Admin: `GET /admin/ai/usage` · `GET/POST /admin/ai/prompts` (phiên bản; kích hoạt qua phê duyệt) · `GET/PATCH /admin/ai/models` (chọn model miễn phí: Ollama/OpenAI-compatible/fake) · `POST /admin/ai/kill-switch`.

## 11. Analytics và báo cáo

`GET /reports/monthly-summary?month=` · `GET /reports/spending-by-category?from&to` · `GET /reports/daily-spending?from&to` · `GET /reports/income?from&to` · `GET /reports/largest?from&to&limit` · `GET /reports/recurring` · `GET /reports/insights?month=` · CRUD `/budgets`, `/savings-goals`.
Admin: `GET /admin/reports/kpis?from&to&granularity` · `/admin/reports/volume` · `/admin/reports/providers` · `/admin/reports/kyc-funnel` · `/admin/reports/fees`.

## 12. Hỗ trợ và trợ giúp

`GET /help/articles?q=&locale=` · `GET /help/articles/{slug}` · `GET/POST /support/tickets` · `GET /support/tickets/{id}` · `POST /support/tickets/{id}/messages`.

## 13. Vận hành admin

| Nhóm | Endpoint |
|---|---|
| Dashboard | `GET /admin/dashboard?from&to` |
| User | `GET /admin/users` (page, q, status, kyc_tier, role) · `GET /admin/users/{id}` · `POST /admin/users/{id}/lock` · `/unlock` · `/restrict` · `POST /admin/users/{id}/pii-reveal` (lý do; có audit) · `POST /admin/users/{id}/sessions/revoke-all` |
| Ví | `GET /admin/wallets` · `GET /admin/wallets/{id}` (kèm posting ledger) · `POST /admin/wallets/{id}/freeze` (lý do) · `POST /admin/wallets/{id}/unfreeze` (tạo approval) |
| Giao dịch | `GET /admin/transactions` · `GET /admin/transactions/{ref}` (journal, provider operation, callback, rủi ro, event, audit) · `POST /admin/transactions/{ref}/inquire` (truy vấn trạng thái provider) · `POST /admin/transactions/{ref}/reversals` [I] (đề xuất) · `POST /admin/payments/{ref}/refunds` [I] (đề xuất) · `POST /admin/adjustments` [I] (đề xuất) |
| Phê duyệt | `GET /admin/approvals?status=&action_type=` · `GET /admin/approvals/{id}` · `POST /admin/approvals/{id}/approve` · `/reject` · `/cancel` (maker) |
| Vận hành | `GET /admin/operations/queues` (số lượng) · `GET /admin/operations/queues/{queue}` · `GET/POST /admin/recovery-cases` |
| Rủi ro | `GET /admin/risk/alerts` · `POST /admin/risk/alerts/{id}/resolve` · `GET /admin/risk/holds` · `POST /admin/risk/holds/{ref}/approve` · `/reject` · `GET /admin/risk/rules` · `POST /admin/risk/rules/{code}/versions` (đề xuất) · `POST/DELETE /admin/users/{id}/risk-flags` |
| Đối soát | `GET/POST /admin/reconciliation/runs` · `GET /admin/reconciliation/runs/{id}` · `GET /admin/reconciliation/items` · `POST /admin/reconciliation/items/{id}/resolve` |
| Cấu hình | `GET /admin/config/fees` · `POST /admin/config/fees` (đề xuất) · tương tự cho `/limits`, `/settings`, `/feature-flags` |
| Thông báo | `GET /admin/notifications/templates` · `POST /admin/notifications/broadcasts` (đề xuất) · `GET /admin/notifications/deliveries/stats` |
| Simulator | `GET/PUT /admin/simulator/settings` · `GET /admin/simulator/operations` · `POST /admin/simulator/operations/{key}/actions` · `GET /admin/simulator/logs` |
| Truy cập | `GET /admin/roles` · `GET /admin/permissions` · `POST /admin/users/{id}/roles` (đề xuất) · `DELETE /admin/users/{id}/roles/{role}` |
| Audit | `GET /admin/audit-logs` (lọc: người thực hiện, hành động, đối tượng, ngày) · `GET /admin/audit-logs/verify` (kiểm tra hash chain) · `GET /admin/activity` |
| Ticket | `GET /admin/tickets` · `GET/PATCH /admin/tickets/{id}` · `POST /admin/tickets/{id}/messages` |
| Hệ thống | `GET /admin/system/health` · `GET /admin/system/outbox` (dead letter) · `POST /admin/system/outbox/{id}/replay` |

## 14. Endpoint vận hành

`GET /health/live`, `GET /health/ready` (DB, Redis), `GET /metrics` (chỉ mạng nội bộ), `GET /api/v1/openapi.json` (tắt hoặc cần xác thực ở môi trường production-like).

## 15. Schema minh họa

```jsonc
// Giao dịch (góc nhìn khách hàng)
{
  "id": "0193...", "reference": "VW261121K7Q2M9XD4P",
  "type": "TRANSFER", "direction": "OUT", "status": "COMPLETED",
  "amount": {"amount": "250000", "currency": "VND"},
  "fee": {"amount": "0", "currency": "VND"},
  "total": {"amount": "250000", "currency": "VND"},
  "counterparty": {"display_name": "TRAN T** B", "wallet_number_masked": "******4821", "kind": "USER"},
  "description": "Tiền cơm",
  "category": {"code": "FOOD", "name": "Ăn uống"},
  "created_at": "2026-11-21T07:30:00Z", "completed_at": "2026-11-21T07:30:01Z",
  "timeline": [{"status": "CREATED", "at": "..."}, {"status": "COMPLETED", "at": "..."}],
  "related": [], "failure": null, "next_action": "NONE"
}
```
