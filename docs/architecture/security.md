# Kiến trúc bảo mật, threat model, rủi ro và KYC

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0) |
| Cập nhật | 2026-11-21 |
| Tiêu chuẩn tham chiếu | OWASP ASVS L2, OWASP Top 10, OWASP API Security Top 10 (2023) |
| Liên quan | [ADR-0008 Auth](../adr/0008-authentication-tokens-sessions.md), [Trợ lý AI §7](ai-assistant.md#7-bảo-mật-ai-và-guardrails) |

## 1. Tổng quan phòng thủ nhiều lớp

| Lớp | Biện pháp |
|---|---|
| Biên | TLS (production-like), HSTS, CORS allow-list chặt theo origin của từng app, security header, giới hạn kích thước request |
| API | AuthN trên mọi route không công khai, dependency kiểm tra permission RBAC, Pydantic strict validation, rate limit, idempotency, Problem Details không lộ chi tiết nội bộ |
| Nghiệp vụ | PIN cho thao tác trừ tiền, state machine, hạn mức, risk engine, maker-checker cho thao tác tài chính của nhân viên |
| Dữ liệu | Chỉ SQL có tham số qua SQLAlchemy, chốt chặn DB (CHECK/UNIQUE/trigger), DB role quyền tối thiểu, mã hóa cột nhạy cảm, che PII |
| Giám sát | Audit log (hash chain), lịch sử đăng nhập, cảnh báo bảo mật, cảnh báo rủi ro, log có cấu trúc kèm correlation id |

## 2. Xác thực

### 2.1 Thông tin đăng nhập

- Mật khẩu: Argon2id (`argon2-cffi`), tham số m=64 MiB, t=3, p=4 (chỉnh để mất khoảng 100–250 ms trên máy mục tiêu); hash lại khi đăng nhập nếu tham số thay đổi.
- Chính sách mật khẩu: tối thiểu 10, tối đa 128 ký tự, kiểm tra với danh sách 100k mật khẩu bị lộ phổ biến lưu local (không gọi dịch vụ ngoài), không ép quy tắc tổ hợp ký tự (theo NIST SP 800-63B).
- Không lộ email tồn tại: email không tồn tại và sai mật khẩu nhận cùng response với thời gian tương đương (verify một hash giả).

### 2.2 Token và session (ADR-0008)

- **Access token**: JWT, EdDSA (Ed25519) hoặc HS256 có key id xoay vòng; sống 10 phút; claim `sub, sid, aud (web|admin), roles_version, iat, exp, jti`. Chỉ giữ **trong bộ nhớ** của SPA.
- **Refresh token**: 256 bit ngẫu nhiên, lưu SHA-256 hash trong `sessions`; gửi qua cookie `vw_rt` — `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. Thời hạn: web 30 ngày trượt (nhàn rỗi 7 ngày), admin 8 giờ tuyệt đối (nhàn rỗi 30 phút).
- **Xoay vòng có phát hiện dùng lại**: mỗi lần refresh cấp token mới và đánh dấu token cũ đã dùng; dùng lại token đã dùng sẽ thu hồi toàn bộ họ token và phát cảnh báo bảo mật.
- **Kiểm tra session mỗi request**: `sid` được kiểm tra với `sessions.revoked_at` (cache Redis ≤ 30 giây). Nhờ đó "đăng xuất mọi phiên" và thu hồi của nhân viên có hiệu lực trong vài giây, trong khi access token vẫn ngắn.
- Tách audience: token admin bị route khách hàng từ chối và ngược lại. Tài khoản nhân viên không dùng được app khách hàng.

### 2.3 Brute-force và lạm dụng

- Đăng nhập: 5 lần / 15 phút theo tài khoản+IP, 20 / 15 phút theo IP, 100 / giờ theo tài khoản; vượt ⇒ 429 kèm `Retry-After`.
- Khóa tài khoản: sai 10 lần liên tiếp ⇒ `LOCKED` 15 phút, gấp đôi khi lặp lại (tối đa 24 giờ); gửi email thông báo.
- Đăng ký, đặt lại mật khẩu, gửi lại mã xác minh: rate limit theo IP và theo email; response đặt lại mật khẩu giống nhau dù email có tồn tại hay không.
- Proof-of-work/captcha ngoài phạm vi (đã ghi lại).

### 2.4 MFA cho nhân viên

- TOTP (RFC 6238, `pyotp`), bắt buộc đăng ký ở lần đăng nhập admin đầu tiên, 10 mã khôi phục (lưu hash).
- Permission nhạy cảm (`is_sensitive`) yêu cầu đã xác thực MFA trong 15 phút gần nhất ("step-up").

### 2.5 Đặt lại mật khẩu và khôi phục tài khoản

- Token dùng một lần, sống 30 phút, lưu hash; khi thành công: thu hồi mọi session, gửi email thông báo, chặn rút tiền 24 giờ (thời gian chờ rủi ro).
- Đổi email: xác nhận qua địa chỉ mới + thông báo tới địa chỉ cũ kèm link "không phải tôi" để khóa tài khoản.

## 3. Bảo mật giao dịch

- **PIN**: 6 số; lưu dạng `Argon2id(HMAC-SHA256(PIN_PEPPER, pin))`. Pepper chỉ nằm trong môi trường ứng dụng (không có trong DB), nên chỉ có bản dump DB thì không thể brute force offline không gian 10⁶ giá trị.
- Số lần thử: sai 5 lần ⇒ khóa 30 phút; lần khóa thứ 3 trong 24 giờ ⇒ khóa tới khi đặt lại bằng mật khẩu + mã email; mỗi lần khóa ⇒ thông báo bảo mật.
- PIN được kiểm tra ngay trong request xác nhận; không bao giờ lưu phía client, không bao giờ ghi log (che các khóa `pin`, `password`, `token`, `secret` trong log).
- Xác nhận thao tác nhạy cảm: đổi PIN, thêm tài khoản nhận tiền rút, mở lại ví tự khóa, đổi email ⇒ nhập lại mật khẩu (và PIN khi phù hợp).
- Chống phát lại: idempotency key + chuyển trạng thái bằng CAS + báo giá hết hạn (10 phút) + dung sai timestamp của callback.
- Tài khoản nhận tiền rút mới thêm chỉ dùng được sau 1 giờ (risk rule), có thông báo khi thêm.

## 4. Phân quyền (RBAC)

- Mã permission `resource:action:scope`, scope ∈ {`own`, `any`}. Ma trận ví dụ (seed, điều chỉnh được):

| Permission | Cust | Supp | Ops | Fin | Risk | KYC | Admin | Super |
|---|---|---|---|---|---|---|---|---|
| `wallet:read:own`, `wallet:transact:own` | ✓ | | | | | | | |
| `user:read:any` (đã che) | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `user:pii_reveal:any` | | | | | ✓ | ✓ | ✓ | ✓ |
| `transaction:read:any` | | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ |
| `transaction:inquire:any` (provider) | | | ✓ | ✓ | | | ✓ | ✓ |
| `refund:propose:any`, `reversal:propose:any` | | | ✓ | ✓ | | | ✓ | ✓ |
| `refund:approve:any`, `reversal:approve:any` | | | | ✓ | | | ✓ | ✓ |
| `adjustment:propose:any` / `adjustment:approve:any` | | | | ✓ / ✓ | | | | ✓ |
| `wallet:freeze:any` | | | | | ✓ | | ✓ | ✓ |
| `wallet:unfreeze:any` (qua phê duyệt) | | | | | ✓ | | ✓ | ✓ |
| `risk_alert:manage:any`, `risk_hold:decide:any` | | | | | ✓ | | | ✓ |
| `kyc:review:any` | | | | | | ✓ | | ✓ |
| `ticket:manage:any` | | ✓ | ✓ | | | | ✓ | ✓ |
| `reconciliation:manage:any` | | | | ✓ | | | | ✓ |
| `config:propose:any` / `config:approve:any` | | | | | | | ✓ / | ✓ |
| `role:assign:any` | | | | | | | (role không phải admin) | ✓ |
| `audit:read:any` | | | | ✓ | ✓ | | ✓ | ✓ |
| `simulator:manage:any` | | | ✓ | | | | ✓ | ✓ |
| `ai_admin:manage:any` | | | | | | | ✓ | ✓ |

- Thực thi: FastAPI dependency `require(permission)`; kiểm tra mức đối tượng trong service (scope `own` so chủ sở hữu tài nguyên với principal) — chống BOLA (API1:2023).
- Response schema tách riêng theo đối tượng (không dump thẳng ORM ra JSON) — chống lộ dữ liệu quá mức / BOPLA (API3:2023).
- Maker-checker: phê duyệt thực thi **payload đã lưu** (đã kiểm hash), không phải body request mới; checker phải có permission duyệt và khác maker; phê duyệt hết hạn sau 72 giờ.
- Thao tác của Super Admin vẫn bị audit và vẫn cần người duyệt thứ hai với thao tác tài chính (seed demo có hai Super Admin).

## 5. Bảo vệ dữ liệu

- Cột mã hóa (AES-256-GCM, khóa từ môi trường, tiền tố key id để xoay khóa): MFA secret, số tài khoản ngân hàng, số giấy tờ KYC. Cột hash để tra cứu (HMAC với khóa riêng).
- Mặc định che trong UI nhân viên: email `ng***@gmail.com`, SĐT `*******123`, tài khoản ngân hàng `****6789`; xem đầy đủ = permission + lý do + audit.
- File KYC lưu ngoài DB (volume local khi dev, S3-compatible sau này) với khóa ngẫu nhiên, chỉ phục vụ qua signed URL ngắn hạn có kiểm quyền; kiểm tra content-type và kích thước (JPEG/PNG/PDF ≤ 5 MB), xóa EXIF, không bao giờ thực thi.
- Log: không có PII ngoài user id đã hash; processor che; không log body của endpoint tiền.
- Tối thiểu dữ liệu trong prompt AI (xem tài liệu AI). Với gói free cloud, chỉ gửi dữ liệu giả lập và đã che (xem §7, T18).
- Đăng xuất xóa cache phía client (xem tài liệu Thông báo & PWA).

## 6. Bảo mật web

- XSS: React tự escape; không dùng `dangerouslySetInnerHTML` trừ Markdown help center đã làm sạch (DOMPurify); CSP chặt: `default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; connect-src 'self' <api>`.
- CSRF: access token trong header `Authorization` (trình duyệt không tự gửi). Endpoint xác thực bằng cookie (`/auth/refresh`, `/auth/logout`) yêu cầu `SameSite=Strict` + kiểm tra `Origin` theo allow-list + header tùy chỉnh `X-VW-CSRF: 1`.
- SQL injection: chỉ ORM/query có tham số; sắp xếp động qua map cột cho phép; lint rule chặn `text()` thô ghép f-string.
- Header: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (camera chỉ ở trang quét QR), `Cross-Origin-Opener-Policy: same-origin`.
- Input: Pydantic strict type, giới hạn độ dài ở mọi nơi, chuẩn hóa Unicode (NFC) cho tên, bỏ ký tự điều khiển trong mô tả.
- SSRF: URL gọi ra ngoài phía server chỉ là URL cấu hình sẵn (provider, LLM, push endpoint). Push endpoint phải là HTTPS tới host push service đã biết.
- Bảo mật dependency: `pip-audit`, `pnpm audit`, Dependabot, lockfile cố định phiên bản.
- Bí mật: biến môi trường / Docker secrets; git chỉ có `.env.example`; CI quét bí mật (gitleaks).

## 7. Threat model (STRIDE, tài sản chính)

Tài sản: số dư/ledger, thông tin đăng nhập/session/PIN, PII/dữ liệu KYC, đặc quyền nhân viên, audit log, bí mật provider, tool AI.

| # | Mối đe dọa | STRIDE | Đường tấn công | Biện pháp | Rủi ro còn lại |
|---|---|---|---|---|---|
| T1 | Chi tiêu hai lần qua request đồng thời | T | Confirm song song / script | Khóa dòng có thứ tự, CHECK ≥ 0, idempotency, CAS | Thấp |
| T2 | Phát lại confirm hoặc callback | S/T | Bắt được request | Idempotency, CAS, chữ ký + timestamp + event_id unique cho callback | Thấp |
| T3 | Giả mạo callback provider để ghi có ví | S | Kẻ tấn công POST vào callback URL | Chữ ký HMAC, so sánh constant-time, số tiền/mã phải khớp operation, allow-list IP provider ở prod | Thấp |
| T4 | Chiếm tài khoản bằng credential stuffing | S | Mật khẩu bị lộ | Rate limit, khóa, kiểm tra mật khẩu bị lộ, thông báo thiết bị mới, PIN cho trừ tiền | Trung bình (khách không có 2FA SMS) |
| T5 | Đánh cắp session (XSS) | I/S | Script độc | CSP, React escape, access token trong bộ nhớ, refresh cookie HttpOnly, TTL ngắn | Thấp |
| T6 | BOLA: đọc giao dịch của người khác | I | Đổi id | Kiểm tra chủ sở hữu trong service, id UUID, test cho từng endpoint | Thấp |
| T7 | Nhân viên lạm quyền (tự điều chỉnh ví mình) | E/T | Đặc quyền admin | Nhân viên không sở hữu ví khách; maker-checker; audit hash chain; tách nhiệm vụ | Thấp |
| T8 | Sửa audit log | T/R | Truy cập DB | Trigger chỉ-thêm, không cấp quyền, job kiểm tra hash chain | Thấp (superuser DB ngoài phạm vi) |
| T9 | Brute force PIN | S | Confirm lặp lại | Bộ đếm + khóa, rate limit, pepper chống offline | Thấp |
| T10 | Vượt hạn mức bằng request song song | T | Đồng thời | Kiểm tra hạn mức khi giữ khóa ví | Thấp |
| T11 | Prompt injection qua mô tả giao dịch | T/I | Người gửi viết chỉ thị vào mô tả | Tool chỉ-đọc, ngữ cảnh giới hạn theo user, đánh dấu dữ liệu, không có tool ghi, kiểm tra output | Thấp |
| T12 | AI làm lộ dữ liệu giữa các user | I | Lạm dụng tool | `user_id` chỉ lấy từ ngữ cảnh xác thực, gắn tool theo từng request, có test | Thấp |
| T13 | Upload file độc (KYC) | T/E | File polyglot | Nhận dạng kiểu file, giới hạn kích thước, mã hóa lại ảnh, lưu trữ cô lập, không thực thi | Thấp |
| T14 | DoS endpoint tốn tài nguyên (AI, export) | D | Gửi dồn dập | Quota theo user, rate limit, export chạy bất đồng bộ | Trung bình |
| T15 | Dữ liệu còn lại trên thiết bị dùng chung sau đăng xuất | I | Cache PWA | Service worker không cache API, xóa query cache, hủy đăng ký push khi đăng xuất | Thấp |
| T16 | QR lừa đảo (merchant giả) | S | QR in sẵn | Payload QR có chữ ký, tên merchant xác minh từ server, cảnh báo nếu bên phát hành chưa xác minh | Trung bình |
| T17 | Lộ bí mật trong repo | I | Commit | gitleaks trong CI, `.env` bị ignore, chỉ có file mẫu | Thấp |
| T18 | Dữ liệu gửi tới AI free cloud bị bên thứ ba lưu/dùng huấn luyện | I | Điều khoản gói miễn phí | Mặc định dùng Ollama local; free cloud chỉ bật khi chủ dự án chọn; chỉ gửi dữ liệu giả lập, đã che | Thấp (dữ liệu là giả lập) |

## 8. Module quản lý rủi ro

### 8.1 Engine

- `risk.evaluate(ctx) → RiskResult(score, decision, matched_rules)`. Hàm thuần trên ngữ cảnh lắp ráp ngay trong transaction tiền: hồ sơ user, tier KYC, cờ tài khoản, thống kê giao dịch gần đây (đếm/tổng theo cửa sổ thời gian, từ query có index), lịch sử với người nhận, sự kiện bảo mật gần đây.
- Điểm = Σ điểm các rule khớp (trần 100). Quyết định: có rule `action_hint=BLOCK` ⇒ BLOCK; điểm ≥ 70 ⇒ REVIEW; còn lại ALLOW. Ngưỡng là cấu hình.
- Mỗi lần đánh giá lưu vào `risk_assessments` (chỉ-thêm) kèm phiên bản rule và bằng chứng ⇒ giải thích được cho analyst và AI (AI chỉ đọc phần giải thích an toàn cho user).

### 8.2 Rule ban đầu

| Mã | Điều kiện (mặc định) | Tác động |
|---|---|---|
| `VELOCITY_COUNT` | > 10 giao dịch trừ tiền trong 10 phút | +40 |
| `VELOCITY_AMOUNT` | Σ chi trong 1 giờ > 50 % hạn mức ngày | +30 |
| `AMOUNT_SPIKE` | số tiền > 5 × trung vị chi 90 ngày của user và > 2.000.000 ₫ | +30 |
| `NEW_RECIPIENT_LARGE` | chuyển lần đầu cho người nhận và số tiền > 5.000.000 ₫ | +30 |
| `CREDENTIAL_CHANGE_COOLDOWN` | đổi mật khẩu/PIN/email < 24 giờ và số tiền > 2.000.000 ₫ | +50 (rút tiền: BLOCK) |
| `NEW_DESTINATION_COOLDOWN` | tài khoản nhận tiền rút được thêm < 1 giờ | BLOCK |
| `REPEATED_FAILURES` | ≥ 3 giao dịch thất bại/bị chặn trong 30 phút | +30 |
| `DUPLICATE_PATTERN` | cùng số tiền + cùng người nhận trong 2 phút (idempotency key khác nhau) | +40 (user thấy cảnh báo "có thể trùng" khi báo giá) |
| `ACCOUNT_FLAG_WATCHLIST` | user bị gắn cờ | +50 |
| `NEW_ACCOUNT_LARGE` | tài khoản < 7 ngày tuổi và số tiền > 5.000.000 ₫ | +30 |
| `NIGHT_LARGE` | 00:00–05:00 giờ địa phương và số tiền > 10.000.000 ₫ | +20 |

### 8.3 Giám sát (bất đồng bộ)

Handler sau giao dịch tạo `risk_alerts` cho mẫu hành vi không cần chặn đồng bộ (dồn tiền: nhiều người gửi vào một ví trong thời gian ngắn; chuyển vòng tiền; khóa PIN lặp lại). Analyst xử lý hàng đợi cảnh báo; mọi thao tác bị audit.

## 9. KYC mô phỏng

- Module riêng; port provider `KycVerifier` (cài đặt mô phỏng; eKYC thật sau này).
- Luồng: user điền dữ liệu hư cấu + tải ảnh giả lập ⇒ PENDING ⇒ reviewer nhận ⇒ IN_REVIEW ⇒ APPROVED (tier 1, hết hạn sau 2 năm) hoặc REJECTED (mã lý do: BLURRY_IMAGE, MISMATCH, EXPIRED_DOCUMENT, SUSPECTED_FRAUD, OTHER) ⇒ user có thể nộp lại (tối đa 5 lần mỗi 30 ngày).
- Kiểm tra tự động mô phỏng (tùy chọn) trả về "độ tin cậy" và quyết định gợi ý cho reviewer; quyết định của người vẫn là quyết định cuối.
- UI và tài liệu ghi rõ "Xác minh mô phỏng – không tải lên giấy tờ tùy thân thật". Seed có ảnh giấy tờ mẫu hư cấu.
- Audit: mọi đổi trạng thái, mọi lần nhân viên mở file.
